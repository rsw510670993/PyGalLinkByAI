"""
整理115 v2 —— 校对 + 整理（develop基线重写）

四条铁律:
1. 唯一时间权威 = 磁链dn时间戳[YYMMDD]；getchu release_date仅展示，永不参与命名
2. 磁链神圣: 对 getchu_games 的 link/nyaa_name/date 等字段只读，绝不修改
3. 存在性优先: 校对 = 确认游戏在115里确实存在、在哪、名字是否符合规范
4. 可恢复: 改名前把cid/原名写入 getchu_115_folders，磁链永远可以重新定位

定位链:
  ① getchu_115_folders.cid 精确寻址(fs_file_skim验证，零搜索)
  ② 按 dn 名称多级搜索(关键词→日期码→裸名，_names_match严格匹配)
  ③ 找不到 → downloaded/submitted 的报 missing_in_115 ⚠，未下载的报 not_downloaded
"""

import json
import os
import re
import sqlite3
import time
from urllib.parse import unquote

from .core import open_db
from .runtime import read_config, repo_root
from .p115_client import (
    parse_magnet_simple,
    search_files,
    get_item_name,
    rename_item,
    list_dir_children_names,
    parent_crumbs_path,
    _normalize_for_comparison,
    _names_match,
    _search_keyword_from_dn,
    _extra_keywords_from_dn,
    _leading_date_codes,
)

FOLDER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS getchu_115_folders (
    date TEXT NOT NULL,
    name TEXT NOT NULL,
    cid TEXT,
    pid TEXT,
    pick_code TEXT,
    folder_name TEXT,
    folder_path TEXT,
    target_name TEXT,
    date_code TEXT,
    company TEXT,
    status TEXT,
    updated_at INTEGER,
    PRIMARY KEY (date, name)
)
"""


def ensure_folder_schema(conn):
    conn.execute(FOLDER_SCHEMA_SQL)
    conn.commit()


def get_folder_record(conn, date, name):
    row = conn.execute(
        "SELECT date, name, cid, pid, pick_code, folder_name, folder_path, target_name,"
        " date_code, company, status, updated_at FROM getchu_115_folders WHERE date=? AND name=?",
        (date, name),
    ).fetchone()
    if not row:
        return None
    keys = ["date", "name", "cid", "pid", "pick_code", "folder_name", "folder_path",
            "target_name", "date_code", "company", "status", "updated_at"]
    return dict(zip(keys, row))


def save_folder_record(conn, date, name, **kw):
    rec = get_folder_record(conn, date, name) or {}
    rec.update({k: v for k, v in kw.items() if v is not None or k in kw})
    conn.execute(
        """
        INSERT OR REPLACE INTO getchu_115_folders
        (date, name, cid, pid, pick_code, folder_name, folder_path, target_name,
         date_code, company, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, name, rec.get("cid"), rec.get("pid"), rec.get("pick_code"),
         rec.get("folder_name"), rec.get("folder_path"), rec.get("target_name"),
         rec.get("date_code"), rec.get("company"), rec.get("status"), int(time.time())),
    )
    conn.commit()


def backup_db(tag="organize"):
    """批量执行前的一致性备份"""
    src = sqlite3.connect(os.path.join(str(repo_root()), "getchu.db"))
    backup_dir = os.path.join(str(repo_root()), "db_backups")
    os.makedirs(backup_dir, exist_ok=True)
    dst_path = os.path.join(backup_dir, f"getchu.db.before_{tag}.{time.strftime('%Y%m%d_%H%M%S')}")
    dst = sqlite3.connect(dst_path)
    src.backup(dst)
    dst.close()
    src.close()
    return dst_path


def compute_target_name(dn_date_code, company, name, config=None):
    """目标文件夹名: [dn日期码][公司]游戏名 —— 无空格，日期码只来自dn"""
    if not dn_date_code:
        return None
    config = config or {}
    fmt = config.get("organize_name_format", "[{date}][{company}]{name}")
    if company:
        return fmt.format(date=dn_date_code, company=company, name=name)
    return "[{date}]{name}".format(date=dn_date_code, name=name)


def extract_dn_info(link):
    """磁链 → (dn原名, dn日期码)"""
    if not link or not str(link).startswith("magnet:?"):
        return None, None
    parsed = parse_magnet_simple(link)
    dn = parsed.get("dn")
    if not dn:
        return None, None
    try:
        dn = unquote(dn)
    except Exception:
        pass
    codes = _leading_date_codes(dn) or re.findall(r"\[(\d{6})\]", dn)
    return dn, (codes[0] if codes else None)


def locate_by_search(dn, name):
    """按dn多级搜索定位文件夹（复用check_magnet_exists同款关键词策略）。

    返回 {"cid","pid","name","parent_path","is_dir","candidates"} 或 None。
    多个目录候选得分相同且都是目录时返回ambiguous=True。
    """
    client_queries = []
    kw = _search_keyword_from_dn(dn)
    if kw:
        client_queries.append(kw)
    for extra in _extra_keywords_from_dn(dn):
        if extra not in client_queries:
            client_queries.append(extra)
    codes = _leading_date_codes(dn)
    if codes:
        for c in codes:
            for q in (c, f"[{c}]"):
                if q not in client_queries:
                    client_queries.append(q)
    norm = _normalize_for_comparison(name)
    if norm and len(norm) >= 3:
        client_queries.append(name[:20])

    norm_dn = _normalize_for_comparison(dn)
    best = None  # (score, item)
    dir_hits = 0
    seen_ids = set()

    for query in client_queries:
        items = search_files(query, 0)
        for it in items:
            if not isinstance(it, dict):
                continue
            fname = it.get("n") or ""
            if not fname or it.get("cid") in seen_ids:
                continue
            norm_fname = _normalize_for_comparison(fname)
            if not (_names_match(norm_dn, norm_fname) or (norm and norm in norm_fname)):
                continue
            seen_ids.add(it.get("cid"))
            is_dir = str(it.get("fc", "")) == "0"
            score = (2 if is_dir else 0) + (1 if norm and norm in norm_fname else 0)
            if best is None or score > best[0]:
                best = (score, it)
                dir_hits = 1 if is_dir else 0
            elif score == best[0] and is_dir:
                dir_hits += 1

        if best is not None and best[0] >= 3:
            break  # 目录+包含游戏名，足够可信

    if best is None:
        return None
    it = best[1]
    pid = it.get("pid")
    return {
        "cid": it.get("cid"),
        "pid": pid,
        "name": it.get("n"),
        "parent_path": parent_crumbs_path(pid) if pid else None,
        "is_dir": str(it.get("fc", "")) == "0",
        "ambiguous": dir_hits > 1,
        "pick_code": it.get("pc"),
    }


def organize_single(date, name, dry_run=True, conn=None):
    """整理单个游戏: 磁链dn → 定位 → 按需重命名 → 记录到DB

    status:
      already_ok / would_rename / renamed / missing_in_115 / not_downloaded
      conflict / ambiguous / not_dir / no_dn_date / no_link / error
    """
    result = {
        "date": date, "name": name, "status": None,
        "dn_date": None, "old_name": None, "old_path": None,
        "target_name": None, "message": None, "cid": None, "located_by": None,
    }

    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_folder_schema(conn)

    try:
        row = conn.execute(
            "SELECT company, link, COALESCE(downloaded,0), COALESCE(submitted_115,0)"
            " FROM getchu_games WHERE date=? AND name=?",
            (date, name),
        ).fetchone()
        if not row:
            result["status"] = "error"
            result["message"] = "游戏记录不存在"
            return result
        company, link, downloaded, submitted = row

        dn, dn_date = extract_dn_info(link)
        result["dn_date"] = dn_date
        if not dn_date:
            result["status"] = "no_dn_date"
            result["message"] = "磁链dn无[YYMMDD]日期码"
            return result

        config = read_config()
        target = compute_target_name(dn_date, company, name, config)
        result["target_name"] = target

        old_name = None
        cid = None
        pid = None
        parent_path = None
        located_by = None

        # ① DB记录精确寻址
        rec = get_folder_record(conn, date, name)
        if rec and rec.get("cid"):
            cur_name = get_item_name(rec["cid"])
            if cur_name:
                cid = rec["cid"]
                old_name = cur_name
                located_by = "db_record"
                pid = rec.get("pid")
                if pid:
                    parent_path = parent_crumbs_path(pid) or rec.get("folder_path", "").rsplit("/", 1)[0]
                else:
                    parent_path = rec.get("folder_path", "").rsplit("/", 1)[0] or None
            else:
                rec = None  # cid失效（目录被删等），回退搜索

        # ② dn搜索
        if old_name is None:
            loc = locate_by_search(dn, name)
            if loc is None:
                if downloaded or submitted:
                    result["status"] = "missing_in_115"
                    result["message"] = "⚠ 标记已下载/已提交，但115中未找到文件夹"
                else:
                    result["status"] = "not_downloaded"
                    result["message"] = "115中未找到（尚未下载）"
                return result
            if loc.get("ambiguous"):
                result["status"] = "ambiguous"
                result["message"] = "命中多个候选目录，为安全起见跳过"
                return result
            if not loc.get("is_dir"):
                result["status"] = "not_dir"
                result["message"] = "命中的是文件而非目录，跳过"
                return result
            cid = loc.get("cid")
            pid = loc.get("pid")
            old_name = loc.get("name")
            parent_path = loc.get("parent_path")
            located_by = "search"

        result["cid"] = cid
        result["old_name"] = old_name
        result["old_path"] = (parent_path.rstrip("/") + "/" + old_name) if parent_path else old_name
        result["located_by"] = located_by

        # ③ 已符合规范
        if old_name == target:
            result["status"] = "already_ok"
            result["message"] = "文件夹名已符合规范"
            save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=old_name,
                               folder_path=result["old_path"], target_name=target,
                               date_code=dn_date, company=company, status="already_ok")
            return result

        # ④ 预览模式
        if dry_run:
            result["status"] = "would_rename"
            result["message"] = "预览: 将重命名"
            return result

        # ⑤ 冲突检查
        if pid:
            children = list_dir_children_names(pid)
            if children is not None and target in children:
                result["status"] = "conflict"
                result["message"] = "目标名已存在于同级目录，跳过"
                save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=old_name,
                                   folder_path=result["old_path"], target_name=target,
                                   date_code=dn_date, company=company, status="conflict")
                return result

        # ⑥ 执行重命名并校验
        rr = rename_item(cid, target)
        if not rr.get("success"):
            result["status"] = "error"
            result["message"] = "重命名失败: " + str(rr.get("message", ""))
            return result
        new_name = get_item_name(cid)
        if new_name != target:
            result["status"] = "error"
            result["message"] = f"重命名后校验失败: 实际为 {new_name!r}"
            return result

        new_path = (parent_path.rstrip("/") + "/" + target) if parent_path else target
        result["old_name"] = old_name
        result["status"] = "renamed"
        result["message"] = "已重命名"
        save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=target,
                           folder_path=new_path, target_name=target,
                           date_code=dn_date, company=company, status="renamed")
        return result
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        return result
    finally:
        if own_conn:
            conn.close()


def organize_batch(year=None, month=None, name=None, dry_run=True):
    """批量整理: 指定年/月/单游戏；非dry-run时自动备份DB"""
    if not dry_run:
        backup_path = backup_db("organize_batch")
    else:
        backup_path = None

    conn = open_db()
    ensure_folder_schema(conn)
    try:
        sql = ("SELECT date, name FROM getchu_games WHERE link IS NOT NULL AND link != ''")
        params = []
        if year:
            sql += " AND substr(date,1,4)=?"
            params.append(str(year))
        if month:
            sql += " AND CAST(substr(date,6) AS INTEGER)=?"
            params.append(int(month))
        if name:
            sql += " AND name=?"
            params.append(name)
        sql += " ORDER BY date, name"
        rows = conn.execute(sql, params).fetchall()

        results = []
        for date, gname in rows:
            r = organize_single(date, gname, dry_run=dry_run, conn=conn)
            results.append(r)
            time.sleep(0.5)

        summary = {}
        for r in results:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        return {
            "year": year, "month": month, "name": name,
            "dry_run": dry_run,
            "db_backup": backup_path,
            "total": len(results),
            "summary": summary,
            "results": results,
        }
    finally:
        conn.close()
