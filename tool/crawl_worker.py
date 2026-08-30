"""爬取+AI去重 后台worker：crawl 命令的执行体。

用法:
    python3 tool/crawl_worker.py --start-year 2026 --end-year 2026 [--month 3] [--no-ai]
"""
import argparse
import logging
import os
import signal
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from tool.dedup_service import dedup_month
from tool.runtime import (
    cleanup_old_logs,
    daily_log_path,
    now_ts,
    runtime_paths,
    write_json_atomic,
)

_stop_requested = False


def _handle_stop(signum, frame):
    global _stop_requested
    _stop_requested = True


def _default_status():
    return {
        "running": False,
        "pid": None,
        "start_year": None,
        "end_year": None,
        "month": None,
        "current_year": None,
        "current_month": None,
        "months_done": 0,
        "months_total": 0,
        "raw_fetched": 0,
        "inserted": 0,
        "dup_logged": 0,
        "ai_calls": 0,
        "cache_hits": 0,
        "reconcile_merged": 0,
        "reconcile_editions": 0,
        "reconcile_skipped": 0,
        "reconcile_ai_calls": 0,
        "errors": [],
        "month_stats": [],
        "started_at": None,
        "updated_at": None,
        "stopped_reason": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--month", type=int, default=0, help="仅处理指定月份(0=全年)")
    parser.add_argument("--no-ai", action="store_true", help="禁用AI，仅规则分组")
    parser.add_argument("--no-reconcile", action="store_true", dest="no_reconcile",
                        help="跳过存量行再去重(reconcile)阶段")
    args = parser.parse_args()

    paths = runtime_paths()
    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)
    if paths.get("log_auto_cleanup"):
        cleanup_old_logs(retention_days=paths.get("log_retention_days"))

    logging.basicConfig(
        filename=daily_log_path("crawl"),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("crawl_worker")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    start_year, end_year = args.start_year, args.end_year
    if start_year > end_year:
        start_year, end_year = end_year, start_year

    months = [args.month] if args.month else list(range(1, 13))
    tasks = [(y, m) for y in range(start_year, end_year + 1) for m in months]

    status = _default_status()
    status.update({
        "running": True,
        "pid": os.getpid(),
        "start_year": start_year,
        "end_year": end_year,
        "month": args.month,
        "months_total": len(tasks),
        "started_at": now_ts(),
        "updated_at": now_ts(),
    })
    write_json_atomic(paths["crawl_status_path"], status)
    logger.info("crawl worker 启动: %s-%s month=%s no_ai=%s", start_year, end_year, args.month, args.no_ai)

    try:
        for year, month in tasks:
            if _stop_requested:
                status["stopped_reason"] = "signal"
                break

            status["current_year"] = year
            status["current_month"] = month
            status["updated_at"] = now_ts()
            write_json_atomic(paths["crawl_status_path"], status)

            try:
                ms = dedup_month(
                    year, month,
                    use_ai=not args.no_ai,
                    reconcile=not args.no_reconcile,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("dedup_month %s-%s 异常: %s\n%s", year, month, e, traceback.format_exc())
                ms = {
                    "year": year, "month": month, "raw_fetched": 0, "groups": 0,
                    "anchor_groups": 0, "ai_calls": 0, "cache_hits": 0,
                    "inserted": 0, "dup_logged": 0,
                    "errors": [str(e)], "inserted_names": [], "dup_examples": [],
                }

            rec = ms.get("reconcile") or {}
            rec_merged = 0
            rec_editions = 0
            rec_ai = 0
            rec_skipped = 0
            if rec.get("skipped"):
                rec_skipped = 1
            else:
                rec_merged = sum(
                    len(e.get("merged") or []) for e in (rec.get("executed") or [])
                    if not e.get("error")
                )
                rec_editions = len(rec.get("editions") or [])
                rec_ai = int(rec.get("ai_calls") or 0)

            logger.info(
                "%s-%02d: raw=%s groups=%s inserted=%s dup=%s ai=%s cache=%s"
                " rec_merged=%s rec_editions=%s rec_ai=%s rec_skip=%s err=%s",
                year, month, ms.get("raw_fetched"), ms.get("groups"),
                ms.get("inserted"), ms.get("dup_logged"),
                ms.get("ai_calls"), ms.get("cache_hits"),
                rec_merged, rec_editions, rec_ai, rec_skipped, ms.get("errors"),
            )

            status["months_done"] += 1
            status["raw_fetched"] += int(ms.get("raw_fetched") or 0)
            status["inserted"] += int(ms.get("inserted") or 0)
            status["dup_logged"] += int(ms.get("dup_logged") or 0)
            status["ai_calls"] += int(ms.get("ai_calls") or 0)
            status["cache_hits"] += int(ms.get("cache_hits") or 0)
            status["reconcile_merged"] += rec_merged
            status["reconcile_editions"] += rec_editions
            status["reconcile_skipped"] += rec_skipped
            status["reconcile_ai_calls"] += rec_ai
            for err in ms.get("errors") or []:
                if err not in status["errors"]:
                    status["errors"].append(err)
            for err in rec.get("errors") or []:
                if err not in status["errors"]:
                    status["errors"].append(err)
            status["month_stats"].append({
                k: ms.get(k)
                for k in ("year", "month", "raw_fetched", "groups", "anchor_groups",
                          "ai_calls", "cache_hits", "inserted", "dup_logged", "errors")
            } | {
                "reconcile_merged": rec_merged,
                "reconcile_editions": rec_editions,
                "reconcile_ai_calls": rec_ai,
                "reconcile_skipped": bool(rec_skipped),
            })
            status["updated_at"] = now_ts()
            write_json_atomic(paths["crawl_status_path"], status)

        if not _stop_requested:
            status["stopped_reason"] = None
    except Exception as e:  # noqa: BLE001
        status["stopped_reason"] = f"fatal: {e}"
        logger.error("crawl worker fatal: %s", traceback.format_exc())
    finally:
        status["running"] = False
        status["updated_at"] = now_ts()
        write_json_atomic(paths["crawl_status_path"], status)
        logger.info("crawl worker 结束: reason=%s", status.get("stopped_reason"))


if __name__ == "__main__":
    main()
