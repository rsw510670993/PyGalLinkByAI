"""外部 ID 映射表（egs_external_ids）：站内记录 ↔ 各来源编号。

背景（2026-11-26）：封面抓取原本只认 egs_games 上的三列
（getchu_id / dlsite_id / dmm），一列只放得下一个编号，ID 缺失即永久无封面。
这里改成「一条记录可挂多个来源、多个候选」的映射表：

    egs_id + source(getchu/dlsite/dmm) -> external_id

约定：
- 站内坐标就是 egs_id 列本身，不再单独存 source='egs' 的自映射行。
- confirmed = 已核实可用；candidate = 按名搜索出的待确认候选；rejected = 排除。
- 旧列（egs_games.getchu_id 等）继续作为事实来源并保持双写，
  读路径用 COALESCE(映射表, 旧列) 取首选值，切换期不会丢封面。
"""
from __future__ import annotations

import time

# 来源常量：与 egs_games 旧列一一对应
EXTERNAL_SOURCES = ("getchu", "dlsite", "dmm", "vndb")
EXTERNAL_STATUSES = ("confirmed", "candidate", "rejected")
EXTERNAL_ORIGINS = ("egs_crawl", "getchu_search", "dlsite_search", "dmm_probe",
                    "dir_scan", "vndb_search", "manual")

# (source, egs_games 旧列名)
LEGACY_COLUMNS = (
    ("getchu", "getchu_id"),
    ("dlsite", "dlsite_id"),
    ("dmm", "dmm"),
)

_SCHEMA_SQL = (
    """CREATE TABLE IF NOT EXISTS egs_external_ids (
  id            INTEGER PRIMARY KEY,
  egs_id        INTEGER NOT NULL,
  source        TEXT    NOT NULL,
  external_id   TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'confirmed',
  confidence    REAL,
  origin        TEXT    NOT NULL DEFAULT 'manual',
  note          TEXT,
  created_at    TEXT,
  updated_at    TEXT,
  UNIQUE(egs_id, source, external_id)
)""",
    "CREATE INDEX IF NOT EXISTS idx_extids_source "
    "ON egs_external_ids(source, external_id)",
    "CREATE INDEX IF NOT EXISTS idx_extids_egs ON egs_external_ids(egs_id)",
)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def table_present(conn) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='egs_external_ids'"
        ).fetchone()
    except Exception:  # noqa: BLE001 - 只读探测，任何异常都当作不存在
        return False
    return row is not None


def ensure_external_id_schema(conn) -> None:
    """建表（幂等）。旧库、测试临时库都会自动补上。"""
    for sql in _SCHEMA_SQL:
        conn.execute(sql)


def normalize_source(source) -> str | None:
    value = str(source or "").strip().lower()
    return value if value in EXTERNAL_SOURCES else None


def normalize_external_id(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def external_id_columns(alias: str = "egs_games") -> str:
    """返回三段读列表达式：映射表 confirmed 优先，旧列兜底。

    用法：把 SELECT 里原来的 `getchu_id, dlsite_id, dmm` 换成
    `f"{external_id_columns()} "`，列名与旧列保持一致，调用方无需改动。
    """
    parts = []
    for source, column in LEGACY_COLUMNS:
        parts.append(
            "COALESCE(("
            f"SELECT e.external_id FROM egs_external_ids e "
            f"WHERE e.egs_id={alias}.egs_id AND e.source='{source}' "
            "AND e.status='confirmed' "
            "ORDER BY e.confidence DESC, e.id LIMIT 1"
            f"), {alias}.{column}) AS {column}"
        )
    return ", ".join(parts)


def get_external_ids(conn, egs_id: int) -> dict:
    """取某记录的全部外部 ID，按 source 分组（含 status 与来源）。"""
    if not table_present(conn):
        return {}
    rows = conn.execute(
        "SELECT id, source, external_id, status, confidence, origin, note, "
        "created_at, updated_at FROM egs_external_ids WHERE egs_id=? "
        "ORDER BY source, status, confidence DESC, id",
        (int(egs_id),),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "source": row[1], "external_id": row[2], "status": row[3],
            "confidence": row[4], "origin": row[5], "note": row[6],
            "created_at": row[7], "updated_at": row[8],
        }
        out.setdefault(item["source"], []).append(item)
    return out


def set_external_id(conn, egs_id: int, source: str, external_id: str,
                    status: str = "confirmed", origin: str = "manual",
                    confidence: float | None = None, note: str | None = None) -> bool:
    """写一行映射（同键覆盖状态/来源/置信度）。返回是否有变化。"""
    src = normalize_source(source)
    ext = normalize_external_id(external_id)
    if src is None or ext is None or status not in EXTERNAL_STATUSES:
        return False
    if origin not in EXTERNAL_ORIGINS:
        origin = "manual"
    if not table_present(conn):
        return False
    now = _now()
    old = conn.execute(
        "SELECT status, origin, confidence FROM egs_external_ids "
        "WHERE egs_id=? AND source=? AND external_id=?",
        (int(egs_id), src, ext),
    ).fetchone()
    if old is not None:
        old_status, old_origin, old_conf = old[0], old[1], old[2]
        if old_status == status and old_origin == origin and old_conf == confidence:
            return False
        conn.execute(
            "UPDATE egs_external_ids SET status=?, origin=?, confidence=?, note=?, "
            "updated_at=? WHERE egs_id=? AND source=? AND external_id=?",
            (status, origin, confidence, note, now, int(egs_id), src, ext),
        )
        return True
    conn.execute(
        "INSERT INTO egs_external_ids "
        "(egs_id, source, external_id, status, confidence, origin, note, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (int(egs_id), src, ext, status, confidence, origin, note, now, now),
    )
    return True


def sync_row(conn, egs_id: int, getchu_id=None, dlsite_id=None, dmm=None,
             origin: str = "egs_crawl") -> int:
    """把 egs_games 三列的现值同步进映射表（confirmed）。

    只补写、不删除：候选行与已排除行都保留，避免覆盖人工判断。
    返回新增/变更的行数。
    """
    if not table_present(conn):
        return 0
    changed = 0
    values = {"getchu": getchu_id, "dlsite": dlsite_id, "dmm": dmm}
    for source, _column in LEGACY_COLUMNS:
        ext = normalize_external_id(values.get(source))
        if not ext:
            continue
        # 同源已有别的 confirmed 行时不覆盖（人工补录优先）
        exists = conn.execute(
            "SELECT 1 FROM egs_external_ids WHERE egs_id=? AND source=? "
            "AND external_id=? AND status='confirmed'",
            (int(egs_id), source, ext),
        ).fetchone()
        if exists:
            continue
        if set_external_id(conn, egs_id, source, ext, status="confirmed", origin=origin):
            changed += 1
    return changed


def backfill_from_columns(conn, limit: int = 0, dry_run: bool = False) -> dict:
    """按 egs_games 三列批量回填映射表（幂等）。dry_run 只统计不写入。"""
    if not table_present(conn):
        return {"status": "skipped", "reason": "egs_external_ids 不存在"}
    sql = ("SELECT egs_id, getchu_id, dlsite_id, dmm FROM egs_games "
           "WHERE COALESCE(getchu_id,'')!='' OR COALESCE(dlsite_id,'')!='' "
           "OR COALESCE(dmm,'')!='' ORDER BY egs_id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"scanned": len(rows), "inserted": 0, "dry_run": dry_run,
             "by_source": {s: 0 for s, _ in LEGACY_COLUMNS}}
    for row in rows:
        egs_id, gid, pid, dmm = row[0], row[1], row[2], row[3]
        values = {"getchu": gid, "dlsite": pid, "dmm": dmm}
        for source, _column in LEGACY_COLUMNS:
            ext = normalize_external_id(values.get(source))
            if not ext:
                continue
            exists = conn.execute(
                "SELECT 1 FROM egs_external_ids WHERE egs_id=? AND source=? "
                "AND external_id=? AND status='confirmed'",
                (int(egs_id), source, ext),
            ).fetchone()
            if exists:
                continue
            if not dry_run and set_external_id(
                    conn, egs_id, source, ext, status="confirmed", origin="egs_crawl"):
                stats["inserted"] += 1
                stats["by_source"][source] += 1
            elif dry_run:
                stats["inserted"] += 1
                stats["by_source"][source] += 1
    if not dry_run:
        conn.commit()
    return stats


_LEGACY_COLUMN = {source: column for source, column in LEGACY_COLUMNS}


def attach_external_id(conn, egs_id: int, source: str, external_id: str,
                       origin: str = "manual", status: str = "confirmed",
                       confidence: float | None = None,
                       write_legacy: bool = True) -> dict:
    """写映射表，并在 confirmed 时回写主表对应列（只填空，不覆盖已有值）。"""
    src = normalize_source(source)
    ext = normalize_external_id(external_id)
    if src is None or ext is None:
        return {"ok": False, "reason": "source/external_id 非法"}
    changed = set_external_id(conn, egs_id, src, ext, status=status,
                              origin=origin, confidence=confidence)
    legacy_written = False
    column = _LEGACY_COLUMN.get(src)
    # vndb 这类没有对应旧列的来源只写映射表，不回写主表
    if write_legacy and status == "confirmed" and column and table_present(conn):
        cursor = conn.execute(
            f"UPDATE egs_games SET {column}=?, updated_at=? "
            f"WHERE egs_id=? AND COALESCE({column},'')=''",
            (ext, _now(), int(egs_id)),
        )
        legacy_written = cursor.rowcount > 0
    return {"ok": True, "changed": changed, "legacy_written": legacy_written}


def resolve_egs_id(conn, source: str, external_id: str) -> int | None:
    """反查：某来源编号属于哪条站内记录（confirmed 优先，其次候选）。"""
    src = normalize_source(source)
    ext = normalize_external_id(external_id)
    if src is None or ext is None or not table_present(conn):
        return None
    row = conn.execute(
        "SELECT egs_id FROM egs_external_ids WHERE source=? AND external_id=? "
        "ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END, "
        "confidence DESC, id LIMIT 1",
        (src, ext),
    ).fetchone()
    return int(row[0]) if row else None


def audit(conn) -> dict:
    """影子对账：旧列 vs 映射表。

    四类差异：
    - missing_mapping：旧列有值但映射表没有对应 confirmed 行
    - mapping_only：映射表有 confirmed 但旧列为空（读路径可救，写路径未回写）
    - conflict：同一 (egs_id, source) 有多行 confirmed 且值不同
    - orphan：映射行的 egs_id 在 egs_games 里不存在
    """
    if not table_present(conn):
        return {"present": False}
    result = {
        "present": True,
        "rows": conn.execute("SELECT COUNT(*) FROM egs_external_ids").fetchone()[0],
        "confirmed": conn.execute(
            "SELECT COUNT(*) FROM egs_external_ids WHERE status='confirmed'"
        ).fetchone()[0],
        "candidate": conn.execute(
            "SELECT COUNT(*) FROM egs_external_ids WHERE status='candidate'"
        ).fetchone()[0],
        "missing_mapping": 0,
        "mapping_only": 0,
        "conflict": [],
        "orphan": 0,
        "by_source": {},
    }
    for source, column in LEGACY_COLUMNS:
        stats = {"legacy": 0, "mapping": 0, "missing_mapping": 0, "mapping_only": 0}
        rows = conn.execute(
            f"""SELECT g.egs_id, COALESCE(g.{column},'') AS legacy,
                       (SELECT COUNT(*) FROM egs_external_ids e
                         WHERE e.egs_id=g.egs_id AND e.source=? AND e.status='confirmed') AS mapped
                  FROM egs_games g
                 WHERE COALESCE(g.{column},'')!=''
                    OR EXISTS (SELECT 1 FROM egs_external_ids e2
                                WHERE e2.egs_id=g.egs_id AND e2.source=? AND e2.status='confirmed')""",
            (source, source),
        ).fetchall()
        for egs_id, legacy, mapped in rows:
            if legacy:
                stats["legacy"] += 1
                if not mapped:
                    stats["missing_mapping"] += 1
            else:
                stats["mapping_only"] += 1
        stats["mapping"] = conn.execute(
            "SELECT COUNT(DISTINCT egs_id) FROM egs_external_ids "
            "WHERE source=? AND status='confirmed'",
            (source,),
        ).fetchone()[0]
        result["missing_mapping"] += stats["missing_mapping"]
        result["mapping_only"] += stats["mapping_only"]
        result["by_source"][source] = stats
    # 同源多值冲突
    for row in conn.execute(
        "SELECT egs_id, source, COUNT(DISTINCT external_id) AS n FROM egs_external_ids "
        "WHERE status='confirmed' GROUP BY egs_id, source HAVING n > 1 "
        "ORDER BY n DESC LIMIT 50"
    ).fetchall():
        result["conflict"].append({"egs_id": row[0], "source": row[1], "count": row[2]})
    result["conflict_total"] = conn.execute(
        "SELECT COUNT(*) FROM (SELECT egs_id, source FROM egs_external_ids "
        "WHERE status='confirmed' GROUP BY egs_id, source "
        "HAVING COUNT(DISTINCT external_id) > 1)"
    ).fetchone()[0]
    result["orphan"] = conn.execute(
        "SELECT COUNT(*) FROM egs_external_ids e "
        "WHERE NOT EXISTS (SELECT 1 FROM egs_games g WHERE g.egs_id=e.egs_id)"
    ).fetchone()[0]
    return result
