"""EGS（ErogameScape）新情报源：爬取入库核心。

按用户确认范围：
- 只抓 PC + 18禁（erogame=true）
- 新建独立 DB（默认 egs.db），完全新爬，不复用/不迁移 getchu 数据
- 本阶段不做 EGS 磁链/下载链接获取；表结构预留下游字段
- 保留 egs_date/egs_name/egs_company 作为原始身份层，为后续“搬月”处理做准备
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .runtime import repo_root

EGS_SQL_URL = "https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/sql_for_erogamer_form.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
REQUEST_INTERVAL = 1.0  # 对 SQL 表单保持礼貌间隔

# 每列来自 SQL 查询的别名；新增列需同步改这里与 DDL
EGS_SELECT_COLUMNS = """
  g.id                                  AS egs_id,
  g.gamename                            AS egs_name,
  g.furigana                            AS name_kana,
  g.sellday                             AS egs_date,
  b.id                                  AS brand_id,
  b.brandname                           AS egs_company,
  b.kind                                AS brand_kind,
  g.model                               AS model,
  g.erogame                             AS erogame,
  g.comike                              AS getchu_id,
  g.dmm                                 AS dmm,
  g.dlsite_id                           AS dlsite_id,
  g.genre                               AS genre,
  g.shoukai                             AS official_url,
  g.tourokubi                           AS registered_at
"""


def default_egs_db_path() -> str:
    """默认独立 DB：仓库根目录/egs.db，与 getchu.db 分离。"""
    return os.path.join(repo_root(), "egs.db")


def open_egs_db(db_path: str | None = None, timeout_s: int = 30) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or default_egs_db_path(), timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_egs_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egs_games (
            egs_id          INTEGER PRIMARY KEY,
            source          TEXT    NOT NULL DEFAULT 'egs',
            model           TEXT    NOT NULL,
            -- 原始身份层（后续搬月/重标注不改）
            egs_date        TEXT    NOT NULL,             -- YYYY-MM-DD，EGS sellday
            egs_name        TEXT    NOT NULL,             -- EGS gamename
            egs_company     TEXT    NOT NULL,             -- brandlist.brandname
            -- 展示/日历层（后续可能按磁链 dn 搬月/改名）
            date            TEXT    NOT NULL,             -- YYYY-MM，初始取 egs_date 月
            name            TEXT    NOT NULL,             -- 初始=egs_name
            company         TEXT    NOT NULL,             -- 初始=egs_company
            release_ts      TEXT,                         -- YYYY-MM-DD，初始=egs_date
            name_kana       TEXT,
            brand_id        INTEGER,
            brand_kind      TEXT,
            is_eroge        INTEGER NOT NULL DEFAULT 1,
            -- 外部/对照 ID
            getchu_id       TEXT,
            dmm             TEXT,
            dlsite_id       TEXT,
            genre           TEXT,
            official_url    TEXT,
            registered_at   TEXT,
            -- 磁链/115 后续阶段复用列（本阶段仅建列不抓取）
            size            TEXT,
            link            TEXT,
            nyaa_name       TEXT,
            comment         TEXT,
            downloaded      INTEGER NOT NULL DEFAULT 0,
            infohash_hex    TEXT,
            submitted_115   INTEGER NOT NULL DEFAULT 0,
            submitted_pick_code TEXT,
            fetched_at      TEXT,
            updated_at      TEXT
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_egs_games_date_name ON egs_games(date, name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_egs_games_release_ts ON egs_games(release_ts)"
    )
    conn.commit()


def build_month_sql(year: int, month: int) -> str:
    if not (1 <= month <= 12):
        raise ValueError("month must be 1..12")
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return f"""
SELECT
{EGS_SELECT_COLUMNS}
FROM gamelist g
LEFT JOIN brandlist b ON g.brandname = b.id
WHERE g.sellday >= '{start}'
  AND g.sellday <  '{end}'
  AND g.model = 'PC'
  AND g.erogame = true
ORDER BY g.sellday, g.id
"""


def _parse_result_table(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("div", id="query_result_main")
    if main is None:
        raise RuntimeError("响应中没有 query_result_main，可能 SQL 执行失败或页面结构变化")
    table = main.find("table")
    if table is None:
        raise RuntimeError("响应中没有结果表格")
    rows = table.find_all("tr")
    if not rows:
        return []
    header = [th.get_text(" ", strip=True) for th in rows[0].find_all(["th", "td"])]
    out = []
    for tr in rows[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) != len(header):
            # 偶尔空行/异常列；尽量按 header 长度截断或跳过
            if not cells:
                continue
            if len(cells) < len(header):
                cells += [""] * (len(header) - len(cells))
            else:
                cells = cells[:len(header)]
        out.append(dict(zip(header, cells)))
    return out


def fetch_egs_month(year: int, month: int, timeout: int = 30) -> list[dict[str, str]]:
    """拉取 EGS 某月 PC+18禁 清单，返回原始字典行。"""
    sql = build_month_sql(year, month)
    resp = requests.post(EGS_SQL_URL, data={"sql": sql}, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return _parse_result_table(resp.text)


def _normalize_row(raw: dict[str, str], fetched_at: str) -> dict:
    egs_id = int(raw.get("egs_id") or "0")
    egs_date = (raw.get("egs_date") or "").strip()
    egs_name = (raw.get("egs_name") or "").strip()
    egs_company = (raw.get("egs_company") or "").strip()
    if not egs_id or not egs_date or not egs_name:
        raise ValueError(f"EGS 行缺少关键字段: {raw}")

    release_ts = egs_date
    date = egs_date[:7] if len(egs_date) >= 7 else ""
    return {
        "egs_id": egs_id,
        "source": "egs",
        "model": (raw.get("model") or "PC").strip() or "PC",
        "egs_date": egs_date,
        "egs_name": egs_name,
        "egs_company": egs_company,
        "date": date,
        "name": egs_name,
        "company": egs_company,
        "release_ts": release_ts,
        "name_kana": (raw.get("name_kana") or "").strip() or None,
        "brand_id": _int_or_none(raw.get("brand_id")),
        "brand_kind": (raw.get("brand_kind") or "").strip() or None,
        "is_eroge": 1 if (raw.get("erogame") or "").strip() == "t" else 1,
        "getchu_id": (raw.get("getchu_id") or "").strip() or None,
        "dmm": (raw.get("dmm") or "").strip() or None,
        "dlsite_id": (raw.get("dlsite_id") or "").strip() or None,
        "genre": (raw.get("genre") or "").strip() or None,
        "official_url": (raw.get("official_url") or "").strip() or None,
        "registered_at": (raw.get("registered_at") or "").strip() or None,
        "fetched_at": fetched_at,
        "updated_at": fetched_at,
    }


def _int_or_none(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def upsert_egs_rows(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """按 egs_id upsert；date/name 唯一冲突时仍以 egs_id 为准并更新该行。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cols = [
        "egs_id", "source", "model", "egs_date", "egs_name", "egs_company",
        "date", "name", "company", "release_ts", "name_kana", "brand_id",
        "brand_kind", "is_eroge", "getchu_id", "dmm", "dlsite_id", "genre",
        "official_url", "registered_at",
    ]
    # fetched_at/updated_at 只在真正发生变化时写入
    compare_cols = [c for c in cols if c != "egs_id"]
    select_cols = ", ".join(compare_cols)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in compare_cols)
    sql = f"""
        INSERT INTO egs_games ({', '.join(cols)}, fetched_at, updated_at)
        VALUES ({placeholders}, ?, ?)
        ON CONFLICT(egs_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at
    """
    inserted = 0
    updated = 0
    unchanged = 0
    cursor = conn.cursor()
    for raw in rows:
        row = _normalize_row(raw, now)
        values = [row.get(c) for c in cols] + [now, now]
        old = cursor.execute(
            f"SELECT {select_cols} FROM egs_games WHERE egs_id = ?", (row["egs_id"],)
        ).fetchone()
        if old is None:
            cursor.execute(sql, values)
            inserted += 1
        else:
            old_map = dict(zip(compare_cols, old))
            if all(old_map.get(c) == row.get(c) for c in compare_cols):
                unchanged += 1
            else:
                cursor.execute(sql, values)
                updated += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def crawl_egs_range(start_year: int, end_year: int, month: int | None = None,
                    db_path: str | None = None) -> dict:
    """逐月抓取 PC+18禁 并写入 egs_games。

    month 指定时只抓该月；否则抓 start_year..end_year 全部月份。
    """
    if end_year < start_year:
        raise ValueError("end_year must be >= start_year")
    conn = open_egs_db(db_path)
    try:
        ensure_egs_schema(conn)
        stats = {"start_year": start_year, "end_year": end_year, "month": month,
                 "months": 0, "fetched_rows": 0, "inserted": 0, "updated": 0,
                 "unchanged": 0, "errors": []}
        for year in range(start_year, end_year + 1):
            months = [month] if month else range(1, 13)
            for m in months:
                try:
                    rows = fetch_egs_month(year, m)
                    stats["months"] += 1
                    stats["fetched_rows"] += len(rows)
                    if rows:
                        res = upsert_egs_rows(conn, rows)
                        stats["inserted"] += res["inserted"]
                        stats["updated"] += res["updated"]
                        stats["unchanged"] += res["unchanged"]
                    print(f"{year:04d}-{m:02d}: {len(rows)} rows", flush=True)
                except Exception as exc:  # noqa: BLE001
                    msg = f"{year:04d}-{m:02d}: {exc}"
                    stats["errors"].append(msg)
                    print(f"ERROR {msg}", flush=True)
                time.sleep(REQUEST_INTERVAL)
        return stats
    finally:
        conn.close()



def update_egs_game_record(egs_id: int, new_date: str | None = None,
                           new_name: str | None = None,
                           new_company: str | None = None,
                           new_link: str | None = None,
                           new_nyaa_name: str | None = None,
                           new_downloaded: int | None = None,
                           new_submitted_115: int | None = None,
                           new_submitted_pick_code: str | None = None,
                           db_path: str | None = None) -> dict:
    """按 egs_id 修改展示层字段；原始 EGS 身份层保持不变。"""
    if not egs_id:
        return {"success": False, "message": "缺少 egs_id"}

    if new_date is not None:
        new_date = str(new_date).strip()
        if new_date and not re.fullmatch(r"\d{4}-\d{2}", new_date):
            return {"success": False, "message": "年月格式应为 YYYY-MM"}

    updates = {}
    if new_date:
        updates["date"] = new_date
    if new_name is not None:
        new_name = str(new_name).strip()
        if new_name:
            updates["name"] = new_name
    if new_company is not None:
        new_company = str(new_company).strip()
        if new_company:
            updates["company"] = new_company
    if new_link is not None:
        updates["link"] = str(new_link).strip()
    if new_nyaa_name is not None:
        updates["nyaa_name"] = str(new_nyaa_name).strip()
    if new_downloaded is not None:
        updates["downloaded"] = 1 if int(new_downloaded) else 0
    if new_submitted_115 is not None:
        updates["submitted_115"] = 1 if int(new_submitted_115) else 0
    if new_submitted_pick_code is not None:
        updates["submitted_pick_code"] = str(new_submitted_pick_code).strip()

    if not updates:
        return {"success": True, "message": "无变更", "egs_id": egs_id}

    conn = open_egs_db(db_path)
    try:
        row = conn.execute(
            "SELECT date, name FROM egs_games WHERE egs_id = ?", (egs_id,)
        ).fetchone()
        if row is None:
            return {"success": False, "message": "未找到记录"}

        if "date" in updates or "name" in updates:
            target_date = updates.get("date", row["date"])
            target_name = updates.get("name", row["name"])
            conflict = conn.execute(
                "SELECT 1 FROM egs_games WHERE date = ? AND name = ? AND egs_id <> ?",
                (target_date, target_name, egs_id),
            ).fetchone()
            if conflict:
                return {"success": False, "message": "目标年月/游戏名称已存在"}

        updates["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        cols = list(updates.keys())
        sets = ", ".join(f"{c} = ?" for c in cols)
        values = [updates[c] for c in cols] + [egs_id]
        conn.execute(f"UPDATE egs_games SET {sets} WHERE egs_id = ?", values)
        conn.commit()
        return {"success": True, "message": "更新成功", "egs_id": egs_id}
    finally:
        conn.close()


def delete_egs_game_record(egs_id: int, db_path: str | None = None) -> dict:
    """按 egs_id 删除展示行；候选/搜索历史可保留用于回溯。"""
    if not egs_id:
        return {"success": False, "message": "缺少 egs_id"}
    conn = open_egs_db(db_path)
    try:
        cur = conn.execute("DELETE FROM egs_games WHERE egs_id = ?", (egs_id,))
        conn.commit()
        if cur.rowcount <= 0:
            return {"success": False, "message": "未找到记录"}
        return {"success": True, "message": "删除成功", "egs_id": egs_id}
    finally:
        conn.close()