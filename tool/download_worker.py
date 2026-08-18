import argparse
import logging
import os
import signal
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import tool
from tool.runtime import cleanup_old_logs, daily_log_path, now_ts, runtime_paths, write_json_atomic


_stop_requested = False


def _handle_stop(signum, frame):
    global _stop_requested
    _stop_requested = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, default=0)
    args = parser.parse_args()

    paths = runtime_paths()
    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)
    if paths.get("log_auto_cleanup"):
        cleanup_old_logs(retention_days=paths.get("log_retention_days"))

    logging.basicConfig(
        filename=daily_log_path("download"),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    months = list(range(1, 13)) if args.month in (0, None) else [int(args.month)]

    status = {
        "running": True,
        "pid": os.getpid(),
        "year": int(args.year),
        "month": int(args.month),
        "current_month": None,
        "finished_months": 0,
        "total_months": len(months),
        "started_at": now_ts(),
        "updated_at": now_ts(),
        "message": None,
        "stopped_reason": None,
    }
    write_json_atomic(paths["download_status_path"], status)

    try:
        success_all = True
        for m in months:
            if _stop_requested:
                status["stopped_reason"] = "signal"
                success_all = False
                break

            status["current_month"] = m
            status["updated_at"] = now_ts()
            write_json_atomic(paths["download_status_path"], status)

            ok = tool.download_games_by_month(int(args.year), m)
            success_all = success_all and bool(ok)

            status["finished_months"] += 1
            status["message"] = "success" if ok else "failed"
            status["updated_at"] = now_ts()
            write_json_atomic(paths["download_status_path"], status)

        status["running"] = False
        status["message"] = "success" if success_all else (status["stopped_reason"] or "failed")
        status["updated_at"] = now_ts()
        write_json_atomic(paths["download_status_path"], status)
    except Exception:
        status["running"] = False
        status["stopped_reason"] = "error"
        status["message"] = "failed"
        status["error"] = traceback.format_exc()
        status["updated_at"] = now_ts()
        write_json_atomic(paths["download_status_path"], status)
        raise


if __name__ == "__main__":
    main()
