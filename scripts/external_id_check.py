#!/usr/bin/env python3
"""外部 ID 映射表影子对账（只读）。

比较 egs_games 上的 getchu_id / dlsite_id / dmm 与 egs_external_ids 映射表，
输出四类差异；有差异时以退出码 1 结束，供巡检脚本使用。

用法：
    runuser -u www-data -- .venv/bin/python scripts/external_id_check.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tool.egs_core import ensure_egs_schema, open_egs_db  # noqa: E402
from tool.egs_extids import audit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    conn = open_egs_db()
    try:
        ensure_egs_schema(conn)
        result = audit(conn)
    finally:
        conn.close()

    diffs = int(result.get("missing_mapping", 0)) + int(result.get("mapping_only", 0)) \
        + int(result.get("conflict_total", 0)) + int(result.get("orphan", 0))
    result["diffs"] = diffs
    result["ok"] = diffs == 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"rows={result.get('rows', 0)} confirmed={result.get('confirmed', 0)} "
              f"candidate={result.get('candidate', 0)}")
        for source, stats in (result.get("by_source") or {}).items():
            print(f"  {source:7} legacy={stats['legacy']:>6} mapping={stats['mapping']:>6} "
                  f"missing_mapping={stats['missing_mapping']:>5} "
                  f"mapping_only={stats['mapping_only']:>5}")
        print(f"conflict={result.get('conflict_total', 0)} orphan={result.get('orphan', 0)} "
              f"-> {'OK' if result['ok'] else 'DIFF'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
