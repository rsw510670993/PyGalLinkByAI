import argparse
import json
import os
import sys
import concurrent.futures as cf
import multiprocessing as mp

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import tool
from tool import egs_extids
from tool.runtime import runtime_paths


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _in_gal_path(path):
    value = "/" + str(path or "").strip("/")
    return value == "/GAL" or value.startswith("/GAL/")


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
            SUM(CASE WHEN link IS NOT NULL AND link != ''
                           AND COALESCE(magnet_duplicate,0)=0
                           AND COALESCE(submission_excluded,0)=0
                     THEN 1 ELSE 0 END) as magnet_total,
            SUM(CASE WHEN link IS NOT NULL AND link != ''
                           AND COALESCE(magnet_duplicate,0)=0
                           AND COALESCE(submission_excluded,0)=0
                           AND COALESCE(downloaded,0)=1
                     THEN 1 ELSE 0 END) as magnet_downloaded,
            SUM(CASE WHEN link IS NOT NULL AND link != ''
                           AND COALESCE(magnet_duplicate,0)=0
                           AND COALESCE(submission_excluded,0)=0
                           AND COALESCE(submitted_115,0)=1
                     THEN 1 ELSE 0 END) as magnet_submitted
        FROM egs_games
        WHERE date >= ? AND date < ?
        GROUP BY year, month
        ORDER BY year DESC, month DESC
        """,
        (f"{int(start_year):04d}-01", f"{int(end_year) + 1:04d}-01"),
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
        # 当前方针下校对范围只允许 /GAL；未显式给目录时按 /GAL 处理，
        # 不再回退到旧的 /我的下载/Getchu。
        save_path = args.dir or "/GAL"
        if not _in_gal_path(save_path):
            _print({"success": False, "status": "blocked", "message": "校验范围仅限 /GAL"})
            return
        _print(check_magnet_exists(magnet, save_path, debug=bool(getattr(args, "debug", False))))
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def cmd_115_submit(args):
    try:
        from tool.egs_core import open_egs_db
        from tool.p115_client import offline_submit
        magnet = args.magnet
        conn = open_egs_db(getattr(args, "db", None))
        try:
            row = conn.execute(
                "SELECT COALESCE(downloaded,0), COALESCE(link,''), COALESCE(date,'') FROM egs_games WHERE egs_id=?",
                (int(args.egs_id),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            _print({"success": False, "status": "error", "message": "EGS 游戏记录不存在"})
            return
        if int(row[0] or 0) == 1:
            _print({"success": False, "status": "blocked", "message": "该游戏已下载，禁止重复提交115"})
            return
        if not magnet or str(row[1] or "") != magnet:
            _print({"success": False, "status": "blocked", "message": "提交磁链与 EGS 记录不一致"})
            return
        date = str(row[2] or "")
        expected_dir = f"/GAL/GAL-{date[:4]}" if len(date) >= 4 else "/GAL"
        save_path = (args.dir or expected_dir).strip()
        if ("/" + save_path.strip("/")) != ("/" + expected_dir.strip("/")):
            _print({
                "success": False, "status": "blocked",
                "message": f"提交目录仅限 {expected_dir}",
            })
            return
        _print(offline_submit(magnet, save_path))
    except Exception as e:
        _print({"status": "error", "message": f"115模块加载失败: {e}"})


def _check_magnet_exists_child(link, q, strict_infohash=False, offline_tasks=None,
                               allowed_save_paths=None):
    try:
        from tool.p115_client import check_magnet_exists

        res = check_magnet_exists(
            link, "", strict_infohash=strict_infohash, offline_tasks=offline_tasks,
            allowed_save_paths=allowed_save_paths,
        )
        q.put({"ok": True, "res": res})
    except Exception as e:
        q.put({"ok": False, "error": str(e)})


def _check_magnet_exists_with_timeout(link, timeout_s=60, strict_infohash=False,
                                      offline_tasks=None, allowed_save_paths=None):
    if os.name != "nt":
        try:
            ctx = mp.get_context("fork")
        except Exception:
            ctx = mp.get_context("spawn")

        q = ctx.Queue(maxsize=1)
        p = ctx.Process(
            target=_check_magnet_exists_child,
            args=(link, q, strict_infohash, offline_tasks, allowed_save_paths),
        )
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

        return check_magnet_exists(
            link, "", strict_infohash=strict_infohash, offline_tasks=offline_tasks,
            allowed_save_paths=allowed_save_paths,
        )

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


def cmd_egs_crawl(args):
    from tool.egs_core import crawl_egs_range
    start_year = int(args.start_year)
    end_year = int(args.end_year or args.start_year)
    stats = crawl_egs_range(start_year, end_year, month=args.month, db_path=args.db,
                            include_non_eroge=bool(getattr(args, "include_non_eroge", False)))
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
    from tool.egs_core import ensure_egs_schema, open_egs_db, ensure_review_blacklist_schema
    conn = open_egs_db(args.db)
    try:
        cur = conn.cursor()
        ensure_egs_schema(conn)
        ensure_review_blacklist_schema(conn)
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='egs_games'")
        if cur.fetchone() is None:
            _print({"data": [], "current_page": int(args.page), "per_page": int(args.per_page),
                    "total": 0, "year": args.year, "month": args.month, "q": args.q})
            return

        conditions = []
        params = []
        if args.year is not None and args.month is not None:
            conditions.append("date = ?")
            params.append(f"{int(args.year):04d}-{int(args.month):02d}")
        elif args.year is not None:
            year = int(args.year)
            conditions.append("date >= ? AND date < ?")
            params.extend([f"{year:04d}-01", f"{year + 1:04d}-01"])
        elif args.month is not None:
            conditions.append("substr(date,6,2) = ?")
            params.append(f"{int(args.month):02d}")
        if args.brand_kind:
            conditions.append("brand_kind = ?")
            params.append(str(args.brand_kind).strip().upper())
        if getattr(args, "review", "") == "pending":
            conditions.append("""
                (link IS NULL OR link = '')
                AND NOT EXISTS (
                    SELECT 1 FROM egs_review_company_blacklist b
                     WHERE b.company IN (egs_games.company, egs_games.egs_company)
                )
                AND EXISTS (
                    SELECT 1 FROM egs_nyaa_candidates c
                     WHERE c.egs_id = egs_games.egs_id
                )
                AND COALESCE((
                    SELECT l.review_status FROM egs_nyaa_search_log l
                     WHERE l.egs_id = egs_games.egs_id
                ), 'pending') = 'pending'
            """)
        stage = getattr(args, "stage", "")
        if stage == "downloaded":
            conditions.append("downloaded = 1")
        elif stage == "linked":
            conditions.append("link IS NOT NULL AND link != '' AND downloaded = 0")
        elif stage == "none":
            conditions.append("(link IS NULL OR link = '')")
        elif stage == "failed":
            conditions.append("COALESCE(download_failed,0) = 1")
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
                LEFT JOIN egs_nyaa_search_log l ON l.egs_id = c.egs_id
                WHERE c.egs_id = egs_games.egs_id
                  AND COALESCE(l.review_status, '') != 'rejected') AS candidate_count
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
        ext_cols = egs_extids.external_id_columns()
        rows = cur.execute(
            f"""
            SELECT egs_id, date, name, company, release_ts, egs_date, actual_release_ts, brand_kind,
                   {ext_cols}, link, nyaa_name, comment, downloaded, submitted_115, submitted_pick_code,
                   submitted_at, checked_at,
                   download_failed, download_failed_at,
                   COALESCE(magnet_duplicate,0) AS magnet_duplicate, duplicate_of_egs_id,
                   duplicate_reason,
                   COALESCE(submission_excluded,0) AS submission_excluded,
                   submission_excluded_reason,
                   COALESCE(resource_kind,'') AS resource_kind,
                   (SELECT owner.name FROM egs_games owner
                     WHERE owner.egs_id=egs_games.duplicate_of_egs_id) AS duplicate_of_name,
                   (SELECT owner.date FROM egs_games owner
                     WHERE owner.egs_id=egs_games.duplicate_of_egs_id) AS duplicate_of_date
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
            "review": getattr(args, "review", "") or "",
            "q": args.q or "",
        })
    finally:
        conn.close()


def cmd_egs_artifact_audit(args):
    from tool.egs_core import audit_artifact_links, open_egs_db
    conn = open_egs_db(args.db)
    try:
        result = audit_artifact_links(conn)
    finally:
        conn.close()
    _print(result)
    if getattr(args, "strict", False) and not result["ok"]:
        sys.exit(1)


def cmd_egs_external_ids(args):
    """外部 ID 映射表：backfill / audit / get / resolve（backfill 默认 dry-run）。"""
    from tool.egs_core import ensure_egs_schema, open_egs_db
    from tool.egs_extids import (audit, backfill_from_columns, get_external_ids,
                                 resolve_egs_id)
    conn = open_egs_db(args.db)
    try:
        ensure_egs_schema(conn)
        action = getattr(args, "action", "audit")
        if action == "backfill":
            result = backfill_from_columns(
                conn, limit=getattr(args, "limit", 0),
                dry_run=not getattr(args, "execute", False))
            _print({"status": "success", **result})
        elif action == "get":
            if not getattr(args, "egs_id", None):
                _print({"status": "error", "message": "缺少 --egs-id"})
                sys.exit(2)
            _print({"status": "success", "egs_id": args.egs_id,
                    "ids": get_external_ids(conn, args.egs_id)})
        elif action == "resolve":
            source = getattr(args, "source", "") or ""
            external_id = getattr(args, "external_id", "") or ""
            if not source or not external_id:
                _print({"status": "error", "message": "缺少 --source 或 --external-id"})
                sys.exit(2)
            _print({"status": "success", "source": source, "external_id": external_id,
                    "egs_id": resolve_egs_id(conn, source, external_id)})
        else:
            _print({"status": "success", **audit(conn)})
    finally:
        conn.close()


def cmd_egs_overview(args):
    import sqlite3 as _sqlite3
    from tool.egs_core import open_egs_db, ensure_egs_schema
    from tool.pipeline import pending_review_summary
    conn = open_egs_db(args.db)
    try:
        ensure_egs_schema(conn)
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN link IS NOT NULL AND link != '' THEN 1 ELSE 0 END) AS linked,
                      SUM(CASE WHEN downloaded = 1 THEN 1 ELSE 0 END) AS downloaded,
                      SUM(CASE WHEN submitted_115 = 1 THEN 1 ELSE 0 END) AS submitted,
                      SUM(CASE WHEN link IS NULL OR link = '' THEN 1 ELSE 0 END) AS no_link,
                      SUM(CASE WHEN link IS NOT NULL AND link != '' AND submitted_115 = 0 AND downloaded = 0
                               THEN 1 ELSE 0 END) AS pending_submit
                 FROM egs_games"""
        ).fetchone()
        try:
            issues = conn.execute(
                "SELECT COUNT(*) FROM egs_organize_issues WHERE COALESCE(resolved,0)=0"
            ).fetchone()[0]
        except _sqlite3.OperationalError:
            issues = 0
    finally:
        conn.close()
    pending = pending_review_summary(1980, 3000, 0)
    try:
        from tool.egs_thumb import missing_downloaded_count
        cover_missing = missing_downloaded_count()
    except Exception:  # noqa: BLE001 - 封面统计失败不影响总控其他数字
        cover_missing = -1
    _print({
        "status": "success",
        "total": int(row["total"] or 0),
        "linked": int(row["linked"] or 0),
        "downloaded": int(row["downloaded"] or 0),
        "submitted": int(row["submitted"] or 0),
        "no_link": int(row["no_link"] or 0),
        "pending_submit": int(row["pending_submit"] or 0),
        "pending_review": int(pending["count"] or 0),
        "pending_years": pending["years"],
        "issues": int(issues or 0),
        "cover_missing_downloaded": cover_missing,
    })


def cmd_egs_cover(args):
    """补封面：默认自动按置信度取图（getchu/dlsite/dmm → official → vndb）。

    默认 dry-run 只列清单；--execute 才真下载。--only 只跑单个来源（调试用）。
    """
    import time

    import requests

    from tool.egs_core import ensure_egs_schema, open_egs_db
    from tool.egs_thumb import (coverage, fetch_cover_for_row, pending_cover_rows,
                                scan_dlsite_ids_from_dirs)

    conn = open_egs_db(args.db)
    try:
        ensure_egs_schema(conn)
        if getattr(args, "coverage", False):
            _print({"status": "success", **coverage()})
            return
        if getattr(args, "optimize", False):
            from tool.thumb_optimize import run_optimize
            _print(run_optimize(
                delete_originals=bool(getattr(args, "delete_originals", False)),
                limit=getattr(args, "limit", 0),
                force=bool(getattr(args, "force", False))))
            return

        only = (getattr(args, "only", "") or "").strip().lower()
        execute = bool(getattr(args, "execute", False))
        year = getattr(args, "year", None)
        result = {"status": "success", "dry_run": not execute,
                  "mode": only or "auto"}
        if not only:
            # 第一步：从种子名/115 目录名补 DLsite 编号（零误配）
            result["dir_scan"] = scan_dlsite_ids_from_dirs(
                conn, dry_run=not execute, limit=getattr(args, "dir_limit", 0))
        rows = pending_cover_rows(conn, year, year, getattr(args, "month", None),
                                  limit=getattr(args, "limit", 0))
        result["total"] = len(rows)
        if not execute:
            result["preview"] = [{"egs_id": r["egs_id"], "name": r["name"]} for r in rows[:10]]
            _print(result)
            return

        session = requests.Session()
        session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36"),
            "Referer": "https://www.getchu.com/",
        })
        ok_count = failed = 0
        reasons: dict = {}
        for row in rows:
            ok, _source, message = fetch_cover_for_row(
                row, session, conn=conn, sources=[only] if only else None)
            if ok:
                ok_count += 1
            else:
                failed += 1
                key = message.split("；")[-1][:40]
                reasons[key] = reasons.get(key, 0) + 1
            time.sleep(getattr(args, "interval", 1.0) or 0)
        conn.commit()
        result.update(ok=ok_count, failed=failed, failure_reasons=reasons)
        result["coverage"] = coverage()
        _print(result)
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
        rescan=bool(getattr(args, "rescan", False)),
        rescan_limit=int(getattr(args, "rescan_limit", 0) or 0),
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
        new_resource_kind=args.new_resource_kind,
        db_path=args.db,
    ))


def cmd_egs_delete(args):
    from tool.egs_core import delete_egs_game_record
    _print(delete_egs_game_record(int(args.egs_id), db_path=args.db))


def cmd_egs_organize_confirm(args):
    """人工确认后允许跨年移动/整理单个目录。"""
    from tool.egs_core import open_egs_db
    from tool.egs_organize import organize_single, record_organize_issue
    conn = open_egs_db(args.db)
    try:
        row = conn.execute(
            "SELECT egs_id FROM egs_games WHERE date=? AND name=?",
            (str(args.date), str(args.name)),
        ).fetchone()
        result = organize_single(
            str(args.date), str(args.name), dry_run=False,
            conn=conn, confirmed_cross_year=True, confirmed_month_shift=True,
        )
        ok = result.get("status") in (
            "renamed", "moved", "renamed_moved", "already_ok",
            "found_set_downloaded", "wrapped_file",
        )
        # 整理待办联动：成功关闭对应待办，失败则刷新待办内容
        record_organize_issue(
            conn, date=str(args.date), name=str(args.name), code=result.get("status"),
            executed=True, detail=result,
            egs_id=row[0] if row else None,
        )
        _print({"success": ok, **result})
    finally:
        conn.close()


def cmd_egs_organize_issues(args):
    from tool.egs_core import open_egs_db
    from tool.egs_organize import list_organize_issues
    conn = open_egs_db(args.db)
    try:
        _print(list_organize_issues(conn, include_resolved=bool(args.all)))
    finally:
        conn.close()


def cmd_egs_organize_issue_resolve(args):
    from tool.egs_core import open_egs_db
    from tool.egs_organize import resolve_organize_issue
    conn = open_egs_db(args.db)
    try:
        _print(resolve_organize_issue(conn, int(args.id), note=getattr(args, 'note', None)))
    finally:
        conn.close()


def cmd_egs_organize_issue_reject(args):
    from tool.egs_core import open_egs_db
    from tool.egs_organize import reject_organize_issue
    conn = open_egs_db(args.db)
    try:
        _print(reject_organize_issue(conn, int(args.id)))
    finally:
        conn.close()

def cmd_egs_organize_issue_reject_month(args):
    from tool.egs_core import open_egs_db
    from tool.egs_organize import reject_month_shift_issue
    conn = open_egs_db(args.db)
    try:
        _print(reject_month_shift_issue(conn, int(args.id)))
    finally:
        conn.close()

def cmd_egs_torrent_meta_backfill(args):
    """存量回填：为已有磁链的行下载 .torrent 解析 info.name，供整理精确定位。"""
    import sqlite3

    import requests
    from tool.egs_core import ensure_egs_schema, open_egs_db
    from tool.egs_magnet import HEADERS, RequestPacer
    from tool.torrent_meta import fetch_torrent_meta, meta_to_json

    conn = open_egs_db(args.db)
    ensure_egs_schema(conn)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT g.egs_id, g.date, g.name, g.link, g.infohash_hex
              FROM egs_games g
             WHERE COALESCE(g.link,'') != '' AND g.infohash_hex IS NOT NULL
               AND (? OR COALESCE(g.torrent_name,'') = '')
               AND (? = 0 OR g.date LIKE ?)
             ORDER BY g.egs_id
        """
        rows = conn.execute(sql, (bool(args.force), int(args.year or 0), f"{args.year}-%" if args.year else '')).fetchall()
        if args.limit:
            rows = rows[:args.limit]
        stats = {"total": len(rows), "ok": 0, "skip_no_view": 0, "fail": 0, "updated": []}
        if not rows:
            _print(stats)
            return
        session = requests.Session()
        session.headers.update(HEADERS)
        pacer = RequestPacer()
        for row in rows:
            cand = conn.execute(
                """SELECT view_url FROM egs_nyaa_candidates
                    WHERE egs_id=? AND infohash_hex=?
                    ORDER BY selected DESC, id DESC LIMIT 1""",
                (row['egs_id'], row['infohash_hex']),
            ).fetchone()
            if not cand or not cand['view_url']:
                stats['skip_no_view'] += 1
                continue
            meta = fetch_torrent_meta(session, cand['view_url'], expected_infohash=row['infohash_hex'], pacer=pacer)
            if not meta:
                stats['fail'] += 1
                continue
            conn.execute(
                """UPDATE egs_games SET torrent_name=?, torrent_files=?, torrent_size=?
                    WHERE egs_id=?""",
                (meta['name'], meta_to_json(meta), meta.get('total_size'), row['egs_id']),
            )
            conn.commit()
            stats['ok'] += 1
            stats['updated'].append({"egs_id": row['egs_id'], "name": row['name'],
                                     "torrent_name": meta['name']})
        _print(stats)
    finally:
        conn.close()


def cmd_egs_retry_magnet(args):
    """手动重爬：强制重新搜索候选；无更优磁链时保留 download_failed 标记。"""
    import logging

    import requests
    from tool.egs_core import ensure_egs_schema, open_egs_db
    from tool.egs_magnet import HEADERS, process_game

    conn = open_egs_db(args.db)
    ensure_egs_schema(conn)
    try:
        row = conn.execute(
            "SELECT egs_id, date, name, company, release_ts FROM egs_games WHERE egs_id=?",
            (int(args.egs_id),),
        ).fetchone()
        if not row:
            _print({"success": False, "message": "记录不存在"})
            return
        session = requests.Session()
        session.headers.update(HEADERS)
        logger = logging.getLogger("egs_magnet")
        status, result = process_game(conn, session, row, logger, force=True)
        _print({"success": status in ("selected",), "status": status, **result})
    finally:
        conn.close()


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
        resource_kind=args.resource_kind,
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


def cmd_prune(args):
    from tool.ops import prune
    _print(prune(args.keep, args.days, args.db_keep, args.db_days,
                 bool(args.execute), args.requested_by))


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
    p_115_submit.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_115_submit.add_argument("--magnet", type=str, required=True)
    p_115_submit.add_argument("--dir", type=str, default="")
    p_115_submit.add_argument("--db", type=str)
    p_115_submit.set_defaults(func=cmd_115_submit)

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
    p_egs_crawl.add_argument("--include-non-eroge", action="store_true",
                             dest="include_non_eroge",
                             help="把全年龄（erogame=false）也抓进清单，标 is_eroge=0，不进磁链队列")
    p_egs_crawl.set_defaults(func=cmd_egs_crawl)

    p_egs_cover = egs_sub.add_parser(
        "cover", help="补封面：自动按置信度取图（默认 dry-run，--execute 才下载）")
    p_egs_cover.add_argument("--year", type=int)
    p_egs_cover.add_argument("--month", type=int)
    p_egs_cover.add_argument("--limit", type=int, default=0)
    p_egs_cover.add_argument("--only", type=str,
                             choices=["getchu", "dlsite", "dmm", "official", "vndb"],
                             default="", help="只跑指定来源（调试用，不做跨档兜底）")
    p_egs_cover.add_argument("--interval", type=float, default=1.0)
    p_egs_cover.add_argument("--dir-limit", type=int, default=0, dest="dir_limit")
    p_egs_cover.add_argument("--coverage", action="store_true")
    p_egs_cover.add_argument("--optimize", action="store_true", help="只把已有原图转 webp")
    p_egs_cover.add_argument("--delete-originals", action="store_true", dest="delete_originals")
    p_egs_cover.add_argument("--force", action="store_true")
    p_egs_cover.add_argument("--execute", action="store_true")
    p_egs_cover.add_argument("--db", type=str)
    p_egs_cover.set_defaults(func=cmd_egs_cover)

    p_egs_overview = egs_sub.add_parser("overview")
    p_egs_overview.add_argument("--db", type=str)
    p_egs_overview.set_defaults(func=cmd_egs_overview)

    p_egs_status = egs_sub.add_parser("status")
    p_egs_status.add_argument("--db", type=str, dest="db")
    p_egs_status.set_defaults(func=cmd_egs_status)

    p_egs_artifact_audit = egs_sub.add_parser(
        "artifact_audit", help="残留 artifact link 巡检（只读，输出四类+孤儿）")
    p_egs_artifact_audit.add_argument("--db", type=str, dest="db")
    p_egs_artifact_audit.add_argument(
        "--strict", action="store_true",
        help="存在四类残留时以退出码 1 结束，供巡检脚本使用")
    p_egs_artifact_audit.set_defaults(func=cmd_egs_artifact_audit)

    p_egs_extids = egs_sub.add_parser(
        "external-ids", help="外部 ID 映射表：回填/对账/查询（backfill 默认 dry-run）")
    p_egs_extids.add_argument("--action", type=str,
                              choices=["backfill", "audit", "get", "resolve"],
                              default="audit")
    p_egs_extids.add_argument("--limit", type=int, default=0)
    p_egs_extids.add_argument("--execute", action="store_true",
                              help="backfill 时真正写入（默认只统计）")
    p_egs_extids.add_argument("--egs-id", type=int, dest="egs_id")
    p_egs_extids.add_argument("--source", type=str, default="")
    p_egs_extids.add_argument("--external-id", type=str, dest="external_id", default="")
    p_egs_extids.add_argument("--db", type=str)
    p_egs_extids.set_defaults(func=cmd_egs_external_ids)


    p_egs_games = egs_sub.add_parser("games")
    p_egs_games.add_argument("--page", type=int, default=1)
    p_egs_games.add_argument("--per-page", type=int, default=50, dest="per_page")
    p_egs_games.add_argument("--year", type=int)
    p_egs_games.add_argument("--month", type=int)
    p_egs_games.add_argument("--q", type=str, default="")
    p_egs_games.add_argument("--brand-kind", type=str, dest="brand_kind", default="")
    p_egs_games.add_argument("--review", type=str, choices=["", "pending"], default="")
    p_egs_games.add_argument("--stage", type=str,
                             choices=["", "downloaded", "linked", "none", "failed"], default="")
    p_egs_games.add_argument("--db", type=str)
    p_egs_games.set_defaults(func=cmd_egs_games)

    p_egs_magnet = egs_sub.add_parser("magnet")
    p_egs_magnet.add_argument("--year", type=int, required=True)
    p_egs_magnet.add_argument("--month", type=int)
    p_egs_magnet.add_argument("--rescan", action="store_true",
                              help="额外重扫到期记录（按作品年龄 30/90/180/365 天）")
    p_egs_magnet.add_argument("--rescan-limit", type=int, default=0, dest="rescan_limit",
                              help="本次最多重扫多少条（0=不限）")
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
    p_egs_update.add_argument("--new-resource-kind", type=str, dest="new_resource_kind")
    p_egs_update.add_argument("--db", type=str)
    p_egs_update.set_defaults(func=cmd_egs_update)

    p_egs_delete = egs_sub.add_parser("delete")
    p_egs_delete.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_egs_delete.add_argument("--db", type=str)
    p_egs_delete.set_defaults(func=cmd_egs_delete)

    p_egs_organize_confirm = egs_sub.add_parser("organize_confirm")
    p_egs_organize_confirm.add_argument("--date", required=True)
    p_egs_organize_confirm.add_argument("--name", required=True)
    p_egs_organize_confirm.add_argument("--db", type=str)
    p_egs_organize_confirm.set_defaults(func=cmd_egs_organize_confirm)

    p_egs_organize_issues = egs_sub.add_parser("organize_issues")
    p_egs_organize_issues.add_argument("--all", action="store_true")
    p_egs_organize_issues.add_argument("--db", type=str)
    p_egs_organize_issues.set_defaults(func=cmd_egs_organize_issues)

    p_egs_organize_issue_resolve = egs_sub.add_parser("organize_issue_resolve")
    p_egs_organize_issue_resolve.add_argument("--id", type=int, required=True)
    p_egs_organize_issue_resolve.add_argument("--note", type=str, help="关闭结论，写入 issue 的 message")
    p_egs_organize_issue_resolve.add_argument("--db", type=str)
    p_egs_organize_issue_resolve.set_defaults(func=cmd_egs_organize_issue_resolve)

    p_egs_organize_issue_reject = egs_sub.add_parser("organize_issue_reject")
    p_egs_organize_issue_reject.add_argument("--id", type=int, required=True)
    p_egs_organize_issue_reject.add_argument("--db", type=str)
    p_egs_organize_issue_reject.set_defaults(func=cmd_egs_organize_issue_reject)

    p_egs_organize_issue_reject_month = egs_sub.add_parser("organize_issue_reject_month")
    p_egs_organize_issue_reject_month.add_argument("--id", type=int, required=True)
    p_egs_organize_issue_reject_month.add_argument("--db", type=str)
    p_egs_organize_issue_reject_month.set_defaults(func=cmd_egs_organize_issue_reject_month)

    p_egs_torrent_meta_backfill = egs_sub.add_parser("torrent_meta_backfill")
    p_egs_torrent_meta_backfill.add_argument("--year", type=int, default=0)
    p_egs_torrent_meta_backfill.add_argument("--limit", type=int, default=0)
    p_egs_torrent_meta_backfill.add_argument("--force", action="store_true",
                                             help="已回填的行也重新下载覆盖")
    p_egs_torrent_meta_backfill.add_argument("--db", type=str)
    p_egs_torrent_meta_backfill.set_defaults(func=cmd_egs_torrent_meta_backfill)

    p_egs_retry_magnet = egs_sub.add_parser("retry_magnet")
    p_egs_retry_magnet.add_argument("--egs-id", type=int, required=True, dest="egs_id")
    p_egs_retry_magnet.add_argument("--db", type=str)
    p_egs_retry_magnet.set_defaults(func=cmd_egs_retry_magnet)

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
    p_egs_review_decide.add_argument("--resource-kind", type=str, dest="resource_kind",
                                     choices=["", "collection_dlc"])
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

    p_prune = sub.add_parser("prune", help="清理任务记录与 db 快照（默认只预览）")
    p_prune.add_argument("--keep", type=int, default=50, help="任务记录保留最新 N 个")
    p_prune.add_argument("--days", type=int, default=14, help="任务记录保留最近 N 天")
    p_prune.add_argument("--db-keep", type=int, default=3, dest="db_keep",
                         help="db 快照保留最新 N 个")
    p_prune.add_argument("--db-days", type=int, default=7, dest="db_days",
                         help="db 快照保留最近 N 天")
    p_prune.add_argument("--requested-by", default="cli", dest="requested_by")
    p_prune.add_argument("--execute", action="store_true", help="真正删除（默认预览）")
    p_prune.set_defaults(func=cmd_prune)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
