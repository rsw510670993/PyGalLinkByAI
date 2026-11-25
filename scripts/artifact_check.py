#!/usr/bin/env python3
"""双表影子对账（只读）：四类残留判定与 `egs artifact_audit` 同一口径。

差异只看 audit_artifact_links（动态口径，不写死基线）；覆盖统计只展示，
不参与退出码。有残留时退出码 1。

用法：
  runuser -u www-data -- .venv/bin/python scripts/artifact_check.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool.egs_core import audit_artifact_links, open_egs_db  # noqa: E402


def _q(conn, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    conn = open_egs_db()
    try:
        audit = audit_artifact_links(conn)
        info = {
            'artifacts': _q(conn, 'SELECT COUNT(*) FROM egs_artifacts'),
            'links': _q(conn, 'SELECT COUNT(*) FROM egs_artifact_links'),
            'games_with_hash': _q(conn, "SELECT COUNT(*) FROM egs_games WHERE COALESCE(infohash_hex,'')!=''"),
            'hash_link_egs': _q(conn, """SELECT COUNT(DISTINCT l.egs_id) FROM egs_artifact_links l
                JOIN egs_artifacts a ON a.artifact_id=l.artifact_id WHERE a.infohash_hex IS NOT NULL"""),
            'one_hash_multi_egs': _q(conn, """SELECT COUNT(*) FROM (SELECT artifact_id
                FROM egs_artifact_links GROUP BY artifact_id HAVING COUNT(DISTINCT egs_id)>1)"""),
            'submitted': _q(conn, 'SELECT COUNT(*) FROM egs_games WHERE COALESCE(submitted_115,0)=1'),
            'submitted_with_link': _q(conn, """SELECT COUNT(*) FROM egs_games g
                JOIN egs_artifact_links l ON l.egs_id=g.egs_id WHERE COALESCE(g.submitted_115,0)=1"""),
            'folders': _q(conn, 'SELECT COUNT(*) FROM egs_115_folders'),
            'artifacts_with_folder': _q(conn, 'SELECT COUNT(*) FROM egs_artifacts WHERE folder_cid IS NOT NULL'),
        }
    finally:
        conn.close()

    ok = bool(audit.get('ok'))
    if args.json:
        print(json.dumps({'ok': ok, 'audit': audit, 'info': info}, ensure_ascii=False, indent=1))
    else:
        print(f"[{'OK ' if ok else 'DIFF'}] 四类残留: {audit.get('counts')}")
        print(f"孤儿 artifact（只报不判）: {audit.get('orphan_artifacts')}")
        for k, v in info.items():
            print(f"  {k}: {v}")
        bad = sum(1 for v in (audit.get('counts') or {}).values() if v)
        print('差异项:', bad if ok else max(bad, 1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
