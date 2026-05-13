import argparse
import logging
import os
import signal
import sqlite3

import tool
from runtime import now_ts, pid_is_running, runtime_paths, write_json_atomic


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
    os.makedirs(os.path.dirname(paths["log_path"]) or ".", exist_ok=True)

    logging.basicConfig(
        filename=paths["log_path"],
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
        "current_year": start_year,
        "current_month": None,
        "current_game": None,
        "start_year": start_year,
        "end_year": end_year,
        "started_at": now_ts(),
        "updated_at": now_ts(),
        "stopped_reason": None,
    }
    write_json_atomic(paths["spider_status_path"], status)

    total_months = (end_year - start_year + 1) * 12
    done_months = 0

    conn = sqlite3.connect(paths["db_path"])
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
                status["updated_at"] = now_ts()
                write_json_atomic(paths["spider_status_path"], status)

                games = tool.get_getchu_games(year, month)

                for idx, game in enumerate(games):
                    if _stop_requested:
                        status["stopped_reason"] = "signal"
                        break

                    cursor.execute(
                        "INSERT OR IGNORE INTO getchu_games (date, name, company) VALUES (?,?,?)",
                        (game.date, game.name, game.company),
                    )

                    if idx % 10 == 0 or idx == len(games) - 1:
                        status["current_game"] = f"{game.name} ({idx + 1}/{len(games)})"
                        status["updated_at"] = now_ts()
                        write_json_atomic(paths["spider_status_path"], status)

                conn.commit()

                done_months += 1
                status["progress"] = round(done_months / total_months * 100, 2)
                status["updated_at"] = now_ts()
                write_json_atomic(paths["spider_status_path"], status)

            if _stop_requested:
                break

        status["running"] = False
        status["updated_at"] = now_ts()
        write_json_atomic(paths["spider_status_path"], status)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

