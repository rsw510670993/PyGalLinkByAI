#!/usr/bin/env python3
"""
Getchu 缩略图数据刷新工具

解决历史数据缺失 getchu_id/thumb_url 的问题，分三个阶段：

  merge  (A) 合并新旧日期格式产生的重复记录
         - 新格式行 (YYYY-MM-DD) 的 gid/thumb_url 等字段"只填不覆盖"地并入旧格式行 (YYYY-MM)
         - 115 下载数据（link/downloaded/submitted等）只在新旧行互补时迁移，绝不丢失
         - 无独有数据的新格式行删除；无旧格式对应行的新格式行把日期规范为 YYYY-MM

  crawl  (B) 重新爬取清单，为旧记录补全 getchu_id/thumb_url/price/detail_url/release_date
         - 复用 core.get_all_getchu_games（内部 upsert 只更新扩展字段，不触碰115数据）

  thumbs (C) 批量下载缺失的缩略图文件（循环到没有待下载项为止）

用法:
  python tool/refresh_thumbnails.py --phase merge
  python tool/refresh_thumbnails.py --phase crawl --start-year 2008 --end-year 2026
  python tool/refresh_thumbnails.py --phase thumbs --sleep 0.25
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tool
from tool.core import open_db, ensure_getchu_schema
from tool.runtime import runtime_paths
from tool.getchu_detail import batch_download_thumbnails, get_games_without_thumbnails

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("refresh_thumbnails")


def phase_merge(conn):
    """合并新格式(YYYY-MM-DD)重复行到旧格式(YYYY-MM)行，返回统计 dict"""
    cursor = conn.cursor()
    stats = {"new_format_total": 0, "merged": 0, "renamed": 0,
             "deleted": 0, "kept_conflict": 0}

    # 扩展字段：只填不覆盖（旧行为空才写入）
    FILL_FIELDS = ["getchu_id", "thumb_url", "price", "detail_url", "release_date"]
    # 115相关字段：同样只填不覆盖，确保绝不丢失
    TRANSFER_FIELDS = ["link", "nyaa_name", "comment", "infohash_hex",
                       "submitted_pick_code"]

    cursor.execute("SELECT rowid FROM getchu_games WHERE date LIKE '____-__-__' ORDER BY rowid")
    new_ids = [r[0] for r in cursor.fetchall()]
    stats["new_format_total"] = len(new_ids)
    logger.info("发现 %d 条新格式(YYYY-MM-DD)记录待处理", len(new_ids))

    for nid in new_ids:
        cursor.execute("SELECT * FROM getchu_games WHERE rowid = ?", (nid,))
        cols = [d[0] for d in cursor.description]
        n = dict(zip(cols, cursor.fetchone()))
        if n is None or not n["date"].count("__") and len(n["date"]) != 10:
            pass  # 占位，下方统一判断
        ym = n["date"][:7]
        if len(n["date"]) != 10:
            continue  # 已被之前的操作改变，跳过

        # 查找旧格式对应行：同月同名，优先同公司
        cursor.execute(
            """SELECT rowid FROM getchu_games
               WHERE date = ? AND name = ? AND company = ? AND rowid != ? AND length(date) = 7""",
            (ym, n["name"], n["company"], nid),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                """SELECT rowid FROM getchu_games
                   WHERE date = ? AND name = ? AND rowid != ? AND length(date) = 7
                   ORDER BY rowid LIMIT 1""",
                (ym, n["name"], nid),
            )
            row = cursor.fetchone()

        if row:
            oid = row[0]
            # 1) 只填不覆盖：迁移扩展字段
            for f in FILL_FIELDS:
                cursor.execute(
                    f"""UPDATE getchu_games SET {f} = ?
                        WHERE rowid = ? AND ({f} IS NULL OR {f} = '')""",
                    (n[f], oid),
                )
            # size（媒体类型）也为空才填
            cursor.execute(
                "UPDATE getchu_games SET size = ? WHERE rowid = ? AND (size IS NULL OR size = '')",
                (n["size"], oid),
            )
            # 2) 115相关字段：旧行为空/0才迁移
            for f in TRANSFER_FIELDS:
                cursor.execute(
                    f"""UPDATE getchu_games SET {f} = ?
                        WHERE rowid = ? AND ({f} IS NULL OR {f} = '')""",
                    (n[f], oid),
                )
            for f in ["downloaded", "submitted_115"]:
                cursor.execute(
                    f"""UPDATE getchu_games SET {f} = ?
                        WHERE rowid = ? AND COALESCE({f}, 0) = 0""",
                    (n[f], oid),
                )
            # 3) 安全检查：迁移后新行是否还有未保留的115数据
            has_unique_link = bool(n["link"]) and _field_now_empty(cursor, oid, "link") is False
            # 重新读取旧行确认关键字段已保留
            kept = _row_has_115_data(cursor, oid)
            new_still_has = _row_has_115_data_by_values(n)
            if new_still_has and not _transferred_ok(cursor, oid, n):
                # 理论上不会走到这里：存在无法安全合并的115数据，保留新行
                stats["kept_conflict"] += 1
                logger.warning("⚠️ rowid=%s 与旧行存在115数据冲突，保留待人工检查: %s | %s",
                               nid, n["date"], n["name"][:30])
                continue
            cursor.execute("DELETE FROM getchu_games WHERE rowid = ?", (nid,))
            stats["merged"] += 1
            stats["deleted"] += 1
        else:
            # 没有旧格式对应行 → 日期规范化为 YYYY-MM
            cursor.execute(
                """UPDATE getchu_games SET date = ?
                   WHERE rowid = ? AND NOT EXISTS (
                       SELECT 1 FROM getchu_games g2
                       WHERE g2.date = ? AND g2.name = getchu_games.name AND g2.rowid != getchu_games.rowid
                   )""",
                (ym, nid, ym),
            )
            if cursor.rowcount > 0:
                stats["renamed"] += 1
            else:
                stats["kept_conflict"] += 1

    conn.commit()
    return stats


def _field_now_empty(cursor, rowid, field):
    cursor.execute(f"SELECT {field} FROM getchu_games WHERE rowid = ?", (rowid,))
    v = cursor.fetchone()[0]
    return v in (None, "")


def _row_has_115_data(cursor, rowid):
    cursor.execute(
        """SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid = ?""",
        (rowid,),
    )
    link, dl, sub = cursor.fetchone()
    return bool(link) or int(dl or 0) == 1 or int(sub or 0) == 1


def _row_has_115_data_by_values(n):
    return bool(n["link"]) or int(n["downloaded"] or 0) == 1 or int(n["submitted_115"] or 0) == 1


def _transferred_ok(cursor, oid, n):
    """确认新行的115数据都已保留在旧行（或新行本来就没有）"""
    cursor.execute(
        "SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid = ?", (oid,)
    )
    olink, odl, osub = cursor.fetchone()
    if n["link"] and not olink:
        return False
    if int(n["downloaded"] or 0) == 1 and int(odl or 0) != 1:
        return False
    if int(n["submitted_115"] or 0) == 1 and int(osub or 0) != 1:
        return False
    return True


def phase_crawl(conn, start_year, end_year):
    """重新爬取清单补全扩展字段（内部 upsert 不触碰115数据）"""
    logger.info("开始重爬 %d-%d 年清单，补全 getchu_id/thumb_url ...", start_year, end_year)
    ok = tool.get_all_getchu_games(start_year, end_year, 1, 12)
    logger.info("重爬完成: %s", "成功" if ok else "失败")
    return ok


def phase_thumbs(conn, sleep_seconds=0.25, batch_size=500):
    """循环下载缺失的缩略图，直到没有待下载项"""
    cursor = conn.cursor()
    total_stats = {"success": 0, "failed": 0, "skipped": 0}
    round_no = 0
    while True:
        round_no += 1
        pending = get_games_without_thumbnails(conn, batch_size)
        if not pending:
            logger.info("✅ 第%d轮：没有待下载的缩略图，全部完成", round_no)
            break
        logger.info("第%d轮：待下载 %d 条 (sleep=%.2fs)", round_no, len(pending), sleep_seconds)
        stats = batch_download_thumbnails(pending, conn, sleep_seconds=sleep_seconds)
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
        logger.info("第%d轮完成: 累计 成功%d 失败%d 跳过%d",
                    round_no, total_stats["success"], total_stats["failed"], total_stats["skipped"])
        if stats.get("success", 0) == 0 and stats.get("skipped", 0) == len(pending):
            # 本轮全部跳过，避免死循环
            logger.info("本轮全部跳过，结束")
            break
    return total_stats


def print_summary(conn, tag):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM getchu_games")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE getchu_id IS NOT NULL")
    with_gid = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE thumb_url IS NOT NULL")
    with_url = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE thumb_path IS NOT NULL")
    with_path = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE COALESCE(downloaded,0)=1")
    downloaded = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE COALESCE(submitted_115,0)=1")
    submitted = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE link IS NOT NULL")
    with_link = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE date LIKE '____-__-__'")
    new_fmt = cursor.fetchone()[0]
    logger.info(
        "[%s] 总数:%d | gid:%d thumb_url:%d 已下载图:%d | 115数据 downloaded:%d submitted:%d 磁链:%d | 新格式残留:%d",
        tag, total, with_gid, with_url, with_path, downloaded, submitted, with_link, new_fmt,
    )


def main():
    parser = argparse.ArgumentParser(description="Getchu 缩略图数据刷新")
    parser.add_argument("--phase", required=True, choices=["merge", "crawl", "thumbs", "status"],
                        help="执行阶段")
    parser.add_argument("--start-year", type=int, default=2008)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--sleep", type=float, default=0.25, help="缩略图下载限速秒数")
    args = parser.parse_args()

    paths = runtime_paths()
    conn = open_db(db_path=paths["db_path"])
    ensure_getchu_schema(conn)

    if args.phase == "status":
        print_summary(conn, "当前状态")
    elif args.phase == "merge":
        print_summary(conn, "merge前")
        stats = phase_merge(conn)
        logger.info("合并统计: %s", stats)
        print_summary(conn, "merge后")
    elif args.phase == "crawl":
        print_summary(conn, "crawl前")
        phase_crawl(conn, args.start_year, args.end_year)
        print_summary(conn, "crawl后")
    elif args.phase == "thumbs":
        print_summary(conn, "thumbs前")
        stats = phase_thumbs(conn, sleep_seconds=args.sleep)
        logger.info("下载统计: %s", stats)
        print_summary(conn, "thumbs后")

    conn.close()


if __name__ == "__main__":
    main()
