#!/usr/bin/env python3
"""月度提交编排（P0 草案，先 dry-run 验证可行性）。

流程：探明 115 剩余配额 → 选出可提交记录 → 按配额切片 → （execute 时）提交
→ 等 10 分钟 → check → organize。

默认 dry-run，只打印计划；--execute 才真提交。配额是硬闸门：本轮提交数
永远不超过「剩余配额」与「用户设定的每日上限」。

用法：
  runuser -u www-data -- .venv/bin/python scripts/run_month.py
  runuser -u www-data -- .venv/bin/python scripts/run_month.py --execute --cap 50
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool.egs_core import open_egs_db  # noqa: E402
from tool.egs_organize import mkdir_year_dir, resolve_cid  # noqa: E402

PENDING_SQL = """
    SELECT g.egs_id, g.date, g.name, g.link
      FROM egs_games g
     WHERE COALESCE(g.is_eroge, 1) = 1
       AND g.link IS NOT NULL AND g.link != ''
       AND COALESCE(g.downloaded, 0) = 0
       AND COALESCE(g.submitted_115, 0) = 0
       AND COALESCE(g.download_failed, 0) = 0
       AND COALESCE(g.submission_excluded, 0) = 0
       AND EXISTS (SELECT 1 FROM egs_nyaa_candidates c
                    WHERE c.egs_id = g.egs_id AND c.tier = 'auto' AND c.score >= 50)
     ORDER BY g.date, g.egs_id
"""


def remaining_quota() -> dict:
    from tool.p115_client import offline_list
    listing = offline_list()
    if not listing.get("success"):
        raise SystemExit(f"读取 115 离线列表失败：{listing.get('message')}")
    resp = listing.get("response") or {}
    try:
        quota = int(resp.get("quota") or 0)
    except (TypeError, ValueError):
        quota = 0
    return {"quota": quota, "used": resp.get("count"), "total": resp.get("total")}


def main() -> int:
    ap = argparse.ArgumentParser(description="月度提交编排（默认 dry-run）")
    ap.add_argument("--execute", action="store_true", help="真提交（默认只打印计划）")
    ap.add_argument("--cap", type=int, default=0, help="本次最多提交条数，0 表示由配额决定")
    args = ap.parse_args()

    quota = remaining_quota()
    conn = open_egs_db()
    try:
        rows = conn.execute(PENDING_SQL).fetchall()
    finally:
        conn.close()

    limit = quota["quota"]
    if args.cap > 0:
        limit = min(limit, args.cap)
    plan = rows[:limit]

    print(json.dumps({
        "mode": "execute" if args.execute else "dry-run",
        "quota": quota,
        "pending_total": len(rows),
        "plan_count": len(plan),
        "first": [{"egs_id": r["egs_id"], "date": r["date"], "name": r["name"]} for r in plan[:3]],
    }, ensure_ascii=False, indent=1))

    if not args.execute:
        print("dry-run：未提交。加 --execute 才会真跑。")
        return 0

    year_dirs: dict[str, int] = {}
    for row in plan:
        year = str(row["date"])[:4]
        year_path = f"/GAL/GAL-{year}"
        if year_path not in year_dirs:
            cid = resolve_cid(year_path) or mkdir_year_dir(int(year))
            if not cid:
                print(f"{row['egs_id']} → 目录 {year_path} 不存在且无法创建，跳过")
                continue
            year_dirs[year_path] = cid
        proc = subprocess.run(
            [sys.executable, "-m", "tool.cli", "115", "submit",
             "--egs-id", str(row["egs_id"]), "--magnet", row["link"],
             "--dir", year_path],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-1:]
        print(f"{row['egs_id']} {row['name'][:24]} → {tail[0][:80] if tail else 'no output'}")
        time.sleep(0.6)
    return 0


if __name__ == "__main__":
    sys.exit(main())
