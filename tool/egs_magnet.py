"""EGS 磁链获取核心。

流程（幂等可重入）:
  1. 取 egs_games 中 link 为空的行（按 PC + 18禁已入库清单）
  2. 逐个到 sukebei.nyaa 搜索候选（多轮关键词放宽），解析标题/日期/大小/磁链
  3. egs_match.score_candidate 评分，候选全部落库 egs_nyaa_candidates
  4. 最高分 >= THRESHOLD → 回填 egs_games.link/nyaa_name/size/infohash_hex
  5. egs_nyaa_search_log 记录搜索史：重跑时已搜索且无结果的行自动跳过（零网络请求）

说明：
- 以 egs_id 为稳定主键；搜索历史/候选都挂在 egs_id 上，避免后续搬月/改名造成重复搜索。
- 这阶段只做“下载链接获取”，不做搬月；后续可基于 dn 继续处理 release/date/name。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from tool.egs_core import open_egs_db
from tool.egs_match import THRESHOLD, extract_infohash, select_best

SUKEBEI_URL = "https://sukebei.nyaa.si/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
REQUEST_INTERVAL = 2.5
MAX_PER_QUERY = 10


def ensure_egs_magnet_schema(conn: sqlite3.Connection) -> None:
    """建立 EGS 磁链候选与搜索历史表。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egs_nyaa_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            egs_id INTEGER NOT NULL,
            date TEXT,
            name TEXT,
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
            UNIQUE(egs_id, infohash_hex)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egs_nyaa_search_log (
            egs_id INTEGER PRIMARY KEY,
            date TEXT,
            name TEXT,
            result_count INTEGER,
            best_score REAL,
            selected_infohash TEXT,
            tried_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_egs_nyaa_candidates_egs_id ON egs_nyaa_candidates(egs_id)")
    conn.commit()


def _search_once(session: requests.Session, query: str, logger: logging.Logger):
    """单轮 sukebei 搜索，失败重试 3 次。"""
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


def _parse_result_page(html: str) -> list[dict]:
    """解析 sukebei 列表页。"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
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
        link_el = next(
            (a for a in cells[2].select("a[href]") if "magnet:?xt=urn:btih:" in (a.attrs.get("href") or "")),
            None,
        )
        magnet = link_el.attrs["href"] if link_el else ""
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


def search_candidates(session: requests.Session, name: str, company: str,
                      logger: logging.Logger) -> list[dict]:
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


def _publisher_of(title: str) -> str | None:
    tl = (title or "").lower()
    if "girlcelly" in tl:
        return "girlcelly"
    if "2d.g.f." in tl or "2dgf" in re.sub(r"\s+", "", tl):
        return "2D.G.F."
    return None


def _save_candidates(conn: sqlite3.Connection, egs_id: int, date: str, name: str,
                     cands: list[dict], best_key: str | None) -> None:
    """候选落库，带评分与选中标记。"""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    for c in cands:
        conn.execute(
            """
            INSERT INTO egs_nyaa_candidates
                (egs_id, date, name, nyaa_title, nyaa_date, size, magnet,
                 infohash_hex, view_url, publisher, score, score_detail,
                 fetched_at, selected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(egs_id, infohash_hex) DO UPDATE SET
                date=excluded.date,
                name=excluded.name,
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
            (egs_id, date, name, c.get("nyaa_title"), c.get("nyaa_date"),
             c.get("size"), c.get("magnet"), c.get("infohash_hex"),
             c.get("view_url"), _publisher_of(c.get("nyaa_title")), c.get("score"),
             json.dumps(c.get("score_detail") or {}, ensure_ascii=False),
             now_str, 1 if best_key and c.get("infohash_hex") == best_key else 0),
        )


def process_game(conn: sqlite3.Connection, session: requests.Session, row,
                 logger: logging.Logger, force: bool = False) -> tuple[str, dict | None]:
    """处理单条 EGS 游戏行。row 需含 egs_id,date,name,company,release_ts。"""
    egs_id, date, name, company, release_ts = (
        row["egs_id"], row["date"], row["name"], row["company"], row["release_ts"]
    )
    if not force:
        logged = conn.execute(
            "SELECT selected_infohash FROM egs_nyaa_search_log WHERE egs_id=?",
            (egs_id,),
        ).fetchone()
        if logged:
            return "skip_cache", None

    game = {"name": name, "company": company or "", "date": date,
            "release_date": release_ts}
    cands = search_candidates(session, name, company or "", logger)
    best, best_score, best_detail = select_best(game, cands, THRESHOLD)
    best_key = best.get("infohash_hex") if best else None
    _save_candidates(conn, egs_id, date, name, cands, best_key)

    result = {
        "egs_id": egs_id,
        "date": date,
        "name": name,
        "candidates": len(cands),
        "best_score": best_score if cands else 0.0,
    }
    tried_at = time.strftime("%Y-%m-%d %H:%M:%S")
    if best:
        conn.execute(
            """
            UPDATE egs_games
               SET link=?, nyaa_name=?, size=?, infohash_hex=?,
                   updated_at=?
             WHERE egs_id=?
            """,
            (best.get("magnet"), best.get("nyaa_title"), best.get("size"),
             extract_infohash(best.get("magnet")), tried_at, egs_id),
        )
        conn.execute(
            """
            INSERT INTO egs_nyaa_search_log
                (egs_id, date, name, result_count, best_score, selected_infohash, tried_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(egs_id) DO UPDATE SET
                date=excluded.date, name=excluded.name,
                result_count=excluded.result_count, best_score=excluded.best_score,
                selected_infohash=excluded.selected_infohash, tried_at=excluded.tried_at
            """,
            (egs_id, date, name, len(cands), best_score, best_key, tried_at),
        )
        conn.commit()
        result["selected_title"] = best.get("nyaa_title")
        result["selected_infohash"] = best_key
        logger.info("SELECTED %s | %s | score=%s detail=%s", name[:40],
                    (best.get("nyaa_title") or "")[:60], best_score, best_detail)
        return "selected", result

    conn.execute(
        """
        INSERT INTO egs_nyaa_search_log
            (egs_id, date, name, result_count, best_score, selected_infohash, tried_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        ON CONFLICT(egs_id) DO UPDATE SET
            date=excluded.date, name=excluded.name,
            result_count=excluded.result_count, best_score=excluded.best_score,
            selected_infohash=NULL, tried_at=excluded.tried_at
        """,
        (egs_id, date, name, len(cands), best_score if cands else None, tried_at),
    )
    conn.commit()
    status = "no_result" if not cands else "low_score"
    logger.info("%s %s | candidates=%s max_score=%s", status.upper(), name[:40],
                len(cands), best_score)
    return status, result


def pending_rows(conn: sqlite3.Connection, year: int, month: int | None = None,
                 force: bool = False, limit: int = 0) -> list[sqlite3.Row]:
    """取待搜索的 EGS 行，默认跳过搜索历史。"""
    sql = """
        SELECT egs_id, date, name, company, release_ts
          FROM egs_games
         WHERE substr(date,1,4)=?
           AND (link IS NULL OR link='')
           AND (release_ts IS NULL OR release_ts <= date('now','localtime'))
    """
    params: list = [str(year)]
    if month:
        sql += " AND CAST(substr(date,6) AS INTEGER)=?"
        params.append(int(month))
    if not force:
        sql += """
           AND NOT EXISTS (
               SELECT 1 FROM egs_nyaa_search_log l
                WHERE l.egs_id = egs_games.egs_id
           )
        """
    sql += " ORDER BY date, release_ts, egs_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def run_magnet(year: int, month: int | None = None, force: bool = False,
               limit: int = 0, db_path: str | None = None,
               logger: logging.Logger | None = None) -> dict:
    """同步执行一轮 EGS 磁链搜索。"""
    own_logger = logger is None
    if own_logger:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s - %(levelname)s - %(message)s")
        logger = logging.getLogger("egs_magnet")

    conn = open_egs_db(db_path)
    try:
        ensure_egs_magnet_schema(conn)
        rows = pending_rows(conn, year, month=month, force=force, limit=limit)
        status = {
            "year": year, "month": month, "force": force, "limit": limit,
            "total": len(rows), "selected": 0, "low_score": 0,
            "no_result": 0, "skip_cache": 0, "error": 0, "results": [],
        }
        session = requests.Session()
        session.headers.update(HEADERS)
        for row in rows:
            try:
                st, result = process_game(conn, session, row, logger, force=force)
            except Exception as e:  # noqa: BLE001
                st = "error"
                result = {
                    "egs_id": row["egs_id"], "date": row["date"],
                    "name": row["name"], "error": str(e),
                }
                logger.exception("处理失败 %s / %s", row["date"], row["name"])
            status[st] = status.get(st, 0) + 1
            if result:
                status["results"].append(result)
            # 保持对 sukebei 的礼貌间隔，避免 429
            time.sleep(REQUEST_INTERVAL)
        return status
    finally:
        conn.close()
