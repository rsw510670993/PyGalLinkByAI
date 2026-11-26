"""三档分流（auto/review/reject）规则测试（2026-11-26）。

取代「单阈值 40 一律交人工」的旧 gate：回测显示人工判定几乎全集中在 20-40 分挡，
该挡人工通过率约 80%，因此把公司与发售日双证据齐备的灰区候选升为 auto。
"""
import unittest

from tool import egs_match

GAME = {
    'name': 'しゅぷれ～むキャンディ ～王道には王道たる理由があるんです!～',
    'company': '枕',
    'date': '2008-09',
}


def score(title, date=None, game=None):
    return egs_match.score_candidate(game or GAME,
                                     {'nyaa_title': title, 'nyaa_date': date})


def tier_of(title, date=None, game=None):
    s, d = score(title, date=date, game=game)
    return s, egs_match.classify_candidate({'score': s, 'score_detail': d}), d


class TierTests(unittest.TestCase):
    def test_full_evidence_is_auto(self):
        s, tier, d = tier_of('[080926] [枕] しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～')
        self.assertGreaterEqual(s, egs_match.THRESHOLD)
        self.assertEqual(tier, egs_match.TIER_AUTO)
        # 名称完全命中 + 日期接近，证据已足够自动采用。
        self.assertIn('name_exact', d)

    def test_title_only_grey_zone_is_review(self):
        # 标题完整包含但既无公司也无日期：30 分，进复核队列。
        _, tier, d = tier_of('しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～')
        self.assertNotIn('company', d)
        self.assertEqual(tier, egs_match.TIER_REVIEW)

    def test_company_and_date_lift_grey_zone_to_auto(self):
        # 20-40 分但公司与日期双命中 → 自动采用（37 分是回测到的真实样本）。
        tier = egs_match.classify_candidate(
            {'score': 37, 'score_detail': {'name_partial': 0.8, 'company': 10, 'date': '±1d'}})
        self.assertEqual(tier, egs_match.TIER_AUTO)

    def test_grey_zone_without_company_stays_review(self):
        tier = egs_match.classify_candidate(
            {'score': 35, 'score_detail': {'name_partial': 0.8, 'date': '±1d'}})
        self.assertEqual(tier, egs_match.TIER_REVIEW)

    def test_non_game_content_is_reject(self):
        _, tier, d = tier_of('[080926] [枕] しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～ HCG.rar')
        self.assertTrue(d.get('non_game_content'))
        self.assertEqual(tier, egs_match.TIER_REJECT)

    def test_series_volume_is_reject(self):
        game = {'name': 'アルタードピンク ～特務戦隊デュエルレンジャー～',
                'company': 'Black Rainbow', 'date': '2008-09'}
        _, tier, d = tier_of('[080829][Black Rainbow] アルタードピンク ～特務戦隊デュエルレンジャー～ 第2話',
                             game=game)
        self.assertTrue(d.get('series_volume'))
        self.assertEqual(tier, egs_match.TIER_REJECT)

    def test_low_score_is_reject(self):
        _, tier, _ = tier_of('完全に無関係な別のゲーム ～テスト～')
        self.assertEqual(tier, egs_match.TIER_REJECT)

    def test_short_name_without_company_is_review(self):
        game = {'name': 'HOME', 'company': '某社', 'date': '2008-09'}
        _, tier, d = tier_of('HOME ～想い出の場所～', game=game)
        self.assertTrue(d.get('short_name_requires_company'))
        self.assertEqual(tier, egs_match.TIER_REVIEW)

    def test_annotate_tiers_writes_in_place(self):
        cands = [
            {'nyaa_title': '[080926] [枕] しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～',
             'nyaa_date': '2008-09-26'},
            {'nyaa_title': 'しゅぷれ～むキャンディ ～王道には王道たる理由があるんです！～'},
            {'nyaa_title': '完全に無関係な別のゲーム ～テスト～'},
        ]
        egs_match.select_best(GAME, cands)
        self.assertTrue(all('tier' in c for c in cands))
        self.assertEqual([c['tier'] for c in cands],
                         [egs_match.TIER_AUTO, egs_match.TIER_REVIEW, egs_match.TIER_REJECT])


if __name__ == '__main__':
    unittest.main()
