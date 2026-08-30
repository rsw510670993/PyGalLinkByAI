"""磁链解析与重标注（Phase 3）。

dn 为唯一时间权威（延续 organize v2 哲学）：
- release_ts  ← dn [YYMMDD] → YYYY-MM-DD（发布时间，新列；release_date 保留 getchu 发售日仅展示）
- company     ← dn 公司段（date码[/getchu id]后第一个非数字括号段）
- name        ← dn 游戏名段（去尾部标记/特典/版次后缀）
- getchu_id   ← dn 7+位数字段回填（仅当为空）
原值保留：name_orig / company_orig（首次原始值，--force 重做不覆盖）
date（日历月桶）不变——getchu 上架月保持日历结构

幂等：release_ts 已设置的行跳过（--force 重做）
name 变更级联：getchu_115_folders / nyaa_candidates / dedup_cache 引用同步
              + reconcile_state 哈希刷新（保持 Phase 1 幂等，避免无谓重跑）
纯 DB 操作，无网络/AI；dry-run 默认。
"""
import json
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from .core import open_db, ensure_getchu_schema
from .dedup_service import load_delete_list, _month_rows_hash
from .runtime import read_config

# 公司段的噪音词（避免把尾部标记误判为公司）
_COMPANY_NOISE = {
    "serial", "crack", "crack updated", "update", "repack", "dl版",
    "特典", "manual", "trim", "1080p", "720p", "complete",
}
_EXTRA_EDITION_TOKENS = ["DL版", "ダウンロード版", "パッケージ版"]


def ensure_relabel_schema(conn):
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(getchu_games)")
    cols = {row[1] for row in cursor.fetchall()}
    for col, ddl in [
        ("release_ts", "TEXT"),
        ("name_orig", "TEXT"),
        ("company_orig", "TEXT"),
    ]:
        if col not in cols:
            cursor.execute(f"ALTER TABLE getchu_games ADD COLUMN {col} {ddl}")
    conn.commit()


def parse_magnet_dn(link):
    """磁链 → 解码后的 dn 原文（无 dn 返回 None）。"""
    if not link or not str(link).startswith("magnet:?"):
        return None
    try:
        qs = parse_qs(urlparse(str(link)).query)
        dn = (qs.get("dn") or [None])[0]
    except Exception:
        return None
    if not dn:
        return None
    try:
        dn = unquote(dn)
    except Exception:
        pass
    return dn.strip() or None


def _fmt_release_ts(date_code):
    """[YYMMDD] → YYYY-MM-DD；非法返回 None。"""
    if not date_code or not re.fullmatch(r"\d{6}", date_code):
        return None
    yy, mm, dd = date_code[:2], date_code[2:4], date_code[4:6]
    try:
        mi, di = int(mm), int(dd)
    except ValueError:
        return None
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return None
    return f"20{yy}-{mm}-{dd}"


def _clean_dn_name(raw, delete_list):
    """dn 游戏名段清洗：去尾部[标记]、去" + "特典段、去版次词、压缩连续重复词。"""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"(\s*\[[^\]]*\])+\s*$", "", s).strip()
    s = re.split(r"\s+\+", s)[0].strip()
    s = re.sub(r"(\s*\[[^\]]*\])+\s*$", "", s).strip()
    if not s:
        return None
    # 版次词剥离（与 Phase1 规则分组同源：delete 列表任意位置替换）+ dn特有版次词
    combined = sorted(
        set(list(delete_list) + list(_EXTRA_EDITION_TOKENS) + ["DVD Version"]),
        key=len, reverse=True,
    )
    for tok in combined:
        if tok and tok in s:
            s = s.replace(tok, " ")
    s = re.sub(r"[\s　]+", " ", s).strip()
    # girlcelly 命名习惯「名 名 + 特典」→ 压缩连续重复词（长度>=4才压，避免误伤叠词）
    parts = s.split(" ")
    if len(parts) >= 2:
        merged = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and parts[i] == parts[i + 1] and len(parts[i]) >= 4:
                merged.append(parts[i])
                i += 2
            else:
                merged.append(parts[i])
                i += 1
        s = " ".join(merged)
    return s or None


def extract_dn_parts(link, delete_list=None):
    """磁链 → {dn, date_code, release_ts, getchu_id, company, name}。

    解析失败的字段为 None。
    """
    delete_list = delete_list if delete_list is not None else load_delete_list()
    out = {"dn": None, "date_code": None, "release_ts": None,
           "getchu_id": None, "company": None, "name": None}
    dn = parse_magnet_dn(link)
    if not dn:
        return out
    out["dn"] = dn

    segs = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"\[([^\]]+)\]", dn)]
    if not segs:
        return out

    # 1. 定位首个 6 位日期码
    di = None
    for i, (s, e, content) in enumerate(segs):
        if re.fullmatch(r"\d{6}", content):
            di = i
            out["date_code"] = content
            break
    if di is None:
        return out
    out["release_ts"] = _fmt_release_ts(out["date_code"])

    # 2. 从日期码之后扫描：跳过额外日期码/数字段(取getchu id)，公司段=其后第一个
    #    后面还有自由文本的非数字括号段
    rest = dn[segs[di][1]:]
    rest_segs = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"\[([^\]]+)\]", rest)]
    prev_end = 0
    for j, (s, e, content) in enumerate(rest_segs):
        free = rest[prev_end:s].strip()
        if free:
            out["name"] = _clean_dn_name(free, delete_list)
            return out
        nxt_free = rest[e:(rest_segs[j + 1][0] if j + 1 < len(rest_segs) else len(rest))].strip()
        if re.fullmatch(r"\d{6}", content):
            prev_end = e
            continue
        if re.fullmatch(r"\d{7,}", content):
            if out["getchu_id"] is None:
                out["getchu_id"] = content
            prev_end = e
            continue
        if content.strip().lower() in _COMPANY_NOISE or not nxt_free:
            prev_end = e
            continue
        out["company"] = content.strip()
        out["name"] = _clean_dn_name(nxt_free, delete_list)
        return out
    free = rest[prev_end:].strip()
    if free:
        out["name"] = _clean_dn_name(free, delete_list)
    return out


def _cascade_name_change(conn, month_key, old_name, new_name, old_company, new_company):
    """name/company 变更后同步引用表。"""
    # getchu_115_folders（PK=date,name，冲突则合并填充后删除旧行）
    fr = conn.execute(
        "SELECT cid, pid, pick_code, folder_name, folder_path, target_name,"
        " date_code, company, status FROM getchu_115_folders WHERE date=? AND name=?",
        (month_key, old_name),
    ).fetchone()
    if fr:
        exists = conn.execute(
            "SELECT 1 FROM getchu_115_folders WHERE date=? AND name=?",
            (month_key, new_name),
        ).fetchone()
        if exists:
            conn.execute(
                """UPDATE getchu_115_folders SET
                    cid=COALESCE(cid,?), pid=COALESCE(pid,?),
                    pick_code=COALESCE(pick_code,?),
                    folder_name=COALESCE(folder_name,?),
                    folder_path=COALESCE(folder_path,?),
                    target_name=COALESCE(target_name,?),
                    date_code=COALESCE(date_code,?)
                   WHERE date=? AND name=?""",
                (*fr[:7], month_key, new_name),
            )
            conn.execute(
                "DELETE FROM getchu_115_folders WHERE date=? AND name=?",
                (month_key, old_name),
            )
        else:
            conn.execute(
                "UPDATE getchu_115_folders SET name=? WHERE date=? AND name=?",
                (new_name, month_key, old_name),
            )
    # nyaa_candidates
    conn.execute(
        "UPDATE nyaa_candidates SET name=? WHERE date=? AND name=?",
        (new_name, month_key, old_name),
    )
    # dedup_cache 指向
    conn.execute(
        "UPDATE dedup_cache SET target_name=? WHERE target_name=?",
        (new_name, old_name),
    )
    if new_company and old_company:
        conn.execute(
            "UPDATE dedup_cache SET target_company=? WHERE target_company=? AND target_name=?",
            (new_company, old_company, old_name),
        )


def relabel_month(year, month, conn=None, config=None, force=False, dry_run=True):
    """对指定月份有磁链的行做 dn 重标注。返回计划/结果（无网络无AI）。"""
    config = config or read_config()
    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_getchu_schema(conn)
    ensure_relabel_schema(conn)
    delete_list = load_delete_list(config)

    month_key = f"{year}-{month:02d}"
    plan = {
        "year": year, "month": month, "dry_run": dry_run, "force": force,
        "with_link": 0, "already": 0, "no_dn": 0, "no_dn_date": 0,
        "name_conflict": 0, "applied": 0, "changes": [], "errors": [],
    }

    try:
        rows = conn.execute(
            """
            SELECT name, company, link, release_ts, name_orig, company_orig, getchu_id
            FROM getchu_games
            WHERE date=? AND link IS NOT NULL AND link != ''
            ORDER BY name
            """,
            (month_key,),
        ).fetchall()
        plan["with_link"] = len(rows)

        for (name, company, link, release_ts, name_orig, company_orig, getchu_id) in rows:
            company = company or ""
            if release_ts and not force:
                plan["already"] += 1
                continue

            parts = extract_dn_parts(link, delete_list)
            if not parts["dn"]:
                plan["no_dn"] += 1
                continue
            if not parts["release_ts"]:
                plan["no_dn_date"] += 1
                continue

            new_company = parts["company"] if parts["company"] and parts["company"] != company else None
            new_name = parts["name"] if parts["name"] and parts["name"] != name else None
            new_gid = parts["getchu_id"] if parts["getchu_id"] and not getchu_id else None

            change = {
                "name": name,
                "release_ts": parts["release_ts"],
                "company_new": new_company,
                "name_new": new_name,
                "getchu_id_backfill": new_gid,
                "status": "ok",
            }

            if not dry_run:
                if new_name:
                    # 同月 PK 冲突守卫
                    conflict = conn.execute(
                        "SELECT 1 FROM getchu_games WHERE date=? AND name=?",
                        (month_key, new_name),
                    ).fetchone()
                    # 跨月重名守卫：目标名已被其他月份行占用 → 不改名
                    #（避免把日历行变成与另一行同名的"影子行"，错配磁链场景）
                    taken = conn.execute(
                        "SELECT 1 FROM getchu_games WHERE name=? AND NOT (date=? AND name=?)",
                        (new_name, month_key, name),
                    ).fetchone()
                    if conflict or taken:
                        plan["name_conflict"] += 1
                        change["status"] = "name_conflict"
                        new_name = None
                gid_val = new_gid
                if new_gid:
                    dup = conn.execute(
                        "SELECT 1 FROM getchu_games WHERE getchu_id=? AND NOT (date=? AND name=?)",
                        (new_gid, month_key, name),
                    ).fetchone()
                    if dup:
                        gid_val = None
                conn.execute(
                    """
                    UPDATE getchu_games
                    SET release_ts=?, name=?, company=?,
                        name_orig=COALESCE(name_orig, ?),
                        company_orig=COALESCE(company_orig, ?),
                        getchu_id=COALESCE(getchu_id, ?)
                    WHERE date=? AND name=?
                    """,
                    (parts["release_ts"], new_name or name, new_company or company,
                     name if new_name else None,
                     company if new_company else None,
                     gid_val, month_key, name),
                )
                if new_name:
                    _cascade_name_change(
                        conn, month_key, name, new_name, company, new_company or company
                    )
                change["status"] = "name_conflict" if change["status"] == "name_conflict" else "applied"

            if new_name or new_company or new_gid or not release_ts:
                plan["changes"].append(change)
            if not dry_run and change["status"] == "applied":
                plan["applied"] += 1

        if not dry_run:
            # 刷新 reconcile_state 行哈希，保持 Phase 1 幂等（不触发无谓重跑）
            conn.execute(
                """
                INSERT INTO reconcile_state (date, rows_hash, merged_count, edition_count, done_at)
                VALUES (?, ?, 0, 0, ?)
                ON CONFLICT(date) DO UPDATE SET
                    rows_hash=excluded.rows_hash, done_at=excluded.done_at
                """,
                (month_key, _month_rows_hash(conn, month_key),
                 time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        return plan
    finally:
        if own_conn:
            conn.close()


def relabel_year(year, config=None, force=False, dry_run=True):
    """全年 12 个月依序重标注。"""
    plans = []
    for month in range(1, 13):
        plans.append(relabel_month(year, month, config=config, force=force, dry_run=dry_run))
    return plans


def relabel_status(conn=None):
    """全局重标注状态统计。"""
    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_getchu_schema(conn)
        ensure_relabel_schema(conn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM getchu_games WHERE link IS NOT NULL AND link != ''")
        with_link = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM getchu_games WHERE link IS NOT NULL AND link != '' AND release_ts IS NULL"
        )
        pending = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM getchu_games WHERE link IS NOT NULL AND link != '' AND release_ts IS NOT NULL"
        )
        relabeled = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM getchu_games WHERE name_orig IS NOT NULL")
        name_relabelled = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM getchu_games WHERE company_orig IS NOT NULL")
        company_relabelled = cur.fetchone()[0]
        return {
            "with_link": with_link,
            "release_ts_set": relabeled,
            "pending": pending,
            "name_changed": name_relabelled,
            "company_changed": company_relabelled,
        }
    finally:
        if own_conn:
            conn.close()
