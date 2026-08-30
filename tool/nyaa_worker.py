"""sukebei.nyaa 磁链获取 worker（Phase 2）。

流程（幂等可重入）:
  1. 取 getchu_games 中 link 为空的行（Phase 1 去重后一行一作品）
  2. 逐个到 sukebei.nyaa 搜索候选（多轮关键词放宽），解析标题/日期/大小/磁链
  3. nyaa_match.score_candidate 评分，候选全部落库 nyaa_candidates
  4. 最高分 >= THRESHOLD → 回填 getchu_games.link/nyaa_name/size/infohash_hex
  5. nyaa_search_log 记录搜索史：重跑时已搜索且无结果的行自动跳过（零网络请求）

用法:
    python3 tool/nyaa_worker.py --year 2026 [--month 1] [--force] [--limit N]
"""
import argparse
import json
import logging
import os
import re
import signal
import sys
import time
import traceback
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from tool.core import open_db, ensure_getchu_schema
from tool.nyaa_match import THRESHOLD, extract_infohash, select_best
from tool.runtime import (
    cleanup_old_logs,
    daily_log_path,
    now_ts,
    runtime_paths,
    write_json_atomic,
)

_stop_requested = False
SUKEBEI_URL = "https://sukebei.nyaa.si/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
REQUEST_INTERVAL = 2.5
MAX_PER_QUERY = 10


def _handle_stop(signum, frame):
    global _stop_requested
    _stop_requested = True


def ensure_nyaa_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nyaa_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            nyaa_title TEXT,
            nyaa_date TEXT,
            size TEXT,
            magnet TEXT,
            infohash_hex TEXT,
            view_url TEXT,
            publisher TEXT,
            score REAL,
            score_detail TEXT,
            fetched_at TEXT,
            selected INTEGER DEFAULT 0,
            UNIQUE(date, name, infohash_hex)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nyaa_search_log (
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            result_count INTEGER,
            best_score REAL,
            selected_infohash TEXT,
            tried_at TEXT,
            PRIMARY KEY (date, name)
        )
        """
    )
    conn.commit()


def _search_once(session, query, logger):
    url = SUKEBEI_URL + "?f=0&c=1_3&q=" + quote(query)
    last_err = None
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=25)
            if resp.status_code == 429:
                wait = 15 * (attempt + 1)
                logger.warning("sukebei 429，等待%ss重试: %s", wait, query[:40])
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return _parse_result_page(resp.text)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("搜索失败(第%s次) %s: %s", attempt + 1, query[:40], e)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"搜索失败: {query[:40]}: {last_err}")


def _parse_result_page(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        # 标题
        link_views = cells[1].select('a[href*="view"]')
        if link_views:
            name_el = link_views[-1]
            title = name_el.attrs.get("title") or name_el.get_text(strip=True)
            view_url = name_el.attrs.get("href") or ""
        else:
            title = cells[1].get_text(strip=True)
            view_url = ""
        if view_url and view_url.startswith("/"):
            view_url = "https://sukebei.nyaa.si" + view_url
        # 磁链
        link_el = next(
            (a for a in cells[2].select("a[href]") if "magnet:?xt=urn:btih:" in (a.attrs.get("href") or "")),
            None,
        )
        magnet = link_el.attrs["href"] if link_el else ""
        # 大小/日期
        size = cells[3].get_text(strip=True)
        date_str = cells[4].get_text(strip=True)
        try:
            from datetime import datetime
            date_str = datetime.strptime(date_str, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            date_str = date_str or None
        if not title:
            continue
        out.append({
            "nyaa_title": title,
            "nyaa_date": date_str,
            "size": size or None,
            "magnet": magnet or None,
            "infohash_hex": extract_infohash(magnet) or ("sha1:" + re.sub(r"\W+", "", title)[:32]),
            "view_url": view_url or None,
        })
        if len(out) >= MAX_PER_QUERY:
            break
    return out


def search_candidates(session, name, company, logger):
    """多轮关键词搜索并合并去重。"""
    queries = []
    if company:
        queries.append(f"{name} {company}")
    queries.append(name)
    stripped = re.sub(r"[^\w\s]", " ", name)
    if stripped.strip() and stripped.strip() != name:
        queries.append(stripped)

    merged = {}
    for q in queries:
        if _stop_requested:
            break
        try:
            items = _search_once(session, q, logger)
        except RuntimeError as e:
            logger.error("%s", e)
            items = []
        for it in items:
            key = it["infohash_hex"]
            if key not in merged:
                merged[key] = it
        time.sleep(REQUEST_INTERVAL)
        if len(merged) >= MAX_PER_QUERY:
            break
    return list(merged.values())[:MAX_PER_QUERY]


def _publisher_of(title):
    tl = (title or "").lower()
    if "girlcelly" in tl:
        return "girlcelly"
    if "2d.g.f." in tl or "2dgf" in re.sub(r"\s+", "", tl):
        return "2D.G.F."
    return None


def _save_candidates(conn, date, name, cands, best_key):
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    for c in cands:
        conn.execute(
            """
            INSERT INTO nyaa_candidates
                (date, name, nyaa_title, nyaa_date, size, magnet, infohash_hex,
                 view_url, publisher, score, score_detail, fetched_at, selected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, name, infohash_hex) DO UPDATE SET
                nyaa_title=excluded.nyaa_title,
                nyaa_date=excluded.nyaa_date,
                size=excluded.size,
                magnet=excluded.magnet,
                view_url=excluded.view_url,
                publisher=excluded.publisher,
                score=excluded.score,
                score_detail=excluded.score_detail,
                fetched_at=excluded.fetched_at,
                selected=excluded.selected
            """,
            (date, name, c.get("nyaa_title"), c.get("nyaa_date"), c.get("size"),
             c.get("magnet"), c.get("infohash_hex"), c.get("view_url"),
             _publisher_of(c.get("nyaa_title")), c.get("score"),
             json.dumps(c.get("score_detail") or {}, ensure_ascii=False),
             now_str, 1 if best_key and c.get("infohash_hex") == best_key else 0),
        )


def process_game(conn, session, row, logger, force=False):
    """处理单游戏，返回 (status, result)。status: selected/low_score/no_result/error/skip_cache"""
    date, name, company, release_date = row
    # 幂等：已搜索过且非 force → 跳过
    if not force:
        logged = conn.execute(
            "SELECT selected_infohash FROM nyaa_search_log WHERE date=? AND name=?",
            (date, name),
        ).fetchone()
        if logged:
            return "skip_cache", None

    game = {"name": name, "company": company or "", "date": date, "release_date": release_date}
    cands = search_candidates(session, name, company or "", logger)
    best, best_score, best_detail = select_best(game, cands, THRESHOLD)

    best_key = best.get("infohash_hex") if best else None
    _save_candidates(conn, date, name, cands, best_key)

    result = {
        "date": date, "name": name, "candidates": len(cands),
        "best_score": best_score if cands else 0.0,
    }
    if best:
        conn.execute(
            """
            UPDATE getchu_games
            SET link=?, nyaa_name=?, size=?, infohash_hex=?
            WHERE date=? AND name=?
            """,
            (best.get("magnet"), best.get("nyaa_title"), best.get("size"),
             extract_infohash(best.get("magnet")), date, name),
        )
        result["selected_title"] = best.get("nyaa_title")
        conn.execute(
            """
            INSERT INTO nyaa_search_log (date, name, result_count, best_score, selected_infohash, tried_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, name) DO UPDATE SET
                result_count=excluded.result_count, best_score=excluded.best_score,
                selected_infohash=excluded.selected_infohash, tried_at=excluded.tried_at
            """,
            (date, name, len(cands), best_score, best_key, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        logger.info("SELECTED %s | %s | score=%s detail=%s", name[:40], (best.get("nyaa_title") or "")[:60], best_score, best_detail)
        return "selected", result

    conn.execute(
        """
        INSERT INTO nyaa_search_log (date, name, result_count, best_score, selected_infohash, tried_at)
        VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(date, name) DO UPDATE SET
            result_count=excluded.result_count, best_score=excluded.best_score,
            selected_infohash=NULL, tried_at=excluded.tried_at
        """,
        (date, name, len(cands), best_score if cands else None, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    status = "no_result" if not cands else "low_score"
    logger.info("%s %s | candidates=%s max_score=%s", status.upper(), name[:40], len(cands), best_score)
    return status, result


def _default_status():
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="忽略搜索历史强制重搜")
    parser.add_argument("--limit", type=int, default=0, help="最多处理N个游戏(0=不限)")
    args = parser.parse_args()

    paths = runtime_paths()
    os.makedirs(paths["status_dir"], exist_ok=True)
    os.makedirs(paths["log_dir"], exist_ok=True)
    if paths.get("log_auto_cleanup"):
        cleanup_old_logs(retention_days=paths.get("log_retention_days"))

    logging.basicConfig(
        filename=daily_log_path("nyaa"),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("nyaa_worker")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    conn = open_db()
    ensure_getchu_schema(conn)
    ensure_nyaa_schema(conn)

    sql = """
        SELECT date, name, company, release_date FROM getchu_games
        WHERE substr(date,1,4)=? AND (link IS NULL OR link='')
          AND (release_date IS NULL OR release_date <= date('now','localtime'))
    """
    params = [str(args.year)]
    if args.month:
        sql += " AND CAST(substr(date,6) AS INTEGER)=?"
        params.append(int(args.month))
    if not args.force:
        sql += """ AND NOT EXISTS (
            SELECT 1 FROM nyaa_search_log l WHERE l.date=getchu_games.date AND l.name=getchu_games.name
        )"""
    sql += " ORDER BY date, name"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql, params).fetchall()

    status = _default_status()
    status.update({
        "running": True,
        "pid": os.getpid(),
        "year": args.year,
        "month": args.month,
        "total": len(rows),
        "started_at": now_ts(),
        "updated_at": now_ts(),
    })
    write_json_atomic(paths["nyaa_status_path"], status)
    logger.info("nyaa worker 启动: year=%s month=%s total=%s force=%s", args.year, args.month, len(rows), args.force)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        for row in rows:
            if _stop_requested:
                status["stopped_reason"] = "signal"
                break
            date, name = row[0], row[1]
            status["current"] = {"date": date, "name": name}
            status["updated_at"] = now_ts()
            write_json_atomic(paths["nyaa_status_path"], status)

            try:
                st, result = process_game(conn, session, row, logger, force=args.force)
            except Exception as e:  # noqa: BLE001
                logger.error("处理失败 %s / %s: %s\n%s", date, name, e, traceback.format_exc())
                st, result = "error", {"date": date, "name": name, "error": str(e)}
                if str(e) not in status["errors"]:
                    status["errors"].append(str(e))

            status[st] = status.get(st, 0) + 1
            status["done"] += 1
            if result:
                status["results"].append(result)
                if len(status["results"]) > 500:
                    status["results"] = status["results"][-500:]
            status["updated_at"] = now_ts()
            write_json_atomic(paths["nyaa_status_path"], status)
    finally:
        status["running"] = False
        status["current"] = None
        status["updated_at"] = now_ts()
        write_json_atomic(paths["nyaa_status_path"], status)
        conn.close()
        logger.info("nyaa worker 结束: reason=%s", status.get("stopped_reason"))


if __name__ == "__main__":
    main()
