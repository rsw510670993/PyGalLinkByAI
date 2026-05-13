import argparse
import json
import os
import signal
import subprocess
import sys

import tool
from runtime import pid_is_running, read_json, runtime_paths, terminate_pid, write_json_atomic


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def _base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _spider_status_default():
    return {
        "running": False,
        "pid": None,
        "progress": 0.0,
        "current_year": None,
        "current_month": None,
        "current_game": None,
        "start_year": None,
        "end_year": None,
        "started_at": None,
        "updated_at": None,
        "stopped_reason": None,
    }


def _download_status_default():
    return {
        "running": False,
        "pid": None,
        "year": None,
        "month": None,
        "current_month": None,
        "finished_months": 0,
        "total_months": 0,
        "started_at": None,
        "updated_at": None,
        "message": None,
        "stopped_reason": None,
    }


def cmd_years(args):
    years = tool.get_years_list()
    _print({"years": years})


def cmd_games(args):
    paths = runtime_paths()
    config = paths["config"]
    per_page = int(config.get("per_page", 50))
    page = int(args.page)
    if page < 1:
        page = 1

    year = int(args.year) if args.year is not None else None
    month = int(args.month) if args.month is not None else None

    all_games = tool.get_games_data()
    if year:
        all_games = [g for g in all_games if g.year == year]
    if month:
        all_games = [g for g in all_games if g.month == month]

    total = len(all_games)
    start = (page - 1) * per_page
    end = start + per_page
    games_data = all_games[start:end]

    _print(
        {
            "data": [
                {
                    "year": g.year,
                    "month": g.month,
                    "name": g.name,
                    "company": g.company,
                    "download_url": g.link,
                    "nyaa_name": g.nyaa_name,
                    "comment": g.comment,
                }
                for g in games_data
            ],
            "current_page": page,
            "per_page": per_page,
            "total": total,
        }
    )


def cmd_spider_status(args):
    paths = runtime_paths()
    status = read_json(paths["spider_status_path"], _spider_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and not pid_is_running(int(pid)):
        status["running"] = False
        status["stopped_reason"] = "not_running"
        write_json_atomic(paths["spider_status_path"], status)
    _print(status)


def cmd_spider_start(args):
    paths = runtime_paths()
    status = read_json(paths["spider_status_path"], _spider_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and pid_is_running(int(pid)):
        _print({"status": "error", "message": "爬虫已在运行中", "pid": int(pid)})
        return

    start_year = int(args.start_year)
    end_year = int(args.end_year)
    if start_year > end_year:
        start_year, end_year = end_year, start_year

    p = subprocess.Popen(
        [
            sys.executable,
            os.path.join(_base_dir(), "spider_worker.py"),
            "--start-year",
            str(start_year),
            "--end-year",
            str(end_year),
        ],
        cwd=_base_dir(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _print({"status": "success", "message": "爬虫已启动", "pid": p.pid})


def cmd_spider_stop(args):
    paths = runtime_paths()
    status = read_json(paths["spider_status_path"], _spider_status_default())
    pid = status.get("pid")
    if not pid:
        _print({"status": "error", "message": "未找到运行中的爬虫"})
        return
    try:
        if pid_is_running(int(pid)):
            terminate_pid(int(pid), signal.SIGTERM)
        status["running"] = False
        status["stopped_reason"] = "stopped"
        write_json_atomic(paths["spider_status_path"], status)
        _print({"status": "success", "message": "停止请求已发送", "pid": int(pid)})
    except Exception as e:
        _print({"status": "error", "message": str(e), "pid": int(pid)})


def cmd_download_status(args):
    paths = runtime_paths()
    status = read_json(paths["download_status_path"], _download_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and not pid_is_running(int(pid)):
        status["running"] = False
        status["stopped_reason"] = "not_running"
        write_json_atomic(paths["download_status_path"], status)
    _print(status)


def cmd_download_start(args):
    paths = runtime_paths()
    status = read_json(paths["download_status_path"], _download_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and pid_is_running(int(pid)):
        _print({"status": "error", "message": "下载任务已在运行中", "pid": int(pid)})
        return

    year = int(args.year)
    month = int(args.month) if args.month is not None else 0

    p = subprocess.Popen(
        [
            sys.executable,
            os.path.join(_base_dir(), "download_worker.py"),
            "--year",
            str(year),
            "--month",
            str(month),
        ],
        cwd=_base_dir(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _print({"status": "success", "message": "下载任务已启动", "pid": p.pid})


def cmd_download_stop(args):
    paths = runtime_paths()
    status = read_json(paths["download_status_path"], _download_status_default())
    pid = status.get("pid")
    if not pid:
        _print({"status": "error", "message": "未找到运行中的下载任务"})
        return
    try:
        if pid_is_running(int(pid)):
            terminate_pid(int(pid), signal.SIGTERM)
        status["running"] = False
        status["stopped_reason"] = "stopped"
        write_json_atomic(paths["download_status_path"], status)
        _print({"status": "success", "message": "停止请求已发送", "pid": int(pid)})
    except Exception as e:
        _print({"status": "error", "message": str(e), "pid": int(pid)})


def build_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_years = sub.add_parser("years")
    p_years.set_defaults(func=cmd_years)

    p_games = sub.add_parser("games")
    p_games.add_argument("--page", type=int, default=1)
    p_games.add_argument("--year", type=int)
    p_games.add_argument("--month", type=int)
    p_games.set_defaults(func=cmd_games)

    p_spider = sub.add_parser("spider")
    spider_sub = p_spider.add_subparsers(dest="action", required=True)

    p_spider_status = spider_sub.add_parser("status")
    p_spider_status.set_defaults(func=cmd_spider_status)

    p_spider_start = spider_sub.add_parser("start")
    p_spider_start.add_argument("--start-year", type=int, required=True)
    p_spider_start.add_argument("--end-year", type=int, required=True)
    p_spider_start.set_defaults(func=cmd_spider_start)

    p_spider_stop = spider_sub.add_parser("stop")
    p_spider_stop.set_defaults(func=cmd_spider_stop)

    p_download = sub.add_parser("download")
    download_sub = p_download.add_subparsers(dest="action", required=True)

    p_download_status = download_sub.add_parser("status")
    p_download_status.set_defaults(func=cmd_download_status)

    p_download_start = download_sub.add_parser("start")
    p_download_start.add_argument("--year", type=int, required=True)
    p_download_start.add_argument("--month", type=int)
    p_download_start.set_defaults(func=cmd_download_start)

    p_download_stop = download_sub.add_parser("stop")
    p_download_stop.set_defaults(func=cmd_download_stop)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

