#!/usr/bin/env python3
"""刷新 EGS 作品关系缓存（status/egs_relations.json）。

匹配阶段的关系图护栏靠这份缓存判「候选是否指向兄弟作」；缓存缺失时护栏静默跳过，
所以每次 sync 前跑一次即可（9.7k 条边约 4 次请求）。

用法：
  .venv/bin/python scripts/refresh_relations.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from tool.egs_core import EGS_SQL_URL, HEADERS, _parse_result_table  # noqa: E402
from tool.runtime import runtime_paths  # noqa: E402

PAGE = 3000
SLEEP = 1.3
SQL = (
    "SELECT c.game_subject, c.game_object, c.kind, "
    "gs.gamename AS sub_name, go.gamename AS obj_name "
    "FROM connection_between_lists_of_games c "
    "LEFT JOIN gamelist gs ON gs.id=c.game_subject "
    "LEFT JOIN gamelist go ON go.id=c.game_object "
    "ORDER BY c.id LIMIT %d OFFSET %d"
)


def main() -> int:
    rel = {}
    total = 0
    for off in range(0, 100000, PAGE):
        time.sleep(SLEEP)
        sql = SQL % (PAGE, off)
        try:
            rows = _parse_result_table(
                requests.post(EGS_SQL_URL, data={"sql": sql}, headers=HEADERS, timeout=120).text
            )
        except Exception as exc:  # noqa: BLE001 - 单批失败即停止，保留已拉到的部分
            print(f"offset {off} 失败：{exc}", file=sys.stderr)
            break
        if not rows:
            break
        for x in rows:
            subject, obj = int(x["game_subject"]), int(x["game_object"])
            rel.setdefault(str(subject), []).append([x["kind"], x["obj_name"]])
            rel.setdefault(str(obj), []).append([x["kind"], x["sub_name"]])
        total += len(rows)
        print(f"offset {off}: +{len(rows)}，累计 {total}")
        if len(rows) < PAGE:
            break

    target = Path(runtime_paths()["status_dir"]) / "egs_relations.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(rel, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    print(f"已写入 {target}：{total} 条边 / {len(rel)} 个作品节点")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
