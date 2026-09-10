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

from tool.egs_core import open_egs_db, refresh_magnet_duplicates
from tool.egs_match import (
    MAX_SCORE,
    THRESHOLD,
    allows_english_candidate,
    extract_infohash,
    is_abnormally_short_name,
    select_best,
)
from tool.runtime import read_config

SUKEBEI_URL = "https://sukebei.nyaa.si/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
REQUEST_INTERVAL = 1.2          # 搜索请求默认间隔（秒），可被 config.json 覆盖
MIN_REQUEST_INTERVAL = 1.0      # 连续成功后可收缩到的最小间隔（约 1 req/s）
MAX_REQUEST_INTERVAL = 15.0     # 触发 429 后指数退避的上限
TORRENT_INTERVAL = 0.6          # .torrent 下载后使用的间隔（其余请求仍受全局间隔约束）
RECOVERY_STREAK = 10            # 连续成功多少次后尝试收缩一次间隔
RECOVERY_FACTOR = 0.8
BACKOFF_FACTOR = 2.0
EARLY_STOP_SCORE = 60.0         # 精确名称+公司命中且达到该分数时，跳过后续放宽查询
MAX_PER_QUERY = 10
REQUEST_TIMEOUT = 15
MAX_ATTEMPTS = 2          # 原始请求 + 重试 1 次
TIMEOUT_ABORT_LIMIT = 20  # 累计超时达到该值则终止本轮


def pacing_config():
    """读取可选的节流配置；缺失或非法时回退到模块默认值。"""
    raw = read_config() or {}

    def number(key, default, floor=0.05):
        try:
            return max(floor, float(raw.get(key, default)))
        except (TypeError, ValueError):
            return float(default)

    interval = number("magnet_request_interval", REQUEST_INTERVAL)
    min_interval = number("magnet_min_request_interval", MIN_REQUEST_INTERVAL)
    max_interval = number("magnet_max_request_interval", MAX_REQUEST_INTERVAL)
    torrent_interval = number("magnet_torrent_interval", TORRENT_INTERVAL)
    min_interval = min(min_interval, max_interval)
    interval = min(max(interval, min_interval), max_interval)
    torrent_interval = min(torrent_interval, interval)
    return {
        "interval": interval,
        "min_interval": min_interval,
        "max_interval": max_interval,
        "torrent_interval": torrent_interval,
    }


def early_stop_threshold():
    """提前结束后续放宽查询的分数阈值（magnet_early_stop_score）。

    夹在 THRESHOLD 与 MAX_SCORE 之间；设为 MAX_SCORE(65) 即恢复“只认满分”的旧行为。
    """
    raw = read_config() or {}
    try:
        value = float(raw.get("magnet_early_stop_score", EARLY_STOP_SCORE))
    except (TypeError, ValueError):
        value = EARLY_STOP_SCORE
    return min(max(value, THRESHOLD), MAX_SCORE)


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
    # egs_games 的种子 info 信息列（与 115 产物目录精确对应）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(egs_games)")}
        for column, decl in (
            ("torrent_name", "TEXT"),
            ("torrent_files", "TEXT"),
            ("torrent_size", "INTEGER"),
            ("download_failed", "INTEGER NOT NULL DEFAULT 0"),
            ("download_failed_at", "TEXT"),
            ("magnet_duplicate", "INTEGER NOT NULL DEFAULT 0"),
            ("duplicate_of_egs_id", "INTEGER"),
            ("submission_excluded", "INTEGER NOT NULL DEFAULT 0"),
            ("submission_excluded_reason", "TEXT"),
        ):
            if column not in cols:
                conn.execute(f"ALTER TABLE egs_games ADD COLUMN {column} {decl}")
        refresh_magnet_duplicates(conn)
    except sqlite3.OperationalError:
        # egs_games 尚未建立（如仅跑磁链模块的独立库）时跳过，由 ensure_egs_schema 负责
        pass
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
    # 拒绝即遗弃：清掉历史遗留的候选数据，避免继续作为审核对象展示。
    conn.execute(
        """
        DELETE FROM egs_nyaa_candidates
         WHERE egs_id IN (
             SELECT egs_id FROM egs_nyaa_search_log
              WHERE review_status = 'rejected'
         )
        """
    )
    # 零分通常只是宽松搜索带回的噪声，不进入审核档案。极短游戏名缺少足够的
    # 字符信号，仍保留零分候选供人工辨认。
    try:
        zero_rows = conn.execute(
            """
            SELECT c.id, c.egs_id, g.name
              FROM egs_nyaa_candidates c
              JOIN egs_games g ON g.egs_id = c.egs_id
             WHERE COALESCE(c.score, 0) = 0
            """
        ).fetchall()
        remove_ids = [row["id"] for row in zero_rows
                      if not is_abnormally_short_name(row["name"])]
        affected_egs_ids = sorted({row["egs_id"] for row in zero_rows
                                   if not is_abnormally_short_name(row["name"])})
        if remove_ids:
            conn.executemany(
                "DELETE FROM egs_nyaa_candidates WHERE id=?",
                ((candidate_id,) for candidate_id in remove_ids),
            )
            conn.executemany(
                """
                UPDATE egs_nyaa_search_log
                   SET result_count=(
                           SELECT COUNT(*) FROM egs_nyaa_candidates c
                            WHERE c.egs_id=egs_nyaa_search_log.egs_id
                       ),
                       best_score=(
                           SELECT MAX(c.score) FROM egs_nyaa_candidates c
                            WHERE c.egs_id=egs_nyaa_search_log.egs_id
                       )
                 WHERE egs_id=?
                """,
                ((egs_id,) for egs_id in affected_egs_ids),
            )
        conn.execute(
            """
            UPDATE egs_nyaa_search_log
               SET result_count=0, best_score=NULL, review_status='none',
                   reviewed_at=COALESCE(reviewed_at, tried_at)
             WHERE review_status='pending'
               AND NOT EXISTS (
                   SELECT 1 FROM egs_nyaa_candidates c
                    WHERE c.egs_id=egs_nyaa_search_log.egs_id
               )
            """
        )
    except sqlite3.OperationalError:
        pass
    # English 版候选与分数无关，默认不参与匹配和审核；只有 EGS 游戏名明确
    # 包含 English/英語 时例外。已选中或仍被游戏引用的旧记录留待单独审计。
    try:
        english_rows = conn.execute(
            """
            SELECT c.id, c.egs_id, c.nyaa_title, c.infohash_hex, c.selected,
                   g.name, g.infohash_hex AS game_infohash
              FROM egs_nyaa_candidates c
              JOIN egs_games g ON g.egs_id = c.egs_id
             WHERE lower(COALESCE(c.nyaa_title, '')) LIKE '%english%'
            """
        ).fetchall()
        remove_english = [
            row for row in english_rows
            if not allows_english_candidate(row["name"], row["nyaa_title"])
            and not bool(row["selected"])
            and str(row["infohash_hex"] or "").lower()
                != str(row["game_infohash"] or "").lower()
        ]
        if remove_english:
            conn.executemany(
                "DELETE FROM egs_nyaa_candidates WHERE id=?",
                ((row["id"],) for row in remove_english),
            )
            affected = sorted({row["egs_id"] for row in remove_english})
            conn.executemany(
                """
                UPDATE egs_nyaa_search_log
                   SET result_count=(
                           SELECT COUNT(*) FROM egs_nyaa_candidates c
                            WHERE c.egs_id=egs_nyaa_search_log.egs_id
                       ),
                       best_score=(
                           SELECT MAX(c.score) FROM egs_nyaa_candidates c
                            WHERE c.egs_id=egs_nyaa_search_log.egs_id
                       )
                 WHERE egs_id=?
                """,
                ((egs_id,) for egs_id in affected),
            )
        conn.execute(
            """
            UPDATE egs_nyaa_search_log
               SET result_count=0, best_score=NULL, review_status='none',
                   reviewed_at=COALESCE(reviewed_at, tried_at)
             WHERE review_status='pending'
               AND NOT EXISTS (
                   SELECT 1 FROM egs_nyaa_candidates c
                    WHERE c.egs_id=egs_nyaa_search_log.egs_id
               )
            """
        )
    except sqlite3.OperationalError:
        pass
    # 审核采用后同样只保留被采用的候选。
    conn.execute(
        """
        DELETE FROM egs_nyaa_candidates
         WHERE EXISTS (
             SELECT 1
               FROM egs_nyaa_search_log l
              WHERE l.egs_id = egs_nyaa_candidates.egs_id
                AND l.review_status = 'approved'
                AND l.selected_infohash IS NOT NULL
                AND l.selected_infohash != ''
                AND COALESCE(egs_nyaa_candidates.infohash_hex, '') != l.selected_infohash
         )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_egs_nyaa_candidates_egs_id ON egs_nyaa_candidates(egs_id)")
    conn.commit()


class SearchStopped(Exception):
    """Cooperative stop, including during rate-limit waits."""


class RequestPacer:
    """全局请求节流：默认间隔 + 429 指数退避 + 连续成功缓慢提速。

    搜索页与 .torrent 下载共用同一个实例，保证任意两次请求启动之间至少间隔
    「上一次请求设置的间隔」；.torrent 使用更短的间隔，避免每个种子都付一次
    完整的搜索间隔。
    """
    def __init__(self, should_stop=None, interval=None, min_interval=None,
                 max_interval=None, torrent_interval=None):
        cfg = pacing_config()
        self.should_stop = should_stop
        self.min_interval = float(cfg["min_interval"] if min_interval is None else min_interval)
        self.max_interval = float(cfg["max_interval"] if max_interval is None else max_interval)
        self.min_interval = min(self.min_interval, self.max_interval)
        start = float(cfg["interval"] if interval is None else interval)
        self.interval = min(max(start, self.min_interval), self.max_interval)
        self._torrent_interval = float(cfg["torrent_interval"] if torrent_interval is None else torrent_interval)
        self.next_request_at = 0.0
        self._ok_streak = 0
        self.metrics = {"requests": 0, "network_seconds": 0.0, "wait_seconds": 0.0,
                        "retries": 0, "http_429": 0, "timeouts": 0, "early_stops": 0,
                        "queries_saved": 0, "backoffs": 0, "recoveries": 0}

    def check_stop(self):
        if self.should_stop and self.should_stop():
            raise SearchStopped()

    def defer(self, seconds):
        self.next_request_at = max(self.next_request_at, time.monotonic() + float(seconds))

    def torrent_step(self):
        """.torrent 下载后使用的间隔；退避时随全局间隔一起放大。"""
        return max(0.2, min(self.interval, max(self._torrent_interval, self.interval * 0.5)))

    def before_request(self, interval=None):
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
        step = self.interval if interval is None else max(0.05, float(interval))
        self.next_request_at = time.monotonic() + step
        self.metrics["requests"] += 1

    def on_success(self):
        """连续成功达到阈值后，向 min_interval 收缩一档。"""
        self._ok_streak += 1
        if self._ok_streak >= RECOVERY_STREAK and self.interval > self.min_interval:
            self.interval = max(self.min_interval, self.interval * RECOVERY_FACTOR)
            self._ok_streak = 0
            self.metrics["recoveries"] += 1

    def on_rate_limited(self, wait_seconds=None):
        """收到 429：立即退避并抬高稳态间隔（最多到 max_interval）。"""
        self._ok_streak = 0
        if wait_seconds:
            self.defer(wait_seconds)
        if self.interval < self.max_interval:
            self.interval = min(self.max_interval, self.interval * BACKOFF_FACTOR)
            self.metrics["backoffs"] += 1

    def note_failure(self):
        """网络/解析类失败：中断连续成功计数，但不抬高稳态间隔。"""
        self._ok_streak = 0


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
                pacer.on_rate_limited(wait)
                last_err = RuntimeError("HTTP 429")
                logger.warning("sukebei 429，后续请求等待%ss，间隔升至%.1fs: %s",
                               wait, pacer.interval, query[:40])
                continue
            resp.raise_for_status()
            result = _parse_result_page(resp.text)
            pacer.on_success()
            return result
        except requests.Timeout as exc:
            _timeout_count += 1
            pacer.metrics["timeouts"] += 1
            pacer.note_failure()
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
            pacer.note_failure()
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


def _confident_enough(game, best, score, detail, threshold) -> bool:
    """是否已足够确信，可跳过后续放宽查询。

    - 满分（MAX_SCORE）无条件提前结束（与旧行为一致）；
    - 达到 EARLY_STOP_SCORE 时，必须是「精确名称」命中，且在有公司数据时公司也命中，
      避免为了速度牺牲明显更优的候选。
    """
    if not best or not extract_infohash(best.get("magnet")):
        return False
    if score >= MAX_SCORE:
        return True
    if score < threshold:
        return False
    detail = detail or {}
    if "name_exact" not in detail:
        return False
    company = (game or {}).get("company") or ""
    if company and "company" not in detail:
        return False
    return True


def search_candidates(session: requests.Session, name: str, company: str,
                      logger: logging.Logger, game=None) -> list[dict]:
    """Preserve query ordering and candidate ties; stop at the score ceiling or a confident match."""
    queries = []
    if company:
        queries.append(f"{name} {company}")
    queries.append(name)
    stripped = re.sub(r"[^\w\s]", " ", name)
    if stripped.strip() and stripped.strip() != name:
        queries.append(stripped)

    query_list = list(dict.fromkeys(queries))
    threshold = early_stop_threshold()
    merged = {}
    for index, query in enumerate(query_list):
        # Do not cache incomplete searches as no-result/low-score outcomes.
        items = _search_once(session, query, logger)
        for item in items:
            if not allows_english_candidate(name, item.get("nyaa_title")):
                continue
            merged.setdefault(item["infohash_hex"], item)
        candidates = list(merged.values())[:MAX_PER_QUERY]
        if game and candidates:
            best, score, detail = select_best(game, candidates, THRESHOLD)
            if _confident_enough(game, best, score, detail, threshold):
                pacer = getattr(session, "_egs_pacer", None)
                if pacer:
                    pacer.metrics["early_stops"] += 1
                    pacer.metrics["queries_saved"] += len(query_list) - index - 1
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
        if not allows_english_candidate(name, c.get("nyaa_title")):
            if c.get("infohash_hex"):
                conn.execute(
                    "DELETE FROM egs_nyaa_candidates WHERE egs_id=? AND infohash_hex=? AND COALESCE(selected,0)=0",
                    (egs_id, c.get("infohash_hex")),
                )
            continue
        if float(c.get("score") or 0) <= 0 and not is_abnormally_short_name(name):
            if c.get("infohash_hex"):
                conn.execute(
                    "DELETE FROM egs_nyaa_candidates WHERE egs_id=? AND infohash_hex=?",
                    (egs_id, c.get("infohash_hex")),
                )
            continue
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
    old = conn.execute(
        "SELECT COALESCE(download_failed,0) AS download_failed, link, infohash_hex,"
        " COALESCE(magnet_duplicate,0) AS magnet_duplicate"
        " FROM egs_games WHERE egs_id=?", (egs_id,),
    ).fetchone()
    old_failed = bool(old and old["download_failed"])
    old_duplicate = bool(old and old["magnet_duplicate"])
    old_infohash = str(old["infohash_hex"] or "").lower() if old else ""
    if not force and not old_failed and not old_duplicate:
        logged = conn.execute(
            "SELECT selected_infohash FROM egs_nyaa_search_log WHERE egs_id=?",
            (egs_id,),
        ).fetchone()
        if logged:
            return "skip_cache", None

    game = {"name": name, "company": company or "", "date": date,
            "release_date": release_ts}
    cands = search_candidates(session, name, company or "", logger, game=game)
    # Defensive filtering for injected/custom search providers as well as the
    # normal search_candidates path.
    cands = [c for c in cands if allows_english_candidate(name, c.get("nyaa_title"))]
    best, best_score, best_detail = select_best(game, cands, THRESHOLD)
    best_key = best.get("infohash_hex") if best else None
    _save_candidates(conn, egs_id, date, name, cands, best_key)
    # select_best also annotates every candidate with its score. Do not keep
    # zero-score noise unless the game title itself is exceptionally short.
    if not is_abnormally_short_name(name):
        cands = [c for c in cands if float(c.get("score") or 0) > 0]

    result = {
        "egs_id": egs_id,
        "date": date,
        "name": name,
        "candidates": len(cands),
        "best_score": best_score if cands else 0.0,
    }
    tried_at = time.strftime("%Y-%m-%d %H:%M:%S")
    if best:
        # 下载 .torrent 并解析 info.name/文件清单，作为后续整理的精确定位依据。
        # 失败仅降级（torrent_name 等留空），不阻断磁链选中。
        meta = None
        try:
            from .torrent_meta import fetch_torrent_meta, meta_to_json

            pacer = getattr(session, "_egs_pacer", None)
            meta = fetch_torrent_meta(session, best.get("view_url"),
                                      expected_infohash=best_key, pacer=pacer)
        except SearchStopped:
            raise
        except Exception:
            logger.debug("torrent meta fetch failed: %s", name, exc_info=True)
        meta_name = meta.get("name") if meta else None
        meta_files = meta_to_json(meta) if meta else None
        meta_size = meta.get("total_size") if meta else None
        if meta_name:
            result["torrent_name"] = meta_name

        # 失败重查：仍是最优/同一磁链 → 保持 download_failed；换到新 infohash → 清除失败
        new_magnet = bool(old_infohash and best_key and best_key.lower() != old_infohash)
        keep_failed = old_failed and not new_magnet
        download_failed_value = 1 if keep_failed else 0
        download_failed_at_value = tried_at if keep_failed else None
        if new_magnet:
            result["new_magnet"] = True
        result["download_failed"] = download_failed_value
        if keep_failed:
            # 仍无更优磁链：回滚到无磁链状态，避免继续持有已知会失败的磁链
            conn.execute(
                """
                UPDATE egs_games
                   SET link=NULL, nyaa_name=NULL, infohash_hex=NULL,
                       torrent_name=NULL, torrent_files=NULL, torrent_size=NULL,
                       download_failed=1, download_failed_at=?,
                       updated_at=?
                 WHERE egs_id=?
                """,
                (tried_at, tried_at, egs_id),
            )
        else:
            conn.execute(
                """
                UPDATE egs_games
                   SET link=?, nyaa_name=?, size=?, infohash_hex=?,
                       torrent_name=?, torrent_files=?, torrent_size=?,
                       download_failed=?, download_failed_at=?,
                       updated_at=?
                 WHERE egs_id=?
                """,
                (best.get("magnet"), best.get("nyaa_title"), best.get("size"),
                 extract_infohash(best.get("magnet")), meta_name, meta_files,
                 meta_size, download_failed_value, download_failed_at_value,
                 tried_at, egs_id),
            )
        if old_duplicate and new_magnet:
            # 共链项找到不同种子后恢复为独立记录，不能继承原种子的 115 状态。
            conn.execute(
                """UPDATE egs_games
                      SET downloaded=0,submitted_115=0,submitted_pick_code=NULL
                    WHERE egs_id=?""",
                (egs_id,),
            )
        refresh_magnet_duplicates(conn, (old_infohash, best_key))
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
            (egs_id, date, name, len(cands), best_score, best_key, tried_at, tried_at),
        )
        conn.commit()
        result["selected_title"] = best.get("nyaa_title")
        result["selected_infohash"] = best_key
        # 重建后的持久状态决定谁是共链主记录，不能依赖处理顺序。
        dup_owner = conn.execute(
            """SELECT owner.name FROM egs_games game
                 JOIN egs_games owner ON owner.egs_id=game.duplicate_of_egs_id
                WHERE game.egs_id=? AND COALESCE(game.magnet_duplicate,0)=1""",
            (egs_id,),
        ).fetchone()
        if dup_owner is not None:
            result["duplicate_of"] = dup_owner["name"]
            logger.info("DUPLICATE_TORRENT %s | 与《%s》选中同一磁链 %s",
                        name[:40], (dup_owner["name"] or "")[:40], best_key)
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
    if old_failed:
        # 仍无更优磁链：回滚到无磁链状态并保持失败标记，供下轮继续查询
        conn.execute(
            """UPDATE egs_games
                   SET link=NULL, nyaa_name=NULL, infohash_hex=NULL,
                       torrent_name=NULL, torrent_files=NULL, torrent_size=NULL,
                       download_failed=1, download_failed_at=?
                 WHERE egs_id=?""",
            (tried_at, egs_id),
        )
        conn.commit()
    result["download_failed"] = 1 if old_failed else 0
    status = "no_result" if not cands else "low_score"
    logger.info("%s %s | candidates=%s max_score=%s", status.upper(), name[:40],
                len(cands), best_score)
    return status, result


def pending_rows(conn: sqlite3.Connection, year: int, month: int | None = None,
                 force: bool = False, limit: int = 0) -> list[sqlite3.Row]:
    """取待搜索的 EGS 行，默认跳过搜索历史。"""
    sql = """
        SELECT egs_id, date, name, company, release_ts, link, infohash_hex,
               COALESCE(download_failed,0) AS download_failed
          FROM egs_games
         WHERE substr(date,1,4)=?
           AND ((link IS NULL OR link='') OR COALESCE(download_failed,0)=1
                OR COALESCE(magnet_duplicate,0)=1)
           AND (release_ts IS NULL OR release_ts <= date('now','localtime'))
    """
    params: list = [str(year)]
    if month:
        sql += " AND CAST(substr(date,6) AS INTEGER)=?"
        params.append(int(month))
    if not force:
        # 下载失败与共链项每次都重查；共链项只有找到不同 infohash 才退出队列。
        sql += """
           AND (COALESCE(download_failed,0)=1 OR COALESCE(magnet_duplicate,0)=1 OR NOT EXISTS (
               SELECT 1 FROM egs_nyaa_search_log l
                WHERE l.egs_id = egs_games.egs_id
           ))
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
        status["final_interval"] = round(pacer.interval, 3)
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
        game = conn.execute(
            "SELECT egs_id,name,infohash_hex,COALESCE(magnet_duplicate,0) AS magnet_duplicate "
            "FROM egs_games WHERE egs_id=?", (egs_id,)
        ).fetchone()
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
                # 采用后也只保留被采用的那条候选。
                conn.execute(
                    "DELETE FROM egs_nyaa_candidates WHERE egs_id=? AND id != ?",
                    (egs_id, int(candidate_id)),
                )
            else:
                # 手动磁链审核通过时，原候选都不保留。
                conn.execute("DELETE FROM egs_nyaa_candidates WHERE egs_id=?", (egs_id,))
            conn.execute(
                """
                UPDATE egs_games
                   SET link=?, nyaa_name=?, size=?, infohash_hex=?, updated_at=?
                 WHERE egs_id=?
                """,
                (magnet, nyaa_name or None, size, infohash, now_str, egs_id),
            )
            if game["magnet_duplicate"] and str(game["infohash_hex"] or "").lower() != infohash.lower():
                conn.execute(
                    "UPDATE egs_games SET downloaded=0,submitted_115=0,submitted_pick_code=NULL WHERE egs_id=?",
                    (egs_id,),
                )
            refresh_magnet_duplicates(conn, (game["infohash_hex"], infohash))
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
            conn.execute("DELETE FROM egs_nyaa_candidates WHERE egs_id=?", (egs_id,))
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
