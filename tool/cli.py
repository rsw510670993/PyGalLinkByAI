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
    years = tool.get_years_list()
    _print({"years": years})


def cmd_calendar(args):
    from datetime import datetime
    import tool.core

    paths = runtime_paths()
    base_year = int(args.year) if getattr(args, "year", None) else datetime.now().year
    start_year = base_year - 2
    end_year = base_year

    conn = tool.core.open_db()
    tool.core.ensure_getchu_schema(conn)
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
        FROM getchu_games
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



# ---------------------------------------------------------------------------
# crawl: 爬取 getchu + AI 去重入库（新流程 Phase 1）
# ---------------------------------------------------------------------------

def _crawl_status_default():
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


def cmd_crawl_status(args):
    paths = runtime_paths()
    status = read_json(paths["crawl_status_path"], _crawl_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and not pid_is_running(int(pid)):
        status["running"] = False
        status["stopped_reason"] = "not_running"
        write_json_atomic(paths["crawl_status_path"], status)
    _print(status)


def cmd_crawl_start(args):
    paths = runtime_paths()
    status = read_json(paths["crawl_status_path"], _crawl_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and pid_is_running(int(pid)):
        _print({"status": "error", "message": "crawl任务已在运行中", "pid": int(pid)})
        return

    start_year = int(args.start_year)
    end_year = int(args.end_year)
    if start_year > end_year:
        start_year, end_year = end_year, start_year
    month = int(args.month) if getattr(args, "month", None) else 0

    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)

    launch_log = daily_log_path("crawl_launch")
    launch_fp = open(launch_log, "ab", buffering=0)

    cmd = [
        sys.executable,
        os.path.join(_base_dir(), "crawl_worker.py"),
        "--start-year", str(start_year),
        "--end-year", str(end_year),
        "--month", str(month),
    ]
    if getattr(args, "no_ai", False):
        cmd.append("--no-ai")
    if getattr(args, "no_reconcile", False):
        cmd.append("--no-reconcile")

    p = subprocess.Popen(
        cmd,
        cwd=_base_dir(),
        stdout=launch_fp,
        stderr=launch_fp,
        start_new_session=True,
    )
    started = False
    for _ in range(10):
        time.sleep(0.2)
        status = read_json(paths["crawl_status_path"], _crawl_status_default())
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
                "message": "crawl任务启动失败，请检查写权限（status/logs/getchu.db）与Python依赖",
            }
        )
        return

    _print({"status": "success", "message": "crawl任务已启动", "pid": p.pid})


def cmd_crawl_stop(args):
    paths = runtime_paths()
    status = read_json(paths["crawl_status_path"], _crawl_status_default())
    pid = status.get("pid")
    if not pid:
        _print({"status": "error", "message": "未找到运行中的crawl任务"})
        return
    try:
        if pid_is_running(int(pid)):
            terminate_pid(int(pid), signal.SIGTERM)
        status["running"] = False
        status["stopped_reason"] = "stopped"
        write_json_atomic(paths["crawl_status_path"], status)
        _print({"status": "success", "message": "停止请求已发送", "pid": int(pid)})
    except Exception as e:
        _print({"status": "error", "message": str(e), "pid": int(pid)})


def cmd_crawl_reconcile(args):
    import tool.dedup_service as ds

    months = [int(args.month)] if getattr(args, "month", None) else list(range(1, 13))
    dry_run = not getattr(args, "execute", False)
    plans = []
    for month in months:
        try:
            plan = ds.reconcile_month(int(args.year), month, use_ai=not getattr(args, "no_ai", False), dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            plan = {"year": int(args.year), "month": month, "errors": [str(e)]}
        plans.append(plan)

    summary = {
        "dry_run": dry_run,
        "months": len(plans),
        "merge_groups": sum(len(p.get("merges") or []) for p in plans),
        "rows_would_merge": sum(
            len(m.get("members") or []) - 1
            for p in plans for m in (p.get("merges") or [])
        ),
        "ai_calls": sum(int(p.get("ai_calls") or 0) for p in plans),
        "editions_suggestions": sum(len(p.get("editions") or []) for p in plans),
        "errors": [e for p in plans for e in (p.get("errors") or [])],
    }
    _print({"summary": summary, "plans": plans})


# ---------------------------------------------------------------------------
# nyaa: sukebei.nyaa 磁链获取（新流程 Phase 2）
# ---------------------------------------------------------------------------

def _nyaa_status_default():
    return {
        "running": False,
        "pid": None,
        "year": None,
        "month": None,
        "current": None,
        "done": 0,
        "total": 0,
        "selected": 0,
        "low_score": 0,
        "no_result": 0,
        "skip_cache": 0,
        "errors": [],
        "results": [],
        "started_at": None,
        "updated_at": None,
        "stopped_reason": None,
    }


def cmd_nyaa_status(args):
    paths = runtime_paths()
    status = read_json(paths["nyaa_status_path"], _nyaa_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and not pid_is_running(int(pid)):
        status["running"] = False
        status["stopped_reason"] = "not_running"
        write_json_atomic(paths["nyaa_status_path"], status)
    _print(status)


def cmd_nyaa_start(args):
    paths = runtime_paths()
    status = read_json(paths["nyaa_status_path"], _nyaa_status_default())
    pid = status.get("pid")
    if status.get("running") and pid and pid_is_running(int(pid)):
        _print({"status": "error", "message": "nyaa任务已在运行中", "pid": int(pid)})
        return

    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)

    launch_log = daily_log_path("nyaa_launch")
    launch_fp = open(launch_log, "ab", buffering=0)

    cmd = [
        sys.executable,
        os.path.join(_base_dir(), "nyaa_worker.py"),
        "--year", str(int(args.year)),
        "--month", str(int(getattr(args, "month", None) or 0)),
    ]
    if getattr(args, "force", False):
        cmd.append("--force")
    if getattr(args, "limit", None):
        cmd += ["--limit", str(int(args.limit))]

    p = subprocess.Popen(
        cmd,
        cwd=_base_dir(),
        stdout=launch_fp,
        stderr=launch_fp,
        start_new_session=True,
    )
    started = False
    for _ in range(10):
        time.sleep(0.2)
        status = read_json(paths["nyaa_status_path"], _nyaa_status_default())
        if status.get("pid") == p.pid:
            started = True
            break

    if not started:
        try:
            if pid_is_running(int(p.pid)):
                terminate_pid(int(p.pid), signal.SIGTERM)
        except Exception:
            pass
        _print({"status": "error", "message": "nyaa任务启动失败，请检查写权限与网络"})
        return

    _print({"status": "success", "message": "nyaa任务已启动", "pid": p.pid})


def cmd_nyaa_stop(args):
    paths = runtime_paths()
    status = read_json(paths["nyaa_status_path"], _nyaa_status_default())
    pid = status.get("pid")
    if not pid:
        _print({"status": "error", "message": "未找到运行中的nyaa任务"})
        return
    try:
        if pid_is_running(int(pid)):
            terminate_pid(int(pid), signal.SIGTERM)
        status["running"] = False
        status["stopped_reason"] = "stopped"
        write_json_atomic(paths["nyaa_status_path"], status)
        _print({"status": "success", "message": "停止请求已发送", "pid": int(pid)})
    except Exception as e:
        _print({"status": "error", "message": str(e), "pid": int(pid)})


# ---------------------------------------------------------------------------
# magnet: 磁链解析与重标注（新流程 Phase 3）
# ---------------------------------------------------------------------------

def cmd_magnet_relabel(args):
    import tool.relabel as rl

    dry_run = not getattr(args, "execute", False)
    plans = []
    years = None
    if getattr(args, "all", False):
        plans, years = rl.relabel_all(force=bool(getattr(args, "force", False)), dry_run=dry_run)
    else:
        months = [int(args.month)] if getattr(args, "month", None) else list(range(1, 13))
        for month in months:
            try:
                plan = rl.relabel_month(
                    int(args.year), month,
                    force=bool(getattr(args, "force", False)),
                    dry_run=dry_run,
                )
            except Exception as e:  # noqa: BLE001
                plan = {"year": int(args.year), "month": month, "errors": [str(e)]}
            plans.append(plan)

    summary = {
        "dry_run": dry_run,
        "force": bool(getattr(args, "force", False)),
        "all_years": bool(getattr(args, "all", False)),
        "years": years,
        "months": len(plans),
        "with_link": sum(int(p.get("with_link") or 0) for p in plans),
        "already": sum(int(p.get("already") or 0) for p in plans),
        "migrated": sum(int(p.get("migrated") or 0) for p in plans),
        "no_dn": sum(int(p.get("no_dn") or 0) for p in plans),
        "no_dn_date": sum(int(p.get("no_dn_date") or 0) for p in plans),
        "dn_mismatch": sum(int(p.get("dn_mismatch") or 0) for p in plans),
        "applied": sum(int(p.get("applied") or 0) for p in plans),
        "changed_rows": sum(len(p.get("changes") or []) for p in plans),
        "errors": [e for p in plans for e in (p.get("errors") or [])],
    }
    _print({"summary": summary, "plans": plans})


def cmd_magnet_status(args):
    import tool.relabel as rl

    _print(rl.relabel_status())


def cmd_magnet_parse(args):
    import tool.relabel as rl

    _print(rl.extract_dn_parts(args.magnet))



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
        "115", "check_all", "worker",
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


# 整理115: 按磁链dn时间戳定位/校对/规范化重命名游戏文件夹
def cmd_115_organize(args):
    try:
        from tool.organize_115 import organize_batch

        # 安全约定: 默认预览，--execute 才实际改名/移动/重置状态
        dry_run = not bool(getattr(args, "execute", False))
        result = organize_batch(
            year=args.year,
            month=args.month,
            name=args.name,
            dry_run=dry_run,
            limit=getattr(args, "limit", None),
        )
        _print(result)
    except Exception as e:
        _print({"status": "error", "message": f"整理失败: {str(e)}"})



def cmd_115_review(args):
    """查看115整理待人工审阅清单（status/review_115.json）"""
    import json as _json

    paths = runtime_paths()
    path = os.path.join(str(paths["status_dir"]), "review_115.json")
    if not os.path.exists(path):
        _print({"status": "ok", "message": "尚无待审清单（先运行 115 organize 生成）", "path": path})
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception as e:
        _print({"status": "error", "message": f"读取失败: {e}"})
        return
    # 过滤
    status_filter = getattr(args, "status", None)
    items = data.get("items") or []
    if status_filter:
        items = [r for r in items if r.get("status") == status_filter]
    _print({
        "status": "ok",
        "path": path,
        "updated_at": data.get("updated_at_str"),
        "scope": data.get("scope"),
        "total": len(items),
        "items": [
            {k: r.get(k) for k in ("date", "name", "status", "message", "advice",
                                   "old_path", "target_path", "dn_date")}
            for r in items
        ],
    })


def cmd_115_check_all_worker(args):
    import tool.core

    paths = runtime_paths()
    status_path = paths["check_all_status_path"]

    status = {
        "running": True,
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

    conn = tool.core.open_db()
    tool.core.ensure_getchu_schema(conn)
    cursor = conn.cursor()
    base_sql = "SELECT date, name, link FROM getchu_games WHERE link IS NOT NULL AND link != '' AND COALESCE(downloaded, 0) = 0"
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

    for date, name, link in rows:
        try:
            status["current"] = {"date": date, "name": name}
            status["checked"] += 1
            status["updated_at"] = now_ts()
            write_json_atomic(status_path, status)
            result, err = _check_magnet_exists_with_timeout(link, 60)
            if err:
                status["errors"].append(f"{date}/{name}: {err}")
            elif isinstance(result, dict) and result.get("exists"):
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


def build_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_years = sub.add_parser("years")
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

    p_crawl = sub.add_parser("crawl", help="爬取getchu+AI去重入库（新流程）")
    crawl_sub = p_crawl.add_subparsers(dest="action", required=True)

    p_crawl_status = crawl_sub.add_parser("status")
    p_crawl_status.set_defaults(func=cmd_crawl_status)

    p_crawl_start = crawl_sub.add_parser("start")
    p_crawl_start.add_argument("--start-year", type=int, required=True)
    p_crawl_start.add_argument("--end-year", type=int, required=True)
    p_crawl_start.add_argument("--month", type=int, help="仅处理指定月份")
    p_crawl_start.add_argument("--no-ai", action="store_true", dest="no_ai", help="禁用AI去重，仅规则分组")
    p_crawl_start.add_argument("--no-reconcile", action="store_true", dest="no_reconcile",
                               help="跳过存量行再去重阶段")
    p_crawl_start.set_defaults(func=cmd_crawl_start)

    p_crawl_stop = crawl_sub.add_parser("stop")
    p_crawl_stop.set_defaults(func=cmd_crawl_stop)

    p_crawl_reconcile = crawl_sub.add_parser(
        "reconcile", help="存量行再去重：合并同一作品不同表记的行（默认预览，--execute 执行）"
    )
    p_crawl_reconcile.add_argument("--year", type=int, required=True)
    p_crawl_reconcile.add_argument("--month", type=int, help="仅处理指定月份")
    p_crawl_reconcile.add_argument("--execute", action="store_true", help="实际执行合并（默认仅预览）")
    p_crawl_reconcile.add_argument("--no-ai", action="store_true", dest="no_ai", help="仅规则合并，不调用AI")
    p_crawl_reconcile.set_defaults(func=cmd_crawl_reconcile)

    p_nyaa = sub.add_parser("nyaa", help="sukebei.nyaa磁链获取（新流程Phase 2）")
    nyaa_sub = p_nyaa.add_subparsers(dest="action", required=True)

    p_nyaa_status = nyaa_sub.add_parser("status")
    p_nyaa_status.set_defaults(func=cmd_nyaa_status)

    p_nyaa_start = nyaa_sub.add_parser("start")
    p_nyaa_start.add_argument("--year", type=int, required=True)
    p_nyaa_start.add_argument("--month", type=int, help="仅处理指定月份")
    p_nyaa_start.add_argument("--force", action="store_true", help="忽略搜索历史强制重搜")
    p_nyaa_start.add_argument("--limit", type=int, help="最多处理N个游戏")
    p_nyaa_start.set_defaults(func=cmd_nyaa_start)

    p_nyaa_stop = nyaa_sub.add_parser("stop")
    p_nyaa_stop.set_defaults(func=cmd_nyaa_stop)

    p_magnet = sub.add_parser("magnet", help="磁链解析与重标注（新流程Phase 3）")
    magnet_sub = p_magnet.add_subparsers(dest="action", required=True)

    p_magnet_relabel = magnet_sub.add_parser(
        "relabel", help="按dn时间戳重标注发布时间/公司名/游戏名并搬月（默认预览，--execute 执行）"
    )
    p_magnet_relabel.add_argument("--year", type=int, help="目标年份（按getchu登记月桶）")
    p_magnet_relabel.add_argument("--month", type=int, help="仅处理指定月份")
    p_magnet_relabel.add_argument("--all", action="store_true", help="全库历史数据执行")
    p_magnet_relabel.add_argument("--execute", action="store_true", help="实际写入（默认仅预览）")
    p_magnet_relabel.add_argument("--force", action="store_true", help="对已重标注行强制重做")
    p_magnet_relabel.set_defaults(func=cmd_magnet_relabel)

    p_magnet_status = magnet_sub.add_parser("status")
    p_magnet_status.set_defaults(func=cmd_magnet_status)

    p_magnet_parse = magnet_sub.add_parser("parse", help="解析单条磁链（调试用）")
    p_magnet_parse.add_argument("--magnet", type=str, required=True)
    p_magnet_parse.set_defaults(func=cmd_magnet_parse)

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
    p_115_check_all_start.set_defaults(func=cmd_115_check_all_start)

    p_115_check_all_status = check_all_sub.add_parser("status")
    p_115_check_all_status.set_defaults(func=cmd_115_check_all_status)

    p_115_check_all_stop = check_all_sub.add_parser("stop")
    p_115_check_all_stop.set_defaults(func=cmd_115_check_all_stop)

    p_115_check_all_worker = check_all_sub.add_parser("worker")
    p_115_check_all_worker.add_argument("--year", type=int)
    p_115_check_all_worker.add_argument("--month", type=int)
    p_115_check_all_worker.set_defaults(func=cmd_115_check_all_worker)

    p_115_organize = _115_sub.add_parser("organize")
    p_115_organize.add_argument("--year", type=int, help="年份（不填=全部）")
    p_115_organize.add_argument("--month", type=int, help="月份（不填=整年）")
    p_115_organize.add_argument("--name", type=str, help="仅处理指定游戏")
    p_115_organize.add_argument("--limit", type=int, help="最多处理N个（调试用）")
    p_115_organize.add_argument("--execute", action="store_true", help="实际执行（默认仅预览）")
    p_115_organize.set_defaults(func=cmd_115_organize)
    p_115_review = _115_sub.add_parser("review", help="查看115整理待人工审阅清单")
    p_115_review.add_argument("--status", type=str, help="按状态过滤(shared_cid/ambiguous/not_dir/no_dn_date/missing_in_115)")
    p_115_review.set_defaults(func=cmd_115_review)

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
