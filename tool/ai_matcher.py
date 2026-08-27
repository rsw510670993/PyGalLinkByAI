"""aigc2d AI matcher for Getchu/Nyaa candidate selection.

AI communication follows the phpAiAPI project:
- key file: tool/config/key/aigc2d.key
- base_url: https://next.aigc2d.com/v1
- OpenAI Python SDK: OpenAI(api_key=..., base_url=...).chat.completions.create(...)
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import GetchuGame, NyaaData, MatchResult
from .runtime import read_config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://next.aigc2d.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_CANDIDATES = 10
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_REVIEW_THRESHOLD = 0.4

VERDICTS = {"matched", "unmatched", "duplicate", "discarded", "review"}
KEYWORD_RULE_TYPES = {"include", "discard", "duplicate", "review"}
PROTECTED_PUBLISHERS = {"girlcelly", "2d.g.f."}


class Aigc2dError(Exception):
    """Base error for aigc2d API communication."""


class MatchResponseError(Exception):
    """Raised when the AI response cannot be parsed/validated."""


def _tool_dir():
    return os.path.dirname(os.path.abspath(__file__))


def default_key_path():
    return os.path.join(_tool_dir(), "config", "key", "aigc2d.key")


def load_api_key(key_path=None):
    key_path = key_path or os.environ.get("AIGC2D_KEY_PATH") or default_key_path()
    env_key = os.environ.get("AIGC2D_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        raw = Path(key_path).read_text(encoding="utf-8")
    except Exception:
        return ""
    return raw.strip(" \t\n\r\0\x0B\"'")


@dataclass
class Aigc2dConfig:
    base_url: str = DEFAULT_BASE_URL
    default_model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: int = DEFAULT_TIMEOUT
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD
    api_key: str = ""
    key_path: str = ""

    @classmethod
    def from_config(cls, config=None, config_path=None, key_path=None):
        config = config or read_config(config_path)
        section = config.get("aigc2d") or {}
        key_path = key_path or os.environ.get("AIGC2D_KEY_PATH") or default_key_path()
        return cls(
            base_url=str(section.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
            default_model=str(section.get("default_model") or DEFAULT_MODEL),
            max_tokens=int(section.get("max_tokens") or DEFAULT_MAX_TOKENS),
            timeout=int(section.get("timeout") or DEFAULT_TIMEOUT),
            max_candidates=int(section.get("max_candidates") or DEFAULT_MAX_CANDIDATES),
            confidence_threshold=float(section.get("confidence_threshold") or DEFAULT_CONFIDENCE_THRESHOLD),
            review_threshold=float(section.get("review_threshold") or DEFAULT_REVIEW_THRESHOLD),
            api_key=load_api_key(key_path),
            key_path=key_path,
        )


class Aigc2dClient:
    """Thin OpenAI-compatible chat client for aigc2d (mirrors phpAiAPI)."""

    def __init__(self, config=None):
        self.config = config or Aigc2dConfig.from_config()
        if not self.config.api_key:
            raise Aigc2dError(
                f"未找到 aigc2d API Key，请检查 {self.config.key_path} 或设置 AIGC2D_API_KEY"
            )
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise Aigc2dError(
                    "缺少 openai 依赖，请先执行: pip install -r tool/requirements.txt"
                ) from e
            self._client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            )
        return self._client

    def chat(self, messages, model=None, max_tokens=None, timeout=None):
        """Send a chat completion request and return the assistant text.

        Retries up to 3 times on any exception, then raises Aigc2dError.
        """
        client = self._get_client()
        model = model or self.config.default_model
        max_tokens = max_tokens if max_tokens is not None else self.config.max_tokens
        timeout = timeout if timeout is not None else self.config.timeout

        last_error = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=False,
                    timeout=float(timeout),
                    max_tokens=int(max_tokens),
                )
                if not getattr(response, "choices", None):
                    raise Aigc2dError("aigc2d 响应缺少 choices")
                content = response.choices[0].message.content or ""
                return content
            except Exception as e:
                last_error = e
                logger.warning("aigc2d chat 第 %s 次调用失败: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))

        raise Aigc2dError(f"aigc2d chat 调用失败: {last_error}")

    def list_models(self, timeout=None):
        """Optional model catalog fetch (same endpoint as phpAiAPI)."""
        import requests

        timeout = timeout if timeout is not None else self.config.timeout
        url = f"{self.config.base_url}/models"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [m.get("id") for m in data.get("data", []) if m.get("id")]


def _normalize_for_match(text):
    return re.sub(r"\s+", "", str(text or "").lower())


def _load_soul_prompt():
    soul_path = os.path.join(_tool_dir(), "soul.md")
    try:
        return Path(soul_path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def build_match_prompt(game, candidates):
    """Return (system_prompt, user_prompt) for a Getchu game and Nyaa candidates."""
    soul_text = _load_soul_prompt()
    system_prompt = (
        "你是成人游戏（美少女ゲーム/エロゲ）磁力链接匹配专家。"
        "用户会给出 Getchu 游戏信息和若干 Nyaa 候选。"
        "请根据标题、公司、发售日期、文件大小和候选命名习惯，"
        "判断候选是匹配、重复、应弃置还是不匹配。"
        "注意：候选名常包含 限定版/特典/自炊/自购/罗马音/缩写/下载站命名 等变化。"
        "只返回 JSON，不要输出其他内容。"
    )
    if soul_text:
        system_prompt += "\n\n【固定约束】\n" + soul_text

    lines = [
        "Getchu 信息：",
        f"- 发售月份：{game.date}",
        f"- 游戏名：{game.name}",
        f"- 公司：{game.company}",
        "",
        "Nyaa 候选（下标从 0 开始）：",
    ]
    for index, candidate in enumerate(candidates):
        size = getattr(candidate, "size", None) or ""
        date = getattr(candidate, "date", None) or ""
        lines.append(f"[{index}] 名称: {candidate.name} | 大小: {size} | 日期: {date}")
    lines.extend(
        [
            "",
            "请返回严格 JSON，格式如下：",
            "{",
            '  "matched_index": 0,',
            '  "verdict": "matched|unmatched|duplicate|discarded|review",',
            '  "confidence": 0.0,',
            '  "reason": "简短说明",',
            '  "matched_name": "候选名称",',
            '  "keywords": [{"keyword": "廉价版", "rule_type": "discard"}]',
            "}",
            "verdict 取值说明：",
            "- matched: 候选与 Getchu 游戏本体匹配",
            "- unmatched: 没有候选匹配",
            "- duplicate: 候选是同一资源的重复发布",
            "- discarded: 候选应弃置，如廉价版/特典/OST/补丁等",
            "- review: 无法确定，需要人工复核",
            "keywords 只总结这次判断中值得记忆的筛选关键字，rule_type 取 include/discard/duplicate/review。",
        ]
    )
    return system_prompt, "\n".join(lines)


def parse_match_response(content):
    """Parse and validate the strict JSON returned by the AI matcher."""
    if not content:
        raise MatchResponseError("AI 返回内容为空")

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise MatchResponseError(f"AI 返回内容无法解析为 JSON: {content[:200]!r}")
        try:
            data = json.loads(match.group(0))
        except Exception as e:
            raise MatchResponseError(f"AI JSON 解析失败: {e}") from e

    if not isinstance(data, dict):
        raise MatchResponseError("AI 返回内容不是 JSON 对象")

    matched_index = data.get("matched_index")
    if matched_index is None:
        raise MatchResponseError("AI 返回缺少 matched_index")
    try:
        matched_index = int(matched_index)
    except (TypeError, ValueError) as e:
        raise MatchResponseError(f"matched_index 不是整数: {matched_index!r}") from e

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError) as e:
        raise MatchResponseError(f"confidence 不是数字: {data.get('confidence')!r}") from e
    confidence = max(0.0, min(1.0, confidence))

    verdict = str(data.get("verdict") or "").strip().lower()
    allowed_verdicts = {"matched", "unmatched", "duplicate", "discarded", "review"}
    if verdict not in allowed_verdicts:
        verdict = "matched" if matched_index >= 0 else "unmatched"

    keywords = []
    raw_keywords = data.get("keywords") or []
    if isinstance(raw_keywords, list):
        allowed_rule_types = {"include", "discard", "duplicate", "review"}
        for item in raw_keywords:
            if not isinstance(item, dict):
                continue
            keyword = str(item.get("keyword") or item.get("name") or "").strip()
            rule_type = str(item.get("rule_type") or item.get("type") or "review").strip().lower()
            if not keyword or rule_type not in allowed_rule_types:
                continue
            try:
                kw_confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                kw_confidence = 0.5
            kw_confidence = max(0.0, min(1.0, kw_confidence))
            keywords.append({
                "keyword": keyword,
                "rule_type": rule_type,
                "confidence": kw_confidence,
            })

    return {
        "matched_index": matched_index,
        "verdict": verdict,
        "confidence": confidence,
        "reason": str(data.get("reason") or "").strip(),
        "matched_name": str(data.get("matched_name") or "").strip(),
        "keywords": keywords,
    }


def fallback_rule_match(game, nyaa_data_list):
    """Refactored old rule matcher. Used as fallback when AI is unavailable."""
    base = {
        "date": getattr(game, "date", None),
        "name": getattr(game, "name", None),
        "company": getattr(game, "company", None),
        "candidate_count": len(nyaa_data_list),
    }

    if not nyaa_data_list:
        return MatchResult(
            **base,
            selected_index=-1,
            confidence=0.0,
            source="none",
            reason="no candidates",
            verdict="unmatched",
            keywords=[],
        )

    yymm = str(game.date).replace("-", "")[2:]

    def find_first(predicate):
        for index, candidate in enumerate(nyaa_data_list):
            if predicate(candidate):
                return index
        return None

    index = find_first(lambda d: "girlcelly" in d.name and yymm in d.name)
    reason = "rule: girlcelly + YYMM"
    if index is None:
        index = find_first(lambda d: "2D.G.F." in d.name and yymm in d.name)
        reason = "rule: 2D.G.F. + YYMM"
    if index is None:
        index = find_first(lambda d: yymm in d.name)
        reason = "rule: YYMM only"

    if index is None:
        # Old behaviour: no strong month signal, record first candidate but clear link.
        index = 0
        candidate = nyaa_data_list[index]
        return MatchResult(
            **base,
            selected_index=index,
            matched_name=candidate.name,
            link=None,
            size=candidate.size,
            confidence=0.2,
            source="rule",
            reason="rule: no YYMM candidate, first result link cleared",
            verdict="review",
            keywords=[],
        )

    candidate = nyaa_data_list[index]
    return MatchResult(
        **base,
        selected_index=index,
        matched_name=candidate.name,
        link=candidate.link,
        size=candidate.size,
        confidence=0.5,
        source="rule",
        reason=reason,
        verdict="matched",
        keywords=[],
    )


def _strong_rule_hit(game, nyaa_data_list):
    """Return index if normalized exact title + YYMM matches (AI can be skipped)."""
    yymm = str(game.date).replace("-", "")[2:]
    target = _normalize_for_match(getattr(game, "name", None))
    if not target:
        return None
    for index, candidate in enumerate(nyaa_data_list):
        candidate_norm = _normalize_for_match(candidate.name)
        if yymm in candidate.name and target and candidate_norm.startswith(target):
            return index
    return None


def _is_effective_exclude_rule(rule):
    """Only use trusted/high-confidence discard/duplicate memory rules.

    One-off AI suggestions with default confidence 0.5 are too noisy to apply
    as hard filters; manual rules are always trusted.
    """
    if rule.get("rule_type") not in ("discard", "duplicate"):
        return False
    keyword = str(rule.get("keyword") or "").strip()
    if not keyword or keyword.lower() in PROTECTED_PUBLISHERS:
        return False
    if rule.get("source") == "manual":
        return True
    try:
        hit_count = int(rule.get("hit_count") or 0)
        confidence = float(rule.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return False
    return hit_count >= 2 and confidence >= 0.7


def filter_candidates_by_keyword_rules(nyaa_data_list, keyword_rules=None):
    """Remove candidates that match trusted discard/duplicate memory rules."""
    rules = keyword_rules or []
    exclude_keywords = [
        str(rule.get("keyword") or "").strip()
        for rule in rules
        if _is_effective_exclude_rule(rule)
    ]
    if not exclude_keywords:
        return list(nyaa_data_list)

    filtered = []
    for candidate in nyaa_data_list:
        if any(k in candidate.name for k in exclude_keywords):
            continue
        filtered.append(candidate)
    return filtered


def judge_nyaa_match(game, nyaa_data_list, config=None, client=None, keyword_rules=None):
    """Unified entry point for Getchu × Nyaa matching.

    Uses memory keyword rules and strong rules to skip AI when possible,
    calls aigc2d otherwise, and falls back to the old rule matcher on any
    AI failure.
    """
    config = config or Aigc2dConfig.from_config()
    original_candidates = list(nyaa_data_list)[: max(1, config.max_candidates)]
    if not original_candidates:
        return MatchResult(
            date=getattr(game, "date", None),
            name=getattr(game, "name", None),
            company=getattr(game, "company", None),
            selected_index=-1,
            confidence=0.0,
            source="none",
            reason="no candidates",
            candidate_count=0,
            verdict="unmatched",
            keywords=[],
        )

    candidates = filter_candidates_by_keyword_rules(original_candidates, keyword_rules)
    base = {
        "date": getattr(game, "date", None),
        "name": getattr(game, "name", None),
        "company": getattr(game, "company", None),
        "candidate_count": len(candidates),
    }

    if not candidates:
        return MatchResult(
            **base,
            selected_index=-1,
            confidence=0.0,
            source="rule",
            reason="all candidates filtered by memory keyword rules",
            verdict="discarded",
            keywords=[],
        )

    # Very small candidate sets do not need AI.
    if len(candidates) == 1:
        return fallback_rule_match(game, candidates)

    strong_index = _strong_rule_hit(game, candidates)
    if strong_index is not None:
        candidate = candidates[strong_index]
        return MatchResult(
            **base,
            selected_index=strong_index,
            matched_name=candidate.name,
            link=candidate.link,
            size=candidate.size,
            confidence=0.95,
            source="rule",
            reason="strong rule: exact title + YYMM",
            verdict="matched",
            keywords=[],
        )

    if client is None:
        try:
            client = Aigc2dClient(config)
        except Exception as e:
            logger.warning("aigc2d client 初始化失败，使用规则降级: %s", e)
            return fallback_rule_match(game, candidates)

    system_prompt, user_prompt = build_match_prompt(game, candidates)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        content = client.chat(
            messages,
            model=config.default_model,
            max_tokens=config.max_tokens,
            timeout=config.timeout,
        )
        parsed = parse_match_response(content)
    except Exception as e:
        logger.warning("aigc2d 判断失败，使用规则降级: %s", e)
        return fallback_rule_match(game, candidates)

    index = parsed["matched_index"]
    if index == -1:
        return MatchResult(
            **base,
            selected_index=-1,
            matched_name=None,
            link=None,
            size=None,
            confidence=0.0,
            source="ai",
            reason=parsed.get("reason") or "AI no match",
            verdict=parsed.get("verdict") or "unmatched",
            keywords=parsed.get("keywords") or [],
        )

    if index < 0 or index >= len(candidates):
        logger.warning("AI 返回非法下标 %s，使用规则降级", index)
        return fallback_rule_match(game, candidates)

    candidate = candidates[index]
    return MatchResult(
        **base,
        selected_index=index,
        matched_name=parsed.get("matched_name") or candidate.name,
        link=candidate.link,
        size=candidate.size,
        confidence=parsed["confidence"],
        source="ai",
        reason=parsed.get("reason") or "AI match",
        verdict=parsed.get("verdict") or "matched",
        keywords=parsed.get("keywords") or [],
    )


__all__ = [
    "Aigc2dError",
    "Aigc2dConfig",
    "Aigc2dClient",
    "MatchResponseError",
    "build_match_prompt",
    "default_key_path",
    "fallback_rule_match",
    "filter_candidates_by_keyword_rules",
    "judge_nyaa_match",
    "load_api_key",
    "parse_match_response",
]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="aigc2d API communication smoke test")
    parser.add_argument("--message", default="只回复 OK", help="message to send")
    parser.add_argument("--list-models", action="store_true", help="list available models from /models")
    args = parser.parse_args()

    client = Aigc2dClient()
    if args.list_models:
        print(json.dumps(client.list_models(), ensure_ascii=False, indent=2))
        return
    content = client.chat([{"role": "user", "content": args.message}])
    print(content)


if __name__ == "__main__":
    main()
