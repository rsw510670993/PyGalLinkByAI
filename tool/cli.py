import argparse
import json
import os
import signal
import subprocess
import sys
import time
import threading
import concurrent.futures as cf
import multiprocessing as mp

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import tool
from tool.runtime import cleanup_old_logs, daily_log_path, now_ts, pid_is_running, read_json, runtime_paths, terminate_pid, write_json_atomic


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


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
    if args.source == "egs":
        from tool.egs_core import open_egs_db
        conn = open_egs_db()
        try:
            years = [int(row[0]) for row in conn.execute("SELECT DISTINCT substr(date,1,4) FROM egs_games ORDER BY 1 DESC")]
        finally:
            conn.close()
    else:
        years = tool.get_years_list()
    _print({"years": years})


def cmd_calendar(args):
    from datetime import datetime
    import tool.core

    paths = runtime_paths()
    base_year = int(args.year) if getattr(args, "year", None) else datetime.now().year
    start_year = base_year - 2
    end_year = base_year

    from tool.egs_core import open_egs_db
    conn = open_egs_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            substr(date, 1, 4) as year,
            CAST(substr(date, 6, 2) AS INTEGER) as month,
            COUNT(*) as total,
            SUM(CASE WHEN link IS NOT NULL AND link != '' THEN 1 ELSE 0 END) as magnet_total,
            SUM(CASE WHEN link IS NOT NULL AND link != '' AND COALESCE(downloaded, 0) = 1 THEN 1 ELSE 0 END) as magnet_downloaded,
            SUM(CASE WHEN link IS NOT NULL AND link != '' AND COALESCE(submitted_115, 0) = 1 THEN 1 ELSE 0 END) as magnet_submitted
        FROM egs_games
        WHERE CAST(substr(date, 1, 4) AS INTEGER) BETWEEN ? AND ?
        GROUP BY year, month
        ORDER BY year DESC, month DESC
        """,
        (int(start_year), int(end_year)),
    )
    rows = cursor.fetchall()
    conn.close()

    stats = {}
    for y, m, total, magnet_total, magnet_downloaded, magnet_submitted in rows:
        yy = int(y)
        mm = int(m)
        total = int(total or 0)
        magnet_total = int(magnet_total or 0)
        magnet_downloaded = int(magnet_downloaded or 0)
        magnet_submitted = int(magnet_submitted or 0)
        stats[(yy, mm)] = {
            "has_data": total > 0,
            "total": total,
            "magnet_total": magnet_total,
            "magnet_downloaded": magnet_downloaded,
            "magnet_submitted": magnet_submitted,
            "all_magnet_downloaded": bool(magnet_total > 0 and magnet_total == magnet_downloaded),
            "all_magnet_submitted": bool(magnet_total > 0 and magnet_total == magnet_submitted),
        }

    years = []
    for y in range(end_year, start_year - 1, -1):
        months = []
        for m in range(1, 13):
            st = stats.get((y, m)) or {
                "has_data": False,
                "total": 0,
                "magnet_total": 0,
                "magnet_downloaded": 0,
                "magnet_submitted": 0,
                "all_magnet_downloaded": False,
                "all_magnet_submitted": False,
            }
            months.append({"month": m, **st})
        years.append({"year": y, "months": months})

    _print({"base_year": base_year, "start_year": start_year, "end_year": end_year, "years": years})


def cmd_latest_month(args):
    games = tool.get_games_data()
    if not games:
        _print({"year": None, "month": None})
        return
    g = games[0]
    _print({"year": int(g.year), "month": int(g.month)})


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

    month_stats = None
    if year and month:
        magnet_games = [g for g in all_games if getattr(g, "link", None)]
        magnet_total = len(magnet_games)
        magnet_downloaded = sum(1 for g in magnet_games if int(getattr(g, "downloaded", 0) or 0) == 1)
        month_stats = {
            "magnet_total": magnet_total,
            "magnet_downloaded": magnet_downloaded,
            "all_magnet_downloaded": bool(magnet_total > 0 and magnet_total == magnet_downloaded),
        }

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
                    "downloaded": g.downloaded,
                    "submitted_115": getattr(g, "submitted_115", 0),
                }
                for g in games_data
            ],
            "current_page": page,
            "per_page": per_page,
            "total": total,
            "month_stats": month_stats,
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

    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)
    if paths.get("log_auto_cleanup"):
        cleanup_old_logs(retention_days=paths.get("log_retention_days"))

    spider_launch_log = daily_log_path("spider_launch")
    launch_fp = open(spider_launch_log, "ab", buffering=0)

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
        stdout=launch_fp,
        stderr=launch_fp,
        start_new_session=True,
    )
    started = False
    for _ in range(10):
        time.sleep(0.2)
        status = read_json(paths["spider_status_path"], _spider_status_default())
        if status.get("pid") == p.pid:
            started = True
            break

    if not started:
        try:
            if pid_is_running(int(p.pid)):
                terminate_pid(int(p.pid), signal.SIGTERM)
        except Exception:
            pass
        _print(
            {
                "status": "error",
                "message": "爬虫启动失败，请检查写权限（status/logs/getchu.db）与Python依赖是否已安装",
            }
        )
        return

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

    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)
    if paths.get("log_auto_cleanup"):
        cleanup_old_logs(retention_days=paths.get("log_retention_days"))

    download_launch_log = daily_log_path("download_launch")
    launch_fp = open(download_launch_log, "ab", buffering=0)

    p = subprocess.Popen(
        [
            sys.executable,
            os.path.join(_base_dir(), "download_worker.py"),
            "--year",
            str(year),
            "--month",
            str(month),
            "--source", args.source,
        ],
        cwd=_base_dir(),
        stdout=launch_fp,
        stderr=launch_fp,
        start_new_session=True,
    )
    started = False
    for _ in range(10):
        time.sleep(0.2)
        status = read_json(paths["download_status_path"], _download_status_default())
        if status.get("pid") == p.pid:
            started = True
            break

    if not started:
        try:
            if pid_is_running(int(p.pid)):
                terminate_pid(int(p.pid), signal.SIGTERM)
        except Exception:
            pass
        _print(
            {
                "status": "error",
                "message": "下载任务启动失败，请检查写权限（status/logs/getchu.db）与Python依赖是否已安装",
            }
        )
        return

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


def cmd_115_login_qrcode(args):
    try:
        from tool.p115_client import qr_login_step1
        _print(qr_login_step1())
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def cmd_115_login_qrcode_status(args):
    try:
        from tool.p115_client import qr_login_step2
        uid = args.uid
        time = args.time
        sign = args.sign
        _print(qr_login_step2(uid, time, sign))
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def cmd_115_login_confirm(args):
    try:
        from tool.p115_client import qr_login_step3
        uid = args.uid
        app = args.app or "alipaymini"
        _print(qr_login_step3(uid, app))
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def cmd_115_logout(args):
    try:
        from tool.p115_client import cookies_path
        path = cookies_path()
        if os.path.isfile(path):
            os.remove(path)
        _print({"success": True, "message": "已退出登录"})
    except Exception as e:
        _print({"success": False, "message": str(e)})


def cmd_115_login_status(args):
    try:
        from tool.p115_client import get_login_status
        _print(get_login_status())
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def cmd_115_check(args):
    try:
        from tool.p115_client import check_magnet_exists
        magnet = args.magnet
        save_path = args.dir or ""
        _print(check_magnet_exists(magnet, save_path, debug=bool(getattr(args, "debug", False))))
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def cmd_115_submit(args):
    try:
        from tool.p115_client import offline_submit
        magnet = args.magnet
        save_path = args.dir or ""
        _print(offline_submit(magnet, save_path))
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def _check_all_status_default():
    return {
        "running": False,
        "pid": None,
        "year": None,
        "month": None,
        "total": 0,
        "checked": 0,
        "found_downloaded": 0,
        "errors": [],
        "current": None,
        "started_at": None,
        "updated_at": None,
        "stopped_reason": None,
    }


def _check_magnet_exists_child(link, q):
    try:
        from tool.p115_client import check_magnet_exists

        res = check_magnet_exists(link, "")
        q.put({"ok": True, "res": res})
    except Exception as e:
        q.put({"ok": False, "error": str(e)})


def _check_magnet_exists_with_timeout(link, timeout_s=60):
    if os.name != "nt":
        try:
            ctx = mp.get_context("fork")
        except Exception:
            ctx = mp.get_context("spawn")

        q = ctx.Queue(maxsize=1)
        p = ctx.Process(target=_check_magnet_exists_child, args=(link, q))
        p.daemon = True
        p.start()
        p.join(timeout_s)
        if p.is_alive():
            try:
                p.terminate()
            except Exception:
                pass
            p.join(5)
            return None, "timeout"
        try:
            msg = q.get_nowait()
        except Exception:
            return None, "no_result"
        if not isinstance(msg, dict):
            return None, "bad_result"
        if msg.get("ok") is True:
            return msg.get("res"), None
        return None, msg.get("error") or "error"

    def _run():
        from tool.p115_client import check_magnet_exists

        return check_magnet_exists(link, "")

    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_run)
        try:
            res = fut.result(timeout=timeout_s)
            return res, None
        except cf.TimeoutError:
            return None, "timeout"
        except Exception as e:
            return None, str(e)
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)



def cmd_115_check_all_start(args):
    paths = runtime_paths()
    status = read_json(paths["check_all_status_path"], _check_all_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and pid_is_running(int(pid)):
        _print({"status": "error", "message": "校验任务已在运行中"})
        return

    if not os.path.isdir(paths["status_dir"]):
        os.makedirs(paths["status_dir"], exist_ok=True)

    worker_args = [
        sys.executable,
        os.path.join(_base_dir(), "cli.py"),
        "115", "check_all", "worker", "--source", args.source,
    ]
    if args.year:
        worker_args += ["--year", str(args.year)]
    if args.month:
        worker_args += ["--month", str(args.month)]

    p = subprocess.Popen(
        worker_args,
        cwd=_base_dir(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    time.sleep(0.5)
    if pid_is_running(p.pid):
        _print({"status": "success", "message": "校验任务已启动", "pid": p.pid})
    else:
        _print({"status": "error", "message": "校验任务启动失败"})


def cmd_115_check_all_status(args):
    paths = runtime_paths()
    status = read_json(paths["check_all_status_path"], _check_all_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and not pid_is_running(int(pid)):
        status["running"] = False
        write_json_atomic(paths["check_all_status_path"], status)
    _print(status)


def cmd_115_check_all_stop(args):
    paths = runtime_paths()
    status = read_json(paths["check_all_status_path"], _check_all_status_default())
    pid = status.get("pid")
    if pid and pid_is_running(int(pid)):
        try:
            terminate_pid(int(pid))
        except Exception as e:
            _print({"status": "error", "message": f"停止失败: {e}"})
            return
    status["running"] = False
    status["pid"] = None
    status["stopped_reason"] = "manual_stop"
    status["updated_at"] = now_ts()
    write_json_atomic(paths["check_all_status_path"], status)
    _print({"status": "success", "message": "已停止校验任务"})


def cmd_update_game(args):
    import tool.core
    kwargs = {"date": args.date, "name": args.old_name}
    if args.new_date:
        kwargs["new_date"] = args.new_date
    if args.new_name:
        kwargs["new_name"] = args.new_name
    if args.new_company:
        kwargs["new_company"] = args.new_company
    if args.new_link is not None:
        kwargs["new_link"] = args.new_link
    if args.new_downloaded is not None:
        kwargs["new_downloaded"] = args.new_downloaded
    if args.new_nyaa_name is not None:
        kwargs["new_nyaa_name"] = args.new_nyaa_name
    if getattr(args, "new_submitted_115", None) is not None:
        kwargs["new_submitted_115"] = args.new_submitted_115
    if getattr(args, "new_submitted_pick_code", None) is not None:
        kwargs["new_submitted_pick_code"] = args.new_submitted_pick_code
    ok = tool.core.update_game_record(**kwargs)
    _print({"success": ok, "message": "更新成功" if ok else "未找到匹配记录"})


def cmd_delete_game(args):
    import tool.core
    ok = tool.core.delete_game_record(args.date, args.name)
    _print({"success": ok, "message": "删除成功" if ok else "未找到匹配记录"})



def cmd_115_check_all_worker(args):
    import tool.core

    paths = runtime_paths()
    status_path = paths["check_all_status_path"]

    status = {
        "running": True,
        "source": args.source,
        "pid": os.getpid(),
        "year": int(args.year) if getattr(args, "year", None) else None,
        "month": int(args.month) if getattr(args, "month", None) else None,
        "total": 0,
        "checked": 0,
        "found_downloaded": 0,
        "current": None,
        "errors": [],
        "started_at": now_ts(),
        "stopped_reason": None,
        "updated_at": now_ts(),
    }
    write_json_atomic(status_path, status)

    is_egs = args.source == "egs"
    if is_egs:
        from tool.egs_core import open_egs_db
        conn = open_egs_db()
    else:
        conn = tool.core.open_db()
        tool.core.ensure_getchu_schema(conn)
    cursor = conn.cursor()
    base_sql = "SELECT date, name, link FROM getchu_games WHERE link IS NOT NULL AND link != '' AND COALESCE(downloaded, 0) = 0"
    if is_egs:
        base_sql = base_sql.replace("SELECT date, name, link FROM getchu_games", "SELECT date, name, link, egs_id FROM egs_games")
    sql_params = []
    if args.year and args.month:
        base_sql += " AND substr(date, 1, 4) = ? AND CAST(substr(date, 6) AS INTEGER) = ?"
        sql_params = [str(args.year), int(args.month)]
    elif args.year:
        base_sql += " AND substr(date, 1, 4) = ?"
        sql_params = [str(args.year)]
    cursor.execute(base_sql, sql_params)
    rows = cursor.fetchall()
    conn.close()

    status["total"] = len(rows)
    write_json_atomic(status_path, status)

    stop_event = threading.Event()

    def _heartbeat():
        while not stop_event.is_set():
            try:
                status["updated_at"] = now_ts()
                write_json_atomic(status_path, status)
            except Exception:
                pass
            stop_event.wait(3)

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()

    for row in rows:
        date, name, link = tuple(row)[:3]
        try:
            status["current"] = {"date": date, "name": name}
            status["checked"] += 1
            status["updated_at"] = now_ts()
            write_json_atomic(status_path, status)
            result, err = _check_magnet_exists_with_timeout(link, 60)
            if err:
                status["errors"].append(f"{date}/{name}: {err}")
            elif isinstance(result, dict) and result.get("exists"):
                if is_egs:
                    conn = open_egs_db()
                    try:
                        conn.execute("UPDATE egs_games SET downloaded = 1, infohash_hex = ?, updated_at = ? WHERE egs_id = ? AND link = ?", (result.get("infohash_hex"), now_ts(), row[3], link))
                        conn.commit()
                    finally:
                        conn.close()
                else:
                    tool.core.set_downloaded_status(date, name, 1, result.get("infohash_hex"))
                status["found_downloaded"] += 1
        except Exception as e:
            status["errors"].append(f"{date}/{name}: {e}")

        status["updated_at"] = now_ts()
        write_json_atomic(status_path, status)

    stop_event.set()
    status["running"] = False
    status["current"] = None
    write_json_atomic(status_path, status)

    _print({
        "success": True,
        "total": status["total"],
        "checked": status["checked"],
        "found_downloaded": status["found_downloaded"],
        "errors": status["errors"][:10],
    })


def _config_int(config, key, default):
    v = config.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default


def _in_idle_window(config):
    from datetime import datetime

    tz_name = config.get("idle_timezone") or "Asia/Tokyo"
    start_hour = _config_int(config, "idle_start_hour", 0)
    end_hour = _config_int(config, "idle_end_hour", 9)
    now = None
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()
        tz_name = "local"

    ok = start_hour <= int(now.hour) < end_hour
    return ok, now, tz_name, start_hour, end_hour


def _is_running_status(status):
    pid = status.get("pid")
    if not status.get("running") or not pid:
        return False
    try:
        return pid_is_running(int(pid))
    except Exception:
        return False


def _check_year_downloaded(year):
    import tool.core

    conn = tool.core.open_db()
    tool.core.ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT date, name, link FROM getchu_games WHERE substr(date, 1, 4) = ? AND link IS NOT NULL AND link != '' AND COALESCE(downloaded, 0) = 0",
        (str(year),),
    )
    rows = cursor.fetchall()
    conn.close()

    total = len(rows)
    checked = 0
    found_downloaded = 0
    errors = []
    for date, name, link in rows:
        checked += 1
        try:
            result, err = _check_magnet_exists_with_timeout(link, 60)
            if err:
                errors.append(f"{date}/{name}: {err}")
                continue
            if isinstance(result, dict) and result.get("exists"):
                tool.core.set_downloaded_status(date, name, 1, result.get("infohash_hex"))
                found_downloaded += 1
        except Exception as e:
            errors.append(f"{date}/{name}: {e}")
    return {
        "success": True,
        "total": total,
        "checked": checked,
        "found_downloaded": found_downloaded,
        "errors": errors[:10],
    }


def cmd_auto_idle_run(args):
    from datetime import datetime

    paths = runtime_paths()
    config = paths["config"]

    ok, now, tz_name, start_hour, end_hour = _in_idle_window(config)
    if not ok and not getattr(args, "force", False):
        _print(
            {
                "skipped": True,
                "reason": "not_idle_time",
                "timezone": tz_name,
                "idle_start_hour": start_hour,
                "idle_end_hour": end_hour,
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        return

    spider_status = read_json(paths["spider_status_path"], _spider_status_default())
    download_status = read_json(paths["download_status_path"], _download_status_default())
    check_all_status = read_json(paths["check_all_status_path"], _check_all_status_default())
    if _is_running_status(spider_status) or _is_running_status(download_status) or _is_running_status(check_all_status):
        _print({"skipped": True, "reason": "busy"})
        return

    idle_status_path = os.path.join(paths["status_dir"], "idle_run_status.json")
    idle_status = read_json(idle_status_path, {"last_run_week": None, "last_run_at": None})
    try:
        iso = now.isocalendar()
        week_key = f"{int(iso.year)}-W{int(iso.week):02d}"
    except Exception:
        week_key = None
    if week_key and idle_status.get("last_run_week") == week_key and not getattr(args, "force", False):
        _print(
            {
                "skipped": True,
                "reason": "already_ran_this_week",
                "timezone": tz_name,
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
                "week": week_key,
                "last_run_at": idle_status.get("last_run_at"),
            }
        )
        return

    year = int(getattr(args, "year", None) or now.year)

    # 重新获取当前时间用于月份范围计算，避免与闲时窗口的 now 混淆
    try:
        from zoneinfo import ZoneInfo
        _now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        _now = datetime.now()

    default_end_month = _now.month - 1
    current_month = int(getattr(args, "end_month", None) or default_end_month)
    start_month = int(getattr(args, "start_month", None) or 1)
    if start_month < 1:
        start_month = 1
    if current_month > 12:
        current_month = 12
    if current_month < 1:
        _print(
            {
                "skipped": True,
                "reason": "no_months_to_process",
                "timezone": tz_name,
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
                "year": year,
                "start_month": start_month,
                "end_month": current_month,
            }
        )
        return
    if start_month > current_month:
        start_month = current_month

    getchu_ok = False
    try:
        getchu_ok = bool(tool.get_all_getchu_games(year, year, start_month, current_month))
    except Exception:
        getchu_ok = False

    download_results = []
    download_ok_all = True
    for m in range(start_month, current_month + 1):
        try:
            ok2 = bool(tool.download_games_by_month(year, m))
            download_ok_all = download_ok_all and ok2
            download_results.append({"month": m, "success": ok2})
        except Exception as e:
            download_ok_all = False
            download_results.append({"month": m, "success": False, "error": str(e)})

    login = {"logged_in": False, "user": None, "reason": "not_checked"}
    check_res = None
    try:
        from tool.p115_client import get_login_status

        login = get_login_status()
    except Exception as e:
        login = {"logged_in": False, "user": None, "reason": str(e)}

    if login.get("logged_in") is True:
        try:
            check_res = _check_year_downloaded(year)
        except Exception as e:
            check_res = {"success": False, "message": str(e)}

    if week_key:
        try:
            os.makedirs(paths["status_dir"], exist_ok=True)
            write_json_atomic(
                idle_status_path,
                {
                    "last_run_week": week_key,
                    "last_run_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "year": year,
                    "start_month": start_month,
                    "end_month": current_month,
                    "p115_logged_in": bool(login.get("logged_in") is True),
                },
            )
        except Exception:
            pass

    _print(
        {
            "success": True,
            "timezone": tz_name,
            "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            "year": year,
            "start_month": start_month,
            "end_month": current_month,
            "getchu": {"success": getchu_ok},
            "download_links": {"success": download_ok_all, "months": download_results},
            "p115_login": login,
            "p115_check": check_res,
        }
    )


def cmd_egs_crawl(args):
    from tool.egs_core import crawl_egs_range
    start_year = int(args.start_year)
    end_year = int(args.end_year or args.start_year)
    stats = crawl_egs_range(start_year, end_year, month=args.month, db_path=args.db)
    _print(stats)


def cmd_egs_status(args):
    from tool.egs_core import open_egs_db
    conn = open_egs_db(args.db)
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        if "egs_games" not in tables:
            _print({"exists": False, "tables": tables})
            return
        total = cur.execute("SELECT COUNT(*) FROM egs_games").fetchone()[0]
        by_year = cur.execute(
            "SELECT substr(date,1,4) AS year, COUNT(*) FROM egs_games GROUP BY year ORDER BY year"
        ).fetchall()
        by_month = cur.execute(
            "SELECT date, COUNT(*) FROM egs_games GROUP BY date ORDER BY date"
        ).fetchall()
        has_link = cur.execute(
            "SELECT COUNT(*) FROM egs_games WHERE link IS NOT NULL AND link != ''"
        ).fetchone()[0]
        _print({
            "exists": True,
            "db": conn.execute("SELECT file FROM pragma_database_list WHERE name='main'").fetchone()[0],
            "total": total,
            "by_year": [{"year": r[0], "count": r[1]} for r in by_year],
            "by_month": [{"month": r[0], "count": r[1]} for r in by_month],
            "has_link": has_link,
        })
    finally:
        conn.close()


def cmd_egs_games(args):
    from tool.egs_core import open_egs_db, ensure_review_blacklist_schema
    conn = open_egs_db(args.db)
    try:
        cur = conn.cursor()
        ensure_review_blacklist_schema(conn)
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='egs_games'")
        if cur.fetchone() is None:
            _print({"data": [], "current_page": int(args.page), "per_page": int(args.per_page),
                    "total": 0, "year": args.year, "month": args.month, "q": args.q})
            return

        conditions = []
        params = []
        if args.year is not None:
            conditions.append("substr(date,1,4) = ?")
            params.append(f"{int(args.year):04d}")
        if args.month is not None:
            conditions.append("substr(date,6,2) = ?")
            params.append(f"{int(args.month):02d}")
        if args.brand_kind:
            conditions.append("brand_kind = ?")
            params.append(str(args.brand_kind).strip().upper())
        if args.q:
            q = str(args.q).strip()
            if q:
                conditions.append("(name LIKE ? OR company LIKE ? OR name_kana LIKE ?)")
                like = f"%{q}%"
                params.extend([like, like, like])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = cur.execute(f"SELECT COUNT(*) FROM egs_games{where}", params).fetchone()[0]
        page = max(1, int(args.page))
        per_page = max(1, int(args.per_page))
        start = (page - 1) * per_page
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        review_select = ""
        if "egs_nyaa_candidates" in tables:
            review_select += """
            , (SELECT COUNT(*) FROM egs_nyaa_candidates c
                WHERE c.egs_id = egs_games.egs_id) AS candidate_count
            """
        if "egs_nyaa_search_log" in tables:
            log_cols = {r[1] for r in cur.execute("PRAGMA table_info(egs_nyaa_search_log)")}
            if "best_score" in log_cols:
                review_select += """
            , (SELECT l.best_score FROM egs_nyaa_search_log l
                WHERE l.egs_id = egs_games.egs_id) AS best_score
            """
            if "review_status" in log_cols:
                review_select += """
            , (SELECT l.review_status FROM egs_nyaa_search_log l
                WHERE l.egs_id = egs_games.egs_id) AS review_status
            """
        review_select += """
        , EXISTS (
              SELECT 1 FROM egs_review_company_blacklist b
               WHERE b.company IN (egs_games.company, egs_games.egs_company)
          ) AS review_blacklisted
        """
        rows = cur.execute(
            f"""
            SELECT egs_id, date, name, company, release_ts, brand_kind,
                   link, nyaa_name, downloaded, submitted_115, submitted_pick_code
                  {review_select}
              FROM egs_games{where}
             ORDER BY date, release_ts, egs_id
             LIMIT ? OFFSET ?
            """,
            params + [per_page, start],
        ).fetchall()
        _print({
            "data": [dict(r) for r in rows],
            "current_page": page,
            "per_page": per_page,
            "total": int(total or 0),
            "year": args.year,
            "month": args.month,
            "q": args.q or "",
        })
    finally:
        conn.close()


def cmd_egs_magnet(args):
    from tool.egs_magnet import run_magnet
    stats = run_magnet(
        year=int(args.year),
        month=int(args.month) if args.month else None,
        force=bool(args.force),
        limit=int(args.limit or 0),
        db_path=args.db,
    )
    _print(stats)


def cmd_egs_update(args):
    from tool.egs_core import update_egs_game_record
    _print(update_egs_game_record(
        egs_id=int(args.egs_id),
        new_date=args.new_date,
        new_name=args.new_name,
        new_company=args.new_company,
        new_link=args.new_link,
        new_nyaa_name=args.new_nyaa_name,
        new_downloaded=args.new_downloaded,
        new_submitted_115=args.new_submitted_115,
        new_submitted_pick_code=args.new_submitted_pick_code,
        db_path=args.db,
    ))


def cmd_egs_delete(args):
    from tool.egs_core import delete_egs_game_record
    _print(delete_egs_game_record(int(args.egs_id), db_path=args.db))


def cmd_egs_review_detail(args):
    from tool.egs_magnet import review_detail
    _print(review_detail(int(args.egs_id), db_path=args.db))


def cmd_egs_review_decide(args):
    from tool.egs_magnet import decide_review
    _print(decide_review(
        egs_id=int(args.egs_id),
        decision=args.decision,
        candidate_id=args.candidate_id,
        manual_magnet=args.manual_magnet,
        manual_nyaa_name=args.manual_nyaa_name,
        note=args.note,
        db_path=args.db,
    ))


def cmd_egs_review_blacklist(args):
    from tool.egs_core import (
        list_review_company_blacklist,
        add_review_company_blacklist,
        remove_review_company_blacklist,
    )
    if args.action == "add":
        _print(add_review_company_blacklist(args.company, args.note, db_path=args.db))
    elif args.action == "remove":
        _print(remove_review_company_blacklist(args.company, db_path=args.db))
    else:
        conn = None
        from tool.egs_core import open_egs_db, ensure_review_blacklist_schema
        conn = open_egs_db(args.db)
        try:
            ensure_review_blacklist_schema(conn)
            _print({"success": True, "data": list_review_company_blacklist(conn)})
        finally:
            if conn:
                conn.close()


def build_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_years = sub.add_parser("years")
    p_years.add_argument("--source", choices=["getchu", "egs"], default="getchu")
    p_years.set_defaults(func=cmd_years)

    p_calendar = sub.add_parser("calendar")
    p_calendar.add_argument("--year", type=int)
    p_calendar.set_defaults(func=cmd_calendar)

    p_latest = sub.add_parser("latest_month")
    p_latest.set_defaults(func=cmd_latest_month)

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
    p_download_start.add_argument("--source", choices=["getchu", "egs"], default="getchu")
    p_download_start.set_defaults(func=cmd_download_start)

    p_download_stop = download_sub.add_parser("stop")
    p_download_stop.set_defaults(func=cmd_download_stop)

    p_115 = sub.add_parser("115")
    _115_sub = p_115.add_subparsers(dest="action", required=True)

    p_115_login_qrcode = _115_sub.add_parser("login_qrcode")
    p_115_login_qrcode.set_defaults(func=cmd_115_login_qrcode)

    p_115_login_qrcode_status = _115_sub.add_parser("login_qrcode_status")
    p_115_login_qrcode_status.add_argument("--uid", type=str, required=True)
    p_115_login_qrcode_status.add_argument("--time", type=str, required=True)
    p_115_login_qrcode_status.add_argument("--sign", type=str, required=True)
    p_115_login_qrcode_status.set_defaults(func=cmd_115_login_qrcode_status)

    p_115_login_confirm = _115_sub.add_parser("login_confirm")
    p_115_login_confirm.add_argument("--uid", type=str, required=True)
    p_115_login_confirm.add_argument("--app", type=str, default="alipaymini")
    p_115_login_confirm.set_defaults(func=cmd_115_login_confirm)

    p_115_logout = _115_sub.add_parser("logout")
    p_115_logout.set_defaults(func=cmd_115_logout)

    p_115_login_status = _115_sub.add_parser("login_status")
    p_115_login_status.set_defaults(func=cmd_115_login_status)

    p_115_check = _115_sub.add_parser("check")
    p_115_check.add_argument("--magnet", type=str, required=True)
    p_115_check.add_argument("--dir", type=str, default="")
    p_115_check.add_argument("--debug", action="store_true")
    p_115_check.set_defaults(func=cmd_115_check)

    p_115_submit = _115_sub.add_parser("submit")
    p_115_submit.add_argument("--magnet", type=str, required=True)
    p_115_submit.add_argument("--dir", type=str, default="")
    p_115_submit.set_defaults(func=cmd_115_submit)

    p_115_check_all = _115_sub.add_parser("check_all")
    check_all_sub = p_115_check_all.add_subparsers(dest="check_all_action", required=True)

    p_115_check_all_start = check_all_sub.add_parser("start")
    p_115_check_all_start.add_argument("--year", type=int)
    p_115_check_all_start.add_argument("--month", type=int)
    p_115_check_all_start.add_argument("--source", choices=["getchu", "egs"], default="getchu")
    p_115_check_all_start.set_defaults(func=cmd_115_check_all_start)

    p_115_check_all_status = check_all_sub.add_parser("status")
    p_115_check_all_status.set_defaults(func=cmd_115_check_all_status)

    p_115_check_all_stop = check_all_sub.add_parser("stop")
    p_115_check_all_stop.set_defaults(func=cmd_115_check_all_stop)

    p_115_check_all_worker = check_all_sub.add_parser("worker")
    p_115_check_all_worker.add_argument("--year", type=int)
    p_115_check_all_worker.add_argument("--month", type=int)
    p_115_check_all_worker.add_argument("--source", choices=["getchu", "egs"], default="getchu")
    p_115_check_all_worker.set_defaults(func=cmd_115_check_all_worker)

    p_update = sub.add_parser("update_game")
    p_update.add_argument("--date", type=str, required=True)
    p_update.add_argument("--old-name", type=str, required=True)
    p_update.add_argument("--new-date", type=str)
    p_update.add_argument("--new-name", type=str)
    p_update.add_argument("--new-company", type=str)
    p_update.add_argument("--new-link", type=str)
    p_update.add_argument("--new-downloaded", type=int, choices=[0, 1])
    p_update.add_argument("--new-nyaa-name", type=str)
    p_update.add_argument("--new-submitted-115", type=int, choices=[0, 1], dest="new_submitted_115")
    p_update.add_argument("--new-submitted-pick-code", type=str, dest="new_submitted_pick_code")
    p_update.set_defaults(func=cmd_update_game)

    p_delete = sub.add_parser("delete_game")
    p_delete.add_argument("--date", type=str, required=True)
    p_delete.add_argument("--name", type=str, required=True)
    p_delete.set_defaults(func=cmd_delete_game)

    p_egs = sub.add_parser("egs")
    egs_sub = p_egs.add_subparsers(dest="egs_action", required=True)

    p_egs_crawl = egs_sub.add_parser("crawl")
    p_egs_crawl.add_argument("--start-year", type=int, required=True, dest="start_year")
    p_egs_crawl.add_argument("--end-year", type=int, dest="end_year")
    p_egs_crawl.add_argument("--month", type=int, dest="month")
    p_egs_crawl.add_argument("--db", type=str, dest="db")
    p_egs_crawl.set_defaults(func=cmd_egs_crawl)

    p_egs_status = egs_sub.add_parser("status")
    p_egs_status.add_argument("--db", type=str, dest="db")
    p_egs_status.set_defaults(func=cmd_egs_status)

    p_egs_games = egs_sub.add_parser("games")
    p_egs_games.add_argument("--page", type=int, default=1)
    p_egs_games.add_argument("--per-page", type=int, default=50, dest="per_page")
    p_egs_games.add_argument("--year", type=int)
    p_egs_games.add_argument("--month", type=int)
    p_egs_games.add_argument("--q", type=str, default="")
    p_egs_games.add_argument("--brand-kind", type=str, dest="brand_kind", default="")
    p_egs_games.add_argument("--db", type=str)
    p_egs_games.set_defaults(func=cmd_egs_games)

    p_egs_magnet = egs_sub.add_parser("magnet")
    p_egs_magnet.add_argument("--year", type=int, required=True)
    p_egs_magnet.add_argument("--month", type=int)
    p_egs_magnet.add_argument("--force", action="store_true",
                              help="忽略搜索历史强制重搜")
    p_egs_magnet.add_argument("--limit", type=int, default=0,
                              help="最多处理N个游戏，0=不限")
    p_egs_magnet.add_argument("--db", type=str)
    p_egs_magnet.set_defaults(func=cmd_egs_magnet)

    p_egs_update = egs_sub.add_parser("update")
    p_egs_update.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_egs_update.add_argument("--new-date", type=str, dest="new_date")
    p_egs_update.add_argument("--new-name", type=str, dest="new_name")
    p_egs_update.add_argument("--new-company", type=str, dest="new_company")
    p_egs_update.add_argument("--new-link", type=str, dest="new_link")
    p_egs_update.add_argument("--new-nyaa-name", type=str, dest="new_nyaa_name")
    p_egs_update.add_argument("--new-downloaded", type=int,
                              choices=[0, 1], dest="new_downloaded")
    p_egs_update.add_argument("--new-submitted-115", type=int,
                              choices=[0, 1], dest="new_submitted_115")
    p_egs_update.add_argument("--new-submitted-pick-code", type=str,
                              dest="new_submitted_pick_code")
    p_egs_update.add_argument("--db", type=str)
    p_egs_update.set_defaults(func=cmd_egs_update)

    p_egs_delete = egs_sub.add_parser("delete")
    p_egs_delete.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_egs_delete.add_argument("--db", type=str)
    p_egs_delete.set_defaults(func=cmd_egs_delete)

    p_egs_review_detail = egs_sub.add_parser("review_detail")
    p_egs_review_detail.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_egs_review_detail.add_argument("--db", type=str)
    p_egs_review_detail.set_defaults(func=cmd_egs_review_detail)

    p_egs_review_decide = egs_sub.add_parser("review_decide")
    p_egs_review_decide.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_egs_review_decide.add_argument("--decision", type=str, required=True,
                                     choices=["approve", "reject", "reopen"])
    p_egs_review_decide.add_argument("--candidate-id", type=int, dest="candidate_id")
    p_egs_review_decide.add_argument("--manual-magnet", type=str, dest="manual_magnet")
    p_egs_review_decide.add_argument("--manual-nyaa-name", type=str, dest="manual_nyaa_name")
    p_egs_review_decide.add_argument("--note", type=str)
    p_egs_review_decide.add_argument("--db", type=str)
    p_egs_review_decide.set_defaults(func=cmd_egs_review_decide)

    p_egs_review_blacklist = egs_sub.add_parser("review_blacklist")
    egs_blacklist_sub = p_egs_review_blacklist.add_subparsers(dest="action", required=True)
    p_blacklist_list = egs_blacklist_sub.add_parser("list")
    p_blacklist_list.add_argument("--db", type=str)
    p_blacklist_list.set_defaults(func=cmd_egs_review_blacklist)
    p_blacklist_add = egs_blacklist_sub.add_parser("add")
    p_blacklist_add.add_argument("--company", required=True)
    p_blacklist_add.add_argument("--note", default="")
    p_blacklist_add.add_argument("--db", type=str)
    p_blacklist_add.set_defaults(func=cmd_egs_review_blacklist)
    p_blacklist_remove = egs_blacklist_sub.add_parser("remove")
    p_blacklist_remove.add_argument("--company", required=True)
    p_blacklist_remove.add_argument("--db", type=str)
    p_blacklist_remove.set_defaults(func=cmd_egs_review_blacklist)

    p_auto = sub.add_parser("auto")
    auto_sub = p_auto.add_subparsers(dest="action", required=True)

    p_idle = auto_sub.add_parser("idle_run")
    p_idle.add_argument("--force", action="store_true")
    p_idle.add_argument("--year", type=int)
    p_idle.add_argument("--start-month", type=int, dest="start_month")
    p_idle.add_argument("--end-month", type=int, dest="end_month")
    p_idle.set_defaults(func=cmd_auto_idle_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
