import argparse
import logging
import os
import signal
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import tool
from tool.runtime import cleanup_old_logs, daily_log_path, now_ts, runtime_paths, write_json_atomic
from tool.getchu_detail import batch_update_details, get_pending_games


logger = logging.getLogger(__name__)
_stop_requested = False


def _handle_stop(signum, frame):
    global _stop_requested
    _stop_requested = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    args = parser.parse_args()

    paths = runtime_paths()
    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)
    if paths.get("log_auto_cleanup"):
        cleanup_old_logs(retention_days=paths.get("log_retention_days"))

    logging.basicConfig(
        filename=daily_log_path("spider"),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    start_year = args.start_year
    end_year = args.end_year
    if start_year > end_year:
        start_year, end_year = end_year, start_year

    pid = os.getpid()
    status = {
        "running": True,
        "pid": pid,
        "progress": 0.0,
        "phase": "listing",  # listing | detail
        "current_year": start_year,
        "current_month": None,
        "current_game": None,
        "start_year": start_year,
        "end_year": end_year,
        "detail_done": 0,
        "detail_total": 0,
        "detail_failed": 0,
        "started_at": now_ts(),
        "updated_at": now_ts(),
        "stopped_reason": None,
    }
    write_json_atomic(paths["spider_status_path"], status)

    try:
        total_months = (end_year - start_year + 1) * 12
        done_months = 0

        conn = tool.open_db(db_path=paths["db_path"])
        try:
            tool.ensure_getchu_schema(conn)
            cursor = conn.cursor()

            for year in range(start_year, end_year + 1):
                if _stop_requested:
                    status["stopped_reason"] = "signal"
                    break

                status["current_year"] = year
                for month in range(1, 13):
                    if _stop_requested:
                        status["stopped_reason"] = "signal"
                        break

                    status["current_month"] = month
                    status["current_game"] = None
                    status["current_month_fetched"] = 0
                    status["current_month_inserted"] = 0
                    status["current_month_skipped"] = 0
                    status["updated_at"] = now_ts()
                    write_json_atomic(paths["spider_status_path"], status)

                    games = tool.get_getchu_games(year, month)
                    status["current_month_fetched"] = len(games)
                    status["updated_at"] = now_ts()
                    write_json_atomic(paths["spider_status_path"], status)

                    inserted = 0
                    skipped = 0

                    for idx, game in enumerate(games):
                        if _stop_requested:
                            status["stopped_reason"] = "signal"
                            break

                        # 统一入库：保存 getchu_id/thumb_url/price/detail_url/release_date 等扩展字段
                        if tool.upsert_getchu_game(cursor, game):
                            inserted += 1
                        else:
                            skipped += 1

                        if idx % 10 == 0 or idx == len(games) - 1:
                            status["current_game"] = f"{game.name} ({idx + 1}/{len(games)})"
                            status["current_month_inserted"] = inserted
                            status["current_month_skipped"] = skipped
                            status["updated_at"] = now_ts()
                            write_json_atomic(paths["spider_status_path"], status)

                    conn.commit()

                    done_months += 1
                    status["progress"] = round(done_months / total_months * 100, 2)
                    status["current_month_inserted"] = inserted
                    status["current_month_skipped"] = skipped
                    status["updated_at"] = now_ts()
                    write_json_atomic(paths["spider_status_path"], status)

                if _stop_requested:
                    break

                # === 阶段2：详情补全 ===
                if not _stop_requested:
                    logger.info("📋 列表抓取完成，开始详情补全阶段...")
                    status["phase"] = "detail"
                    status["updated_at"] = now_ts()
                    write_json_atomic(paths["spider_status_path"], status)
                    
                    # 获取待处理的游戏列表
                    pending_games = get_pending_games(conn, limit=10000)
                    total_pending = len(pending_games)
                    status["detail_total"] = total_pending
                    status["detail_done"] = 0
                    status["detail_failed"] = 0
                    status["updated_at"] = now_ts()
                    write_json_atomic(paths["spider_status_path"], status)
                    
                    logger.info("📋 待处理游戏数量: %d", total_pending)
                    
                    if total_pending > 0:
                        # 分批处理（每批100个，减少内存占用）
                        batch_size = 100
                        for batch_start in range(0, total_pending, batch_size):
                            if _stop_requested:
                                status["stopped_reason"] = "signal"
                                break
                            
                            batch_end = min(batch_start + batch_size, total_pending)
                            batch_games = pending_games[batch_start:batch_end]
                            
                            logger.info("📋 处理批次 %d-%d/%d", 
                                      batch_start + 1, batch_end, total_pending)
                            
                            # 执行详情补全
                            stats = batch_update_details(
                                batch_games, conn, 
                                start_time=status["started_at"]
                            )
                            
                            # 更新统计
                            status["detail_done"] += stats["success"] + stats["skipped"]
                            status["detail_failed"] += stats["failed"] + stats["retry_failed"]
                            status["progress"] = round(
                                (done_months * 12 + status["detail_done"]) / (total_months * 12 + total_pending) * 100, 2
                            )
                            status["updated_at"] = now_ts()
                            write_json_atomic(paths["spider_status_path"], status)
                            
                            logger.info("📋 批次完成: 成功:%d 失败:%d 跳过:%d 重试:%d", 
                                      stats["success"], stats["failed"], stats["skipped"], stats["retried"])
                            
                            # 检查是否还有待处理的
                            remaining = get_pending_games(conn, limit=1)
                            if not remaining:
                                logger.info("🎉 所有游戏详情补全完成！")
                                break
                    
                    logger.info("🎉 详情补全阶段完成")
                
                conn.commit()
        finally:
            conn.close()
    except Exception:
        status["running"] = False
        status["stopped_reason"] = "error"
        status["error"] = traceback.format_exc()
        status["updated_at"] = now_ts()
        write_json_atomic(paths["spider_status_path"], status)
        raise

    status["running"] = False
    status["updated_at"] = now_ts()
    write_json_atomic(paths["spider_status_path"], status)


if __name__ == "__main__":
    main()
