#!/usr/bin/env python3
"""封面按名搜索 50 条分层抽查（只读：不写库、不下图）。

用途：在把「按名搜索」接进正式链路、并放宽匹配阈值之前，先量出
Getchu 搜索的命中率与误配率。对每条记录同时跑旧判据（pick_getchu_match）
与放宽判据（本文件 v2），把两者的决策并排写入 JSON，供人工复核。

用法：
    runuser -u www-data -- .venv/bin/python scripts/cover_spotcheck.py [--limit N]

输出：status/cover_spotcheck_<YYYYMMDD>.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tool.egs_core import ensure_egs_schema, open_egs_db  # noqa: E402
from tool.egs_thumb import (  # noqa: E402
    _similar,  # noqa: SLF001 - 复用同一套相似度实现，避免两套口径
    brand_match,
    has_cover,
    missing_kind,
    norm_brand,
    norm_text,
    pick_getchu_match,
    search_getchu,
)

SEED = 20261126
INTERVAL = 1.5          # 对 Getchu 保持礼貌间隔
LAYERS = (
    # (层名, 条数, SQL 条件说明由 _layer_rows 实现)
    ("downloaded_no_id", 30),
    ("downloaded_dlsite_id", 10),
    ("downloaded_getchu_id", 5),
    ("undownloaded_no_id", 5),
)

# 放宽判据的常数（与拟落地的 egs_thumb 改动一致）
TITLE_SPAN_MIN = 0.60       # 原 0.70
TITLE_RATIO_MIN = 0.82      # 原 0.86
CONTAIN_SCORE = 0.92        # 包含命中给的分数（原 max(ratio, 0.90)）
EMPTY_BRAND_TITLE_MIN = 0.90  # 候选品牌为空时，标题分数下限


def title_match_v2(target: str, candidate: str) -> tuple[bool, float]:
    """放宽后的标题匹配：包含判定下探到 0.60，相似度下探到 0.82。"""
    a, b = norm_text(target), norm_text(candidate)
    if not a or not b:
        return False, 0.0
    if a == b:
        return True, 1.0
    ratio = _similar(a, b)
    if a in b or b in a:
        span = min(len(a), len(b)) / max(len(a), len(b))
        if span >= TITLE_SPAN_MIN:
            return True, max(ratio, CONTAIN_SCORE)
    return (ratio >= TITLE_RATIO_MIN), ratio


def brand_state(target: str, candidate: str) -> tuple[str, float]:
    """品牌判定三态：match / empty（候选没写品牌）/ miss。"""
    a, b = norm_brand(target), norm_brand(candidate)
    if not b:
        return "empty", 0.0
    if not a:
        return "empty", 0.0
    ok, score = brand_match(target, candidate)
    return ("match", score) if ok else ("miss", score)


def pick_loose(row, results: list[dict]):
    """放宽判据：品牌为空不否决，但标题分数需 >= EMPTY_BRAND_TITLE_MIN。"""
    target_name = row["name"] or row["egs_name"] or ""
    target_brand = row["company"] or row["egs_company"] or ""
    scored = []
    for item in results:
        if (item.get("media") or "").upper() in {"CD", "DVD", "グッズ", "書籍", "アニメ", "音楽", "BOOK"}:
            continue
        t_ok, t_score = title_match_v2(target_name, item.get("title", ""))
        if not t_ok:
            continue
        state, b_score = brand_state(target_brand, item.get("brand", ""))
        if state == "miss":
            continue
        if state == "empty" and t_score < EMPTY_BRAND_TITLE_MIN:
            continue
        scored.append({**item, "title_score": round(t_score, 3),
                       "brand_state": state, "brand_score": round(b_score, 3)})
    if not scored:
        return None, {"reason": "无候选通过放宽判据", "candidates": len(results)}
    scored.sort(key=lambda it: (-it["title_score"], -it["brand_score"]))
    best = scored[0]
    if len(scored) > 1 and scored[1]["title_score"] == best["title_score"]:
        return None, {"reason": "多个候选并列，放弃", "candidates": len(results)}
    return best, {"reason": "放宽判据命中", "candidates": len(results)}


def _layer_rows(conn, layer: str, n: int) -> list:
    """按层取候选行，最后统一抽样，保证各层配额。"""
    rows = conn.execute(
        "SELECT egs_id, getchu_id, dlsite_id, dmm, date, name, company, "
        "egs_name, egs_company, downloaded, official_url FROM egs_games"
    ).fetchall()
    def blank(v):
        return v is None or str(v).strip() == ""
    picked = []
    for r in rows:
        gid, pid, dmm = r["getchu_id"], r["dlsite_id"], r["dmm"]
        if layer == "downloaded_no_id":
            cond = r["downloaded"] and blank(gid) and blank(pid) and blank(dmm)
        elif layer == "downloaded_dlsite_id":
            cond = r["downloaded"] and not blank(pid) and not has_cover(r)
        elif layer == "downloaded_getchu_id":
            cond = r["downloaded"] and not blank(gid) and not has_cover(r)
        elif layer == "undownloaded_no_id":
            cond = (not r["downloaded"]) and blank(gid) and blank(pid) and blank(dmm)
        else:
            cond = False
        if cond:
            picked.append(r)
    random.Random(f"{SEED}-{layer}").shuffle(picked)
    return picked[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    args = ap.parse_args()

    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        samples = []
        for layer, n in LAYERS:
            rows = _layer_rows(conn, layer, n)
            for r in rows:
                samples.append((layer, r))
    finally:
        conn.close()

    if args.limit:
        samples = samples[: args.limit]

    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Referer": "https://www.getchu.com/",
    })

    records = []
    for i, (layer, row) in enumerate(samples):
        name = row["name"] or row["egs_name"] or ""
        hits, err = search_getchu(name, session, pages=1)
        old, old_ev = pick_getchu_match(row, hits)
        new, new_ev = pick_loose(row, hits)
        rec = {
            "layer": layer,
            "egs_id": row["egs_id"],
            "name": name,
            "company": row["company"] or row["egs_company"] or "",
            "date": row["date"],
            "have_id": {"getchu": row["getchu_id"], "dlsite": row["dlsite_id"], "dmm": row["dmm"]},
            "missing_kind": missing_kind(row),
            "search_error": err,
            "hit_count": len(hits),
            "old": None if old is None else {
                "getchu_id": old["getchu_id"], "title": old["title"],
                "brand": old["brand"], "score": old["title_score"],
            },
            "old_reason": old_ev.get("reason"),
            "new": None if new is None else {
                "getchu_id": new["getchu_id"], "title": new["title"],
                "brand": new["brand"], "score": new["title_score"],
                "brand_state": new["brand_state"],
            },
            "new_reason": new_ev.get("reason"),
            "top_hits": [
                {"getchu_id": h["getchu_id"], "title": h["title"], "brand": h["brand"],
                 "release": h["release"], "media": h["media"]}
                for h in hits[:3]
            ],
        }
        records.append(rec)
        print(f"[{i + 1}/{len(samples)}] {row['egs_id']} {layer} hits={len(hits)} "
              f"old={'✓' if old else '✗'} new={'✓' if new else '✗'} {name}", flush=True)
        if i + 1 < len(samples):
            time.sleep(INTERVAL)

    summary = {
        "total": len(records),
        "old_ok": sum(1 for r in records if r["old"]),
        "new_ok": sum(1 for r in records if r["new"]),
        "new_only": sum(1 for r in records if r["new"] and not r["old"]),
        "old_only": sum(1 for r in records if r["old"] and not r["new"]),
        "by_layer": {},
        "search_errors": sum(1 for r in records if r["search_error"]),
    }
    for layer, _n in LAYERS:
        rows = [r for r in records if r["layer"] == layer]
        summary["by_layer"][layer] = {
            "n": len(rows),
            "new_ok": sum(1 for r in rows if r["new"]),
        }

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": SEED,
        "interval": INTERVAL,
        "criteria_v2": {
            "title_span_min": TITLE_SPAN_MIN,
            "title_ratio_min": TITLE_RATIO_MIN,
            "contain_score": CONTAIN_SCORE,
            "empty_brand_title_min": EMPTY_BRAND_TITLE_MIN,
        },
        "summary": summary,
        "records": records,
    }
    dest = ROOT / "status" / f"cover_spotcheck_{time.strftime('%Y%m%d')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"written: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
