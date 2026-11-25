#!/usr/bin/env python3
"""双表影子对账：只读比较旧字段与新表，输出差异。可重复跑，非 0 退出表示有差异。

用法：
  runuser -u www-data -- .venv/bin/python scripts/artifact_check.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'egs.db'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    checks = []
    diff = 0

    def check(name, actual, expected, detail=None):
        nonlocal diff
        ok = actual == expected
        if not ok:
            diff += 1
        checks.append({'name': name, 'actual': actual, 'expected': expected,
                       'ok': ok, 'detail': detail})

    games_hash = q("SELECT COUNT(*) FROM egs_games WHERE COALESCE(infohash_hex,'')!=''")
    link_hash = q("""SELECT COUNT(DISTINCT l.egs_id) FROM egs_artifact_links l
        JOIN egs_artifacts a ON a.artifact_id=l.artifact_id WHERE a.infohash_hex IS NOT NULL""")
    check('有 hash 的记录数 == 有 hash link 的记录数', link_hash, games_hash)

    groups = q("""SELECT COUNT(*) FROM (SELECT artifact_id FROM egs_artifact_links
        GROUP BY artifact_id HAVING COUNT(DISTINCT egs_id)>1)""")
    check('一链多记录组数（基线 213）', groups, 213)

    submitted = q("SELECT COUNT(*) FROM egs_games WHERE COALESCE(submitted_115,0)=1")
    submitted_link = q("""SELECT COUNT(*) FROM egs_games g JOIN egs_artifact_links l ON l.egs_id=g.egs_id
        WHERE COALESCE(g.submitted_115,0)=1""")
    check('已提交记录全部有 link', submitted_link, submitted)

    folders = q("SELECT COUNT(*) FROM egs_115_folders")
    folder_art = q("SELECT COUNT(*) FROM egs_artifacts WHERE folder_cid IS NOT NULL")
    check('有目录的 artifact 数（基线 7374）', folder_art, 7374,
          {'folders_rows': folders})

    dup_missing = q("""SELECT COUNT(*) FROM egs_games g
        LEFT JOIN egs_artifact_links l ON l.egs_id=g.egs_id AND l.relation!='primary'
        WHERE COALESCE(g.magnet_duplicate,0)=1 AND l.egs_id IS NULL""")
    check('magnet_duplicate=1 都有非 primary link', dup_missing, 0)

    failed_with_link = q("""SELECT COUNT(*) FROM egs_games g JOIN egs_artifact_links l ON l.egs_id=g.egs_id
        JOIN egs_artifacts a ON a.artifact_id=l.artifact_id
        WHERE COALESCE(g.download_failed,0)=1 AND COALESCE(g.link,'')='' AND a.infohash_hex IS NOT NULL""")
    check('失败态残留 hash link（应清到 0）', failed_with_link, 0)

    mismatch = q("""SELECT COUNT(*) FROM egs_games g JOIN egs_artifact_links l ON l.egs_id=g.egs_id
        JOIN egs_artifacts a ON a.artifact_id=l.artifact_id
        WHERE COALESCE(g.infohash_hex,'')!='' AND a.infohash_hex IS NOT NULL
          AND lower(a.infohash_hex)!=lower(g.infohash_hex)""")
    check('记录 hash 与 link hash 不一致', mismatch, 0)

    orphans = q("SELECT COUNT(*) FROM egs_artifacts a WHERE NOT EXISTS (SELECT 1 FROM egs_artifact_links l WHERE l.artifact_id=a.artifact_id)")
    check('孤儿 artifact（无记录引用的目录，基线 7）', orphans, 7)

    conn.close()
    if args.json:
        print(json.dumps({'diff': diff, 'checks': checks}, ensure_ascii=False, indent=1))
    else:
        for c in checks:
            mark = 'OK ' if c['ok'] else 'DIFF'
            extra = f"  {c['detail']}" if c['detail'] else ''
            print(f"[{mark}] {c['name']}: {c['actual']} (期望 {c['expected']}){extra}")
        print('差异项:', diff)
    return 1 if diff else 0


if __name__ == '__main__':
    sys.exit(main())
