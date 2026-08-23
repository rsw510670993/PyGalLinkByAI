"""Unit tests for tool/ai_matcher.py (no real aigc2d API calls)."""
import unittest

from tool.ai_matcher import (
    Aigc2dError,
    build_match_prompt,
    fallback_rule_match,
    filter_candidates_by_keyword_rules,
    judge_nyaa_match,
    parse_match_response,
)
from tool.models import GetchuGame, MatchResult, NyaaData


def make_game():
    return GetchuGame("2026-08", "サンプルゲーム 初回限定版", "tone work's")


def make_candidates():
    return [
        NyaaData("2026-08-01 12:00", "1.2 GiB", "サンプルゲーム 通常版 [202608]", "magnet:?one"),
        NyaaData("2026-08-02 12:00", "1.1 GiB", "[girlcelly] SampleGame [202608]", "magnet:?two"),
        NyaaData("2026-09-01 12:00", "1.0 GiB", "別ゲーム [202609]", "magnet:?three"),
    ]


class FakeClient:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.content


class ParseMatchResponseTest(unittest.TestCase):
    def test_plain_json(self):
        data = parse_match_response(
            '{"matched_index": 1, "confidence": 0.92, "reason": "looks good", "matched_name": "x"}'
        )
        self.assertEqual(data["matched_index"], 1)
        self.assertEqual(data["confidence"], 0.92)
        self.assertEqual(data["matched_name"], "x")

    def test_markdown_fenced_json(self):
        data = parse_match_response(
            '```json\n{"matched_index": -1, "confidence": 0.0, "reason": "none", "matched_name": ""}\n```'
        )
        self.assertEqual(data["matched_index"], -1)
        self.assertEqual(data["confidence"], 0.0)

    def test_confidence_clamped(self):
        data = parse_match_response(
            '{"matched_index": 0, "confidence": 5.0, "reason": "", "matched_name": "x"}'
        )
        self.assertEqual(data["confidence"], 1.0)

    def test_verdict_and_keywords(self):
        data = parse_match_response(
            '{"matched_index": -1, "verdict": "discarded", "confidence": 0.8, '
            '"reason": "廉価版", "matched_name": "", '
            '"keywords": [{"keyword": "廉価版", "rule_type": "discard"}]}'
        )
        self.assertEqual(data["verdict"], "discarded")
        self.assertEqual(data["keywords"], [{
            "keyword": "廉価版",
            "rule_type": "discard",
            "confidence": 0.5,
        }])

    def test_invalid_index_raises(self):
        with self.assertRaises(Exception):
            parse_match_response('{"confidence": 0.5}')


class BuildPromptTest(unittest.TestCase):
    def test_prompt_contains_fields(self):
        system, user = build_match_prompt(make_game(), make_candidates())
        self.assertIn("Getchu 信息", user)
        self.assertIn("2026-08", user)
        self.assertIn("サンプルゲーム 初回限定版", user)
        self.assertIn("tone work's", user)
        self.assertIn("[0]", user)
        self.assertIn("[1]", user)
        self.assertIn("matched_index", user)
        self.assertTrue(system.strip())
        self.assertIn("发布来源偏好", system)
        self.assertIn("girlcelly", system)
        self.assertIn("2D.G.F.", system)


class FallbackRuleMatchTest(unittest.TestCase):
    def test_yyy_prefers_girlcelly(self):
        result = fallback_rule_match(make_game(), make_candidates())
        self.assertTrue(result.has_match())
        self.assertEqual(result.selected_index, 1)
        self.assertEqual(result.source, "rule")
        self.assertIn("girlcelly", result.reason)

    def test_no_yymm_clears_link(self):
        candidates = [
            NyaaData("2026-09-01 12:00", "1.0 GiB", "別ゲーム [202609]", "magnet:?other")
        ]
        result = fallback_rule_match(make_game(), candidates)
        self.assertTrue(result.has_match())
        self.assertEqual(result.selected_index, 0)
        self.assertIsNone(result.link)

    def test_empty_candidates(self):
        result = fallback_rule_match(make_game(), [])
        self.assertFalse(result.has_match())
        self.assertEqual(result.source, "none")


class JudgeNyaaMatchTest(unittest.TestCase):
    def test_single_candidate_skips_ai(self):
        candidates = [
            NyaaData("2026-08-01 12:00", "1.2 GiB", "サンプルゲーム [202608]", "magnet:?one")
        ]
        client = FakeClient(content='{"matched_index": 0}')
        result = judge_nyaa_match(make_game(), candidates, client=client)
        self.assertEqual(result.source, "rule")
        self.assertEqual(len(client.calls), 0)

    def test_strong_rule_skips_ai(self):
        candidates = [
            NyaaData("2026-08-01 12:00", "1.2 GiB", "サンプルゲーム 初回限定版 [202608]", "magnet:?one"),
            NyaaData("2026-08-02 12:00", "1.1 GiB", "別ゲーム [202608]", "magnet:?two"),
        ]
        client = FakeClient(content='{"matched_index": 1}')
        result = judge_nyaa_match(make_game(), candidates, client=client)
        self.assertEqual(result.source, "rule")
        self.assertEqual(result.selected_index, 0)
        self.assertEqual(len(client.calls), 0)

    def test_ai_success(self):
        client = FakeClient(
            content=(
                '{"matched_index": 1, "confidence": 0.9, '
                '"reason": "girlcelly and title match", "matched_name": "[girlcelly] SampleGame [202608]"}'
            )
        )
        result = judge_nyaa_match(make_game(), make_candidates(), client=client)
        self.assertEqual(result.source, "ai")
        self.assertEqual(result.selected_index, 1)
        self.assertEqual(result.confidence, 0.9)
        self.assertEqual(result.link, "magnet:?two")
        self.assertEqual(len(client.calls), 1)

    def test_ai_failure_falls_back_to_rule(self):
        client = FakeClient(error=Aigc2dError("boom"))
        result = judge_nyaa_match(make_game(), make_candidates(), client=client)
        self.assertEqual(result.source, "rule")
        self.assertTrue(result.has_match())

    def test_empty_original_candidates_returns_unmatched(self):
        result = judge_nyaa_match(make_game(), [], client=FakeClient())
        self.assertEqual(result.verdict, "unmatched")
        self.assertEqual(result.source, "none")

    def test_keyword_filter_discards_candidates(self):
        candidates = [
            NyaaData("2026-08-01 12:00", "1.2 GiB", "サンプルゲーム 廉価版 [202608]", "magnet:?one"),
            NyaaData("2026-08-02 12:00", "1.1 GiB", "[girlcelly] SampleGame [202608]", "magnet:?two"),
        ]
        rules = [{"keyword": "廉価版", "rule_type": "discard", "confidence": 0.9}]
        filtered = filter_candidates_by_keyword_rules(candidates, rules)
        self.assertEqual(len(filtered), 1)
        self.assertIn("girlcelly", filtered[0].name)

        client = FakeClient(content='{"matched_index": 0, "verdict": "matched"}')
        result = judge_nyaa_match(make_game(), candidates, client=client, keyword_rules=rules)
        self.assertEqual(result.verdict, "matched")
        self.assertEqual(result.selected_index, 0)
        self.assertEqual(len(client.calls), 0)

    def test_protected_publishers_are_not_filtered_out(self):
        candidates = [
            NyaaData("2026-08-01 12:00", "1.2 GiB", "[girlcelly] SampleGame [202608]", "magnet:?one"),
            NyaaData("2026-08-02 12:00", "1.1 GiB", "│2D.G.F.│ SampleGame [202608]", "magnet:?two"),
        ]
        bad_rules = [
            {"keyword": "girlcelly", "rule_type": "duplicate"},
            {"keyword": "2D.G.F.", "rule_type": "discard"},
        ]
        filtered = filter_candidates_by_keyword_rules(candidates, bad_rules)
        self.assertEqual(len(filtered), 2)


if __name__ == "__main__":
    unittest.main()
