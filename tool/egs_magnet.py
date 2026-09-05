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
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from tool.egs_core import open_egs_db
from tool.egs_match import MAX_SCORE, THRESHOLD, extract_infohash, select_best

SUKEBEI_URL = "https://sukebei.nyaa.si/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
REQUEST_INTERVAL = 2.5
MAX_PER_QUERY = 10
REQUEST_TIMEOUT = 15
MAX_ATTEMPTS = 2          # 原始请求 + 重试 1 次
TIMEOUT_ABORT_LIMIT = 20  # 累计超时达到该值则终止本轮


class TimeoutLimitExceeded(Exception):
    """sukebei 累计超时超过阈值，用于终止整轮任务。"""


_timeout_count = 0


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
            tried_at TEXT,
            review_status TEXT,
            reviewed_at TEXT,
            review_note TEXT
        )
        """
    )
    for column, decl in (
        ("review_status", "TEXT"),
        ("reviewed_at", "TEXT"),
        ("review_note", "TEXT"),
    ):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(egs_nyaa_search_log)")}
        if column not in cols:
            conn.execute(f"ALTER TABLE egs_nyaa_search_log ADD COLUMN {column} {decl}")
    conn.execute(
        """
        UPDATE egs_nyaa_search_log
           SET review_status = CASE
               WHEN COALESCE(result_count, 0) > 0 AND selected_infohash IS NULL THEN 'pending'
               ELSE 'none'
           END
         WHERE review_status IS NULL
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_egs_nyaa_candidates_egs_id ON egs_nyaa_candidates(egs_id)")
    conn.commit()


class SearchStopped(Exception):
    """Cooperative stop, including during rate-limit waits."""


class RequestPacer:
    """One request-start interval across queries, games, retries and months."""
    def __init__(self, should_stop=None):
        self.should_stop = should_stop
        self.next_request_at = 0.0
        self.metrics = {"requests": 0, "network_seconds": 0.0, "wait_seconds": 0.0,
                        "retries": 0, "http_429": 0, "timeouts": 0, "early_stops": 0}

    def check_stop(self):
        if self.should_stop and self.should_stop():
            raise SearchStopped()

    def defer(self, seconds):
        self.next_request_at = max(self.next_request_at, time.monotonic() + seconds)

    def before_request(self):
        self.check_stop()
        started = time.monotonic()
        try:
            while True:
                remaining = self.next_request_at - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, .2))
                self.check_stop()
        finally:
            self.metrics["wait_seconds"] += time.monotonic() - started
        self.check_stop()
        self.next_request_at = time.monotonic() + REQUEST_INTERVAL
        self.metrics["requests"] += 1


def _retry_after(response, fallback):
    from email.utils import parsedate_to_datetime
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            seconds = fallback
    return max(fallback, seconds)


def _search_once(session: requests.Session, query: str, logger: logging.Logger):
    """Rate-limited search. Exhausted failures propagate and are never empty results."""
    global _timeout_count
    pacer = getattr(session, "_egs_pacer", None)
    if pacer is None:
        pacer = session._egs_pacer = RequestPacer()
    url = SUKEBEI_URL + "?f=0&c=1_3&q=" + quote(query)
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        pacer.before_request()
        if attempt:
            pacer.metrics["retries"] += 1
        started = time.monotonic()
        try:
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
            finally:
                pacer.metrics["network_seconds"] += time.monotonic() - started
            pacer.check_stop()
            if resp.status_code == 429:
                pacer.metrics["http_429"] += 1
                wait = _retry_after(resp, 15 * (attempt + 1))
                pacer.defer(wait)
                last_err = RuntimeError("HTTP 429")
                logger.warning("sukebei 429，后续请求等待%ss: %s", wait, query[:40])
                continue
            resp.raise_for_status()
            return _parse_result_page(resp.text)
        except requests.Timeout as exc:
            _timeout_count += 1
            pacer.metrics["timeouts"] += 1
            last_err = exc
            logger.warning("超时(第%s次, 累计%s/%s) %s: %s", attempt + 1,
                           _timeout_count, TIMEOUT_ABORT_LIMIT, query[:40], exc)
            if _timeout_count >= TIMEOUT_ABORT_LIMIT:
                raise TimeoutLimitExceeded(f"累计超时达到 {_timeout_count} 次，终止本轮 EGS 磁链获取") from exc
            if attempt + 1 < MAX_ATTEMPTS:
                pacer.defer(3)
        except (SearchStopped, TimeoutLimitExceeded):
            raise
        except Exception as exc:
            last_err = exc
            logger.warning("搜索失败(第%s次) %s: %s", attempt + 1, query[:40], exc)
            if attempt + 1 < MAX_ATTEMPTS:
                pacer.defer(3 * (attempt + 1))
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
                      logger: logging.Logger, game=None) -> list[dict]:
    """Preserve query ordering and candidate ties; stop only at the score ceiling."""
    queries = []
    if company:
        queries.append(f"{name} {company}")
    queries.append(name)
    stripped = re.sub(r"[^\w\s]", " ", name)
    if stripped.strip() and stripped.strip() != name:
        queries.append(stripped)

    merged = {}
    for query in dict.fromkeys(queries):
        # Do not cache incomplete searches as no-result/low-score outcomes.
        items = _search_once(session, query, logger)
        for item in items:
            merged.setdefault(item["infohash_hex"], item)
        candidates = list(merged.values())[:MAX_PER_QUERY]
        if game and candidates:
            best, score, _ = select_best(game, candidates, THRESHOLD)
            if best and score >= MAX_SCORE and extract_infohash(best.get("magnet")):
                pacer = getattr(session, "_egs_pacer", None)
                if pacer:
                    pacer.metrics["early_stops"] += 1
                break
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
    cands = search_candidates(session, name, company or "", logger, game=game)
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
                (egs_id, date, name, result_count, best_score, selected_infohash, tried_at,
                 review_status, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'none', ?)
            ON CONFLICT(egs_id) DO UPDATE SET
                date=excluded.date, name=excluded.name,
                result_count=excluded.result_count, best_score=excluded.best_score,
                selected_infohash=excluded.selected_infohash, tried_at=excluded.tried_at,
                review_status='none', reviewed_at=excluded.reviewed_at, review_note=NULL
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
            (egs_id, date, name, result_count, best_score, selected_infohash, tried_at,
             review_status, reviewed_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
        ON CONFLICT(egs_id) DO UPDATE SET
            date=excluded.date, name=excluded.name,
            result_count=excluded.result_count, best_score=excluded.best_score,
            selected_infohash=NULL, tried_at=excluded.tried_at,
            review_status=excluded.review_status, reviewed_at=excluded.reviewed_at,
            review_note=NULL
        """,
        (egs_id, date, name, len(cands), best_score if cands else None, tried_at,
         'pending' if cands else 'none', tried_at),
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
               logger: logging.Logger | None = None, should_stop=None, progress=None, pacer=None) -> dict:
    """同步执行一轮 EGS 磁链搜索。"""
    global _timeout_count
    own_logger = logger is None
    if own_logger:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s - %(levelname)s - %(message)s")
        logger = logging.getLogger("egs_magnet")

    _timeout_count = 0
    started = time.monotonic()
    pacer = pacer or RequestPacer(should_stop)
    pacer.should_stop = should_stop
    baseline = pacer.metrics.copy()
    session = None
    conn = open_egs_db(db_path)
    try:
        # Fail before any network traffic if SQLite/WAL cannot be written.
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE egs_games SET downloaded=downloaded WHERE 0")
        finally:
            conn.rollback()
        ensure_egs_magnet_schema(conn)
        rows = pending_rows(conn, year, month=month, force=force, limit=limit)
        scope_sql = "SELECT COUNT(*) AS total, SUM(link IS NOT NULL AND link != '') AS linked, SUM((link IS NULL OR link = '') AND release_ts > date('now','localtime')) AS unreleased, SUM((link IS NULL OR link = '') AND (release_ts IS NULL OR release_ts <= date('now','localtime')) AND EXISTS(SELECT 1 FROM egs_nyaa_search_log l WHERE l.egs_id=g.egs_id)) AS cached FROM egs_games g WHERE substr(date,1,4)=?"
        scope_params = [str(year)]
        if month:
            scope_sql += " AND CAST(substr(date,6,2) AS INTEGER)=?"
            scope_params.append(int(month))
        scope = conn.execute(scope_sql, scope_params).fetchone()
        status = {
            "year": year, "month": month, "force": force, "limit": limit,
            "scope_total": int(scope["total"] or 0),
            "skip_linked": int(scope["linked"] or 0),
            "skip_unreleased": int(scope["unreleased"] or 0),
            "skip_history": 0 if force else int(scope["cached"] or 0),
            "total": len(rows), "selected": 0, "low_score": 0,
            "no_result": 0, "skip_cache": 0, "error": 0,
            "timeout_count": 0, "timeout_aborted": False, "results": [],
        }
        session = requests.Session()
        session.headers.update(HEADERS)
        session._egs_pacer = pacer
        for index, row in enumerate(rows):
            if should_stop and should_stop():
                status["stopped"] = True
                break
            if progress:
                progress(row["name"], index, len(rows))
            try:
                st, result = process_game(conn, session, row, logger, force=force)
            except SearchStopped:
                status["stopped"] = True
                break
            except TimeoutLimitExceeded as e:
                st = "error"
                result = {
                    "egs_id": row["egs_id"], "date": row["date"],
                    "name": row["name"], "error": str(e),
                }
                logger.error("处理失败 %s / %s: %s", row["date"], row["name"], e)
                status["error"] += 1
                status["results"].append(result)
                status["timeout_count"] = _timeout_count
                status["timeout_aborted"] = True
                break
            except sqlite3.DatabaseError:
                conn.rollback()
                raise
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                st = "error"
                result = {
                    "egs_id": row["egs_id"], "date": row["date"],
                    "name": row["name"], "error": str(e),
                }
                logger.exception("处理失败 %s / %s", row["date"], row["name"])
            status["timeout_count"] = _timeout_count
            status[st] = status.get(st, 0) + 1
            if result:
                status["results"].append(result)
        status["metrics"] = {key: round(value - baseline[key], 3)
                             for key, value in pacer.metrics.items()}
        status["elapsed_seconds"] = round(time.monotonic() - started, 3)
        logger.info("NYAA_SUMMARY %s", json.dumps({key: value for key, value in status.items() if key != "results"}, ensure_ascii=False))
        return status
    finally:
        if session is not None:
            session.close()
        conn.close()


def review_detail(egs_id: int, db_path: str | None = None) -> dict:
    """取单条 EGS 记录的待审核候选。"""
    from tool.egs_core import open_egs_db

    conn = open_egs_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_egs_magnet_schema(conn)
        game = conn.execute(
            """
            SELECT g.egs_id, g.date, g.name, g.company, g.release_ts,
                   g.link, g.nyaa_name, l.review_status, l.best_score
              FROM egs_games g
              LEFT JOIN egs_nyaa_search_log l ON l.egs_id = g.egs_id
             WHERE g.egs_id=?
            """,
            (int(egs_id),),
        ).fetchone()
        if not game:
            return {"success": False, "message": "EGS记录不存在"}
        candidates = conn.execute(
            """
            SELECT id, nyaa_title, nyaa_date, size, magnet, infohash_hex,
                   view_url, publisher, score, score_detail, selected
              FROM egs_nyaa_candidates
             WHERE egs_id=?
             ORDER BY score DESC, fetched_at DESC, id DESC
            """,
            (int(egs_id),),
        ).fetchall()
        cand_list = [dict(c) for c in candidates]
        release_dt = None
        if game["release_ts"]:
            try:
                release_dt = datetime.fromisoformat(str(game["release_ts"])[:10])
            except ValueError:
                release_dt = None
        cand_dates = []
        for c in cand_list:
            try:
                if c.get("nyaa_date"):
                    cand_dates.append(datetime.fromisoformat(str(c["nyaa_date"])[:10]))
            except ValueError:
                pass
        cross_year_suspect = bool(
            cand_dates
            and release_dt
            and max(cand_dates) < release_dt - timedelta(days=365)
        )
        history = []
        if cross_year_suspect:
            try:
                from tool.egs_core import fetch_egs_same_name_history
                history = [
                    {
                        "egs_id": int(r.get("egs_id") or 0),
                        "egs_date": r.get("egs_date") or "",
                        "egs_name": r.get("egs_name") or "",
                        "egs_company": r.get("egs_company") or "",
                        "brand_kind": r.get("brand_kind") or "",
                        "official_url": r.get("official_url") or "",
                    }
                    for r in fetch_egs_same_name_history(game["name"])
                    if int(r.get("egs_id") or 0) != int(egs_id)
                ]
            except Exception:
                history = []
        return {
            "success": True,
            "game": dict(game),
            "candidates": cand_list,
            "cross_year_suspect": cross_year_suspect,
            "history": history,
        }
    finally:
        conn.close()


def decide_review(egs_id: int, decision: str, candidate_id: int | None = None,
                  manual_magnet: str | None = None, manual_nyaa_name: str | None = None,
                  note: str | None = None, db_path: str | None = None) -> dict:
    """审核低分候选：通过后回填磁链，拒绝后标记不可下载。"""
    from tool.egs_core import open_egs_db

    egs_id = int(egs_id)
    decision = str(decision).lower()
    if decision not in ("approve", "reject", "reopen"):
        return {"success": False, "message": "decision 须为 approve/reject/reopen"}

    conn = open_egs_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_egs_magnet_schema(conn)
        game = conn.execute("SELECT egs_id, name FROM egs_games WHERE egs_id=?", (egs_id,)).fetchone()
        if not game:
            return {"success": False, "message": "EGS记录不存在"}
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if decision == "approve":
            magnet = ""
            nyaa_name = ""
            size = None
            infohash = None
            if candidate_id:
                cand = conn.execute(
                    """
                    SELECT nyaa_title, nyaa_date, size, magnet, infohash_hex
                      FROM egs_nyaa_candidates
                     WHERE id=? AND egs_id=?
                    """,
                    (int(candidate_id), egs_id),
                ).fetchone()
                if not cand:
                    return {"success": False, "message": "候选磁链不存在"}
                magnet = cand["magnet"] or ""
                nyaa_name = cand["nyaa_title"] or ""
                size = cand["size"]
                infohash = cand["infohash_hex"] or extract_infohash(magnet)
            else:
                magnet = str(manual_magnet or "").strip()
                nyaa_name = str(manual_nyaa_name or "").strip()
                infohash = extract_infohash(magnet)
            if not magnet or "magnet:?xt=urn:btih:" not in magnet:
                return {"success": False, "message": "磁链格式无效"}
            if not infohash:
                return {"success": False, "message": "磁链缺少 infohash"}

            conn.execute("UPDATE egs_nyaa_candidates SET selected=0 WHERE egs_id=?", (egs_id,))
            if candidate_id:
                conn.execute(
                    "UPDATE egs_nyaa_candidates SET selected=1 WHERE id=? AND egs_id=?",
                    (int(candidate_id), egs_id),
                )
            conn.execute(
                """
                UPDATE egs_games
                   SET link=?, nyaa_name=?, size=?, infohash_hex=?, updated_at=?
                 WHERE egs_id=?
                """,
                (magnet, nyaa_name or None, size, infohash, now_str, egs_id),
            )
            conn.execute(
                """
                INSERT INTO egs_nyaa_search_log
                    (egs_id, date, name, result_count, best_score, selected_infohash, tried_at,
                     review_status, reviewed_at, review_note)
                SELECT g.egs_id, g.date, g.name,
                       COALESCE(l.result_count, (SELECT COUNT(*) FROM egs_nyaa_candidates c WHERE c.egs_id=g.egs_id), 0),
                       COALESCE(l.best_score, (SELECT MAX(score) FROM egs_nyaa_candidates c WHERE c.egs_id=g.egs_id), 0),
                       ?, ?,
                       'approved', ?, ?
                  FROM egs_games g LEFT JOIN egs_nyaa_search_log l ON l.egs_id=g.egs_id
                 WHERE g.egs_id=?
                ON CONFLICT(egs_id) DO UPDATE SET
                    selected_infohash=excluded.selected_infohash,
                    tried_at=excluded.tried_at,
                    review_status='approved', reviewed_at=excluded.reviewed_at,
                    review_note=excluded.review_note
                """,
                (infohash, now_str, now_str, note, egs_id),
            )
        elif decision == "reject":
            conn.execute("UPDATE egs_nyaa_candidates SET selected=0 WHERE egs_id=?", (egs_id,))
            conn.execute(
                """
                UPDATE egs_nyaa_search_log
                   SET review_status='rejected', reviewed_at=?, review_note=?,
                       selected_infohash=NULL
                 WHERE egs_id=?
                """,
                (now_str, note, egs_id),
            )
        else:
            conn.execute(
                """
                UPDATE egs_nyaa_search_log
                   SET review_status='pending', reviewed_at=NULL, review_note=NULL
                 WHERE egs_id=?
                """,
                (egs_id,),
            )
        conn.commit()
        return {"success": True, "message": "审核已更新", "egs_id": egs_id, "decision": decision}
    finally:
        conn.close()
