"""
整理115 v3 —— 存在性检查 + 位置校验 + 规范命名（Phase 4）

四条铁律:
1. 唯一时间权威 = 磁链dn时间戳（Phase 3 已按多码规则落库 release_ts）；目标命名
   [{dn_date:YYYY-MM-DD}][{company}]{name}，目标位置 /GAL/GAL-{dn年份}
2. 磁链神圣: 对 getchu_games 的 link/nyaa_name/date 等内容字段只读；
   仅允许修正流水线状态位 downloaded / submitted_115（存在性检查结论）
3. 存在性优先: 校对 = 确认游戏在115里确实存在、在哪、名字/位置是否符合规范
4. 可恢复: 改名/移动前把cid/原名/原路径写入 getchu_115_folders，磁链永远可以重新定位

定位链:
  ① getchu_115_folders.cid 精确寻址(fs_file_skim验证，零搜索)
  ② 按 dn 名称多级搜索(关键词→日期码→裸名，_names_match严格匹配)
  ③ 找不到 → 查离线任务(in_offline=提交未完成) → downloaded/submitted 的报
     missing_in_115 ⚠ 并重置 submitted_115=0 供重提，未下载的报 not_downloaded

动作(单游戏一次整理可组合):
  rename   旧名 → [YYYY-MM-DD][公司]名
  move     现目录 → /GAL/GAL-{dn年}（年份目录缺则创建，如 GAL-2010）
  set_dl   115存在但 downloaded=0 → 补记 downloaded=1
  reset_sub  115缺失但 submitted_115=1 → 重置 submitted_115=0
"""

import json
import os
import re
import sqlite3
import time
from urllib.parse import unquote

from .core import open_db
from .runtime import read_config, repo_root
from .relabel import extract_dn_parts
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


GAL_ROOT = "/GAL"


def resolve_dn_timestamp(link, release_ts=None):
    """dn 时间戳 → (dn_date 'YYYY-MM-DD', date_code 6位)。

    优先取 release_ts（Phase 3 relabel 已按多码规则选定真实发售码），
    缺失时从磁链 dn 解析兜底。
    """
    if release_ts:
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(release_ts).strip())
        if m:
            code = m.group(1)[2:] + m.group(2) + m.group(3)
            return str(release_ts).strip(), code
    if link and str(link).startswith("magnet:?"):
        parts = extract_dn_parts(str(link))
        if parts.get("release_ts"):
            yy, mm, dd = parts["release_ts"].split("-")
            return parts["release_ts"], yy[2:] + mm + dd
    return None, None


def compute_target_name(dn_date, company, name, config=None, date_code=None):
    """目标文件夹名: [{dn_date}][{company}]{name}，如 [2012-01-27][みなとそふと]真剣…S

    占位符: {dn_date}=YYYY-MM-DD（推荐）；{date}/{date_code}=旧6位码（兼容）。
    """
    if not dn_date:
        return None
    config = config or {}
    fmt = config.get("organize_name_format", "[{dn_date}][{company}]{name}")
    if company:
        return fmt.format(dn_date=dn_date, date=date_code or dn_date,
                          date_code=date_code or dn_date, company=company, name=name)
    # 无公司名：去掉公司占位段
    fmt2 = re.sub(r"\[{company}\]", "", fmt)
    return fmt2.format(dn_date=dn_date, date=date_code or dn_date,
                       date_code=date_code or dn_date, name=name)


def target_year_dir(dn_date):
    """dn 年份 → /GAL/GAL-{year}"""
    return f"{GAL_ROOT}/GAL-{dn_date[:4]}" if dn_date else None


def _load_client():
    from .p115_client import load_client

    return load_client()


def resolve_cid(path):
    from .p115_client import _resolve_path_to_cid

    return _resolve_path_to_cid(path)


def mkdir_year_dir(year):
    """确保 /GAL/GAL-{year} 存在（缺则创建）。返回 cid 或 None。"""
    from .p115_client import _import_p115client

    path = f"{GAL_ROOT}/GAL-{year}"
    cid = resolve_cid(path)
    if cid:
        return cid
    root_cid = resolve_cid(GAL_ROOT)
    if not root_cid:
        return None
    client = _load_client()
    if client is None:
        return None
    try:
        _, check_response = _import_p115client()
        resp = check_response(client.fs_mkdir({"name": f"GAL-{year}", "pid": root_cid}))
        data = resp.get("data") if isinstance(resp, dict) else {}
        file_id = (data or {}).get("file_id") or (data or {}).get("cid")
        if file_id:
            return str(file_id)
        return resolve_cid(path) or None
    except Exception:
        return None


def move_item(file_id, to_cid):
    """移动文件/目录到目标目录（web端点: fid+pid）"""
    client = _load_client()
    if client is None:
        return {"success": False, "message": "未登录"}
    try:
        from .p115_client import _import_p115client

        _, check_response = _import_p115client()
        resp = check_response(client.fs_move({"fid": str(file_id), "pid": str(to_cid)}))
        return {"success": True, "response": resp}
    except Exception as e:
        return {"success": False, "message": str(e)}


def list_dir_children(cid, max_pages=30):
    """列出目录子项完整信息 [{n, cid, pid, fc, pc}]（fs_files直读，不走搜索索引）。失败返回None"""
    client = _load_client()
    if client is None:
        return None
    try:
        from .p115_client import _import_p115client

        _, check_response = _import_p115client()
        items, offset = [], 0
        for _ in range(max_pages):
            resp = check_response(
                client.fs_files({"cid": str(cid), "limit": 200, "offset": offset, "show_dir": 1})
            )
            data = resp.get("data") or []
            for it in data:
                if isinstance(it, dict) and it.get("n"):
                    items.append({"n": it["n"], "cid": it.get("cid"), "pid": it.get("pid"),
                                  "fc": it.get("fc"), "pc": it.get("pc")})
            if len(data) < 200:
                break
            offset += len(data)
        return items
    except Exception:
        return None


def locate_in_year_dir(year_dir_cid, dn, name):
    """在指定年份目录内直读定位（绕过115搜索索引滞后/搜索限流）。

    返回 {"cid","pid","name","parent_path","is_dir","pick_code"} 或 None。
    """
    if not year_dir_cid:
        return None
    items = list_dir_children(year_dir_cid)
    if not items:
        return None
    norm_dn = _normalize_for_comparison(dn)
    norm = _normalize_for_comparison(name)
    best = None
    for it in items:
        fname = it.get("n") or ""
        norm_fname = _normalize_for_comparison(fname)
        if not (_names_match(norm_dn, norm_fname) or (norm and len(norm) >= 3 and norm in norm_fname)):
            continue
        is_dir = str(it.get("fc", "")) == "0"
        score = (2 if is_dir else 0) + (1 if norm and norm in norm_fname else 0)
        if best is None or score > best[0]:
            best = (score, it)
    if best is None:
        return None
    it = best[1]
    return {"cid": str(it.get("cid")), "pid": it.get("pid"), "name": it.get("n"),
            "parent_path": parent_crumbs_path(it.get("pid")),
            "is_dir": str(it.get("fc", "")) == "0", "pick_code": it.get("pc")}


def magnet_in_offline_tasks(magnet, infohash_hex=None):
    """磁链是否在115离线下载任务中（已提交未完成/排队）"""
    from .p115_client import offline_list, parse_magnet_simple

    if not infohash_hex:
        try:
            infohash_hex = parse_magnet_simple(magnet).get("infohash_hex")
        except Exception:
            infohash_hex = None
    if not infohash_hex:
        return False
    ol = offline_list()
    if not ol.get("success"):
        return False
    tasks = ol.get("tasks") or []
    for task in tasks:
        if isinstance(task, dict):
            url = (task.get("url") or "").lower()
            if infohash_hex and infohash_hex.lower() in url:
                return True
    return False


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


def organize_single(date, name, dry_run=True, conn=None, year_dirs=None):
    """整理单个游戏: 存在性检查 → 规范命名([YYYY-MM-DD][公司]名) → 位置校验(/GAL/GAL-{dn年})

    status:
      already_ok              名称与位置均合规（含本轮已补 set_dl/reset_sub）
      renamed / moved / renamed_moved    实际执行的重命名/移动
      would_rename / would_move / would_rename_moved / would_set_downloaded  预览
      found_set_downloaded    115存在但DB未标记 → 已补记 downloaded=1
      in_offline              磁链在115离线任务中（提交未完成，等待）
      missing_in_115          ⚠ 标记已下载/已提交但115找不到（execute时重置 submitted_115=0）
      not_downloaded          未下载（无磁链提交记录，正常）
      conflict / ambiguous / shared_cid / not_dir / no_dn_date / no_link / error
    """
    result = {
        "date": date, "name": name, "status": None,
        "dn_date": None, "old_name": None, "old_path": None,
        "target_name": None, "target_path": None,
        "actions": [], "message": None, "cid": None, "located_by": None,
    }

    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_folder_schema(conn)
    year_dirs = year_dirs if year_dirs is not None else {}

    try:
        row = conn.execute(
            "SELECT company, link, COALESCE(downloaded,0), COALESCE(submitted_115,0), release_ts"
            " FROM getchu_games WHERE date=? AND name=?",
            (date, name),
        ).fetchone()
        if not row:
            result["status"] = "error"
            result["message"] = "游戏记录不存在"
            return result
        company, link, downloaded, submitted, release_ts = row

        if not link or not str(link).startswith("magnet:?"):
            result["status"] = "no_link"
            result["message"] = "无磁链"
            return result

        dn_date, date_code = resolve_dn_timestamp(link, release_ts)
        result["dn_date"] = dn_date
        if not dn_date:
            result["status"] = "no_dn_date"
            result["message"] = "磁链无有效日期码且无release_ts"
            return result

        config = read_config()
        target = compute_target_name(dn_date, company, name, config, date_code=date_code)
        result["target_name"] = target
        year_dir_path = target_year_dir(dn_date)
        result["target_path"] = f"{year_dir_path}/{target}"

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
                    parent_path = parent_crumbs_path(pid)
                if not parent_path:
                    parent_path = (rec.get("folder_path") or "").rsplit("/", 1)[0] or None
            else:
                rec = None  # cid失效（目录被删等），回退搜索

        # ② dn搜索（全局搜索 + 年份目录直读兜底，防115搜索索引滞后）
        if old_name is None:
            loc = locate_by_search(dn_of(link), name)
            if loc is None:
                # 兜底: 直读 dn 年份目录（搜索索引未收录新目录时仍可定位）
                ydir = resolve_cid(year_dir_path)
                loc = locate_in_year_dir(ydir, dn_of(link), name) if ydir else None
                if loc is not None and loc.get("ambiguous"):
                    loc = None
            if loc is None:
                # ③ 存在性检查：离线任务 → 缺失 → 未下载
                if magnet_in_offline_tasks(link):
                    result["status"] = "in_offline"
                    result["message"] = "磁链在115离线任务中，等待下载完成"
                    return result
                if downloaded or submitted:
                    result["status"] = "missing_in_115"
                    result["message"] = "⚠ 标记已下载/已提交，但115中未找到文件夹"
                    if not dry_run and submitted:
                        conn.execute(
                            "UPDATE getchu_games SET submitted_115=0 WHERE date=? AND name=?",
                            (date, name),
                        )
                        conn.commit()
                        result["actions"].append("reset_sub")
                        result["message"] += "；已重置 submitted_115=0 供重提"
                    return result
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

        # ③.5 共享cid守卫：同一115目录被多行引用 → 只处理目标名与当前目录名一致的一行，
        #     其余跳过待人工审阅（防止重复行/共用磁链行之间改名互踢）
        if located_by == "db_record":
            others = conn.execute(
                "SELECT date, name, target_name FROM getchu_115_folders"
                " WHERE cid=? AND NOT (date=? AND name=?)",
                (cid, date, name),
            ).fetchall()
            if others:
                my_matches_cur = (old_name == target)
                other_matches = any(o[2] == old_name for o in others)
                if my_matches_cur and not other_matches:
                    pass  # 本行持有当前目录名，其他行是陈旧引用 → 本行处理
                else:
                    result["status"] = "shared_cid"
                    result["message"] = ("115目录被多行引用(" + "; ".join(
                        f"{d}/{n[:20]}" for d, n, _t in others) + ")，跳过待人工审阅")
                    return result

        # ④ 动作判定: 改名 + 移动
        need_rename = old_name != target
        cur_parent_norm = (parent_path or "").rstrip("/")
        need_move = cur_parent_norm != year_dir_path

        if not need_rename and not need_move:
            # 已合规 —— 仅需补记流水线状态
            if downloaded == 0:
                if dry_run:
                    result["status"] = "would_set_downloaded"
                    result["message"] = "115中已存在但DB未标记，将补记 downloaded=1"
                else:
                    set_downloaded(conn, date, name, cid)
                    result["actions"].append("set_dl")
                    result["status"] = "found_set_downloaded"
                    result["message"] = "115中已存在，已补记 downloaded=1"
            else:
                if submitted == 0:
                    conn.execute(
                        "UPDATE getchu_games SET submitted_115=1 WHERE date=? AND name=?",
                        (date, name),
                    )
                    conn.commit()
                    result["actions"].append("restore_sub")
                    result["status"] = "found_set_downloaded"
                    result["message"] = "文件夹名与位置均合规；已恢复 submitted_115=1"
                else:
                    result["status"] = "already_ok"
                    result["message"] = "文件夹名与位置均已符合规范"
            _save_if_changed(conn, date, name, cid=cid, pid=pid, folder_name=old_name,
                             folder_path=result["old_path"], target_name=target,
                             date_code=date_code, company=company, status=result["status"])
            return result

        # 目标年份目录（dry-run 不创建，仅检测）
        if need_move:
            if year_dir_path in year_dirs:
                target_pid = year_dirs[year_dir_path]
            else:
                target_pid = resolve_cid(year_dir_path)
                if target_pid:
                    year_dirs[year_dir_path] = target_pid
            if not target_pid:
                if dry_run:
                    result["actions"].append("mkdir_year")
                    result["message"] = f"预览: 年份目录 {year_dir_path} 不存在，执行时将创建"
                    target_pid = None
                else:
                    target_pid = mkdir_year_dir(dn_date[:4])
                    if target_pid:
                        year_dirs[year_dir_path] = target_pid
                    else:
                        result["status"] = "error"
                        result["message"] = f"创建年份目录失败: {year_dir_path}"
                        return result
        else:
            target_pid = pid

        # 冲突检查（目标名与目标目录下的现有项）
        if need_rename and target_pid:
            children = list_dir_children_names(target_pid)
            if children is not None and target in children:
                result["status"] = "conflict"
                result["message"] = f"目标目录已存在同名项: {target}"
                save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=old_name,
                                   folder_path=result["old_path"], target_name=target,
                                   date_code=date_code, company=company, status="conflict")
                return result

        if dry_run:
            result["status"] = ("would_rename_moved" if (need_rename and need_move)
                                else "would_rename" if need_rename else "would_move")
            result["message"] = "预览: " + "、".join(
                (["重命名"] if need_rename else []) + (["移动到 " + year_dir_path] if need_move else []))
            return result

        # ⑤ 执行重命名
        if need_rename:
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
            result["actions"].append("rename")

        # ⑥ 执行移动
        if need_move and target_pid and str(target_pid) != str(pid):
            mr = move_item(cid, target_pid)
            if not mr.get("success"):
                result["status"] = "error"
                result["message"] = "移动失败: " + str(mr.get("message", ""))
                return result
            time.sleep(0.5)
            new_parent = parent_crumbs_path(target_pid)
            if new_parent is not None and new_parent.rstrip("/") != year_dir_path:
                result["status"] = "error"
                result["message"] = f"移动后校验失败: 实际位置 {new_parent!r}"
                return result
            pid = target_pid
            parent_path = year_dir_path
            result["actions"].append("move")

        result["status"] = ("renamed_moved" if (need_rename and need_move)
                            else "renamed" if need_rename else "moved")
        result["message"] = "已" + ("重命名" if need_rename else "") + ("并移动" if (need_rename and need_move) else "移动" if need_move else "")
        new_path = f"{year_dir_path}/{target}"
        save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=target,
                           folder_path=new_path, target_name=target,
                           date_code=date_code, company=company, status=result["status"])

        # ⑦ 存在即补记流水线状态（downloaded=0 → 补记；曾被误重置 submitted → 恢复）
        if downloaded == 0:
            set_downloaded(conn, date, name, cid)
            result["actions"].append("set_dl")
        elif submitted == 0:
            conn.execute(
                "UPDATE getchu_games SET submitted_115=1 WHERE date=? AND name=?",
                (date, name),
            )
            conn.commit()
            result["actions"].append("restore_sub")
        return result
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        return result
    finally:
        if own_conn:
            conn.close()


def dn_of(link):
    """磁链 → dn 原名（搜索用）"""
    from urllib.parse import unquote as _uq

    parsed = parse_magnet_simple(link)
    dn = parsed.get("dn")
    if dn:
        try:
            dn = _uq(dn)
        except Exception:
            pass
    return dn


def set_downloaded(conn, date, name, cid):
    """115确认存在 → 补记 downloaded=1（连同 submitted_115 语义一致）"""
    conn.execute(
        "UPDATE getchu_games SET downloaded=1, submitted_115=1 WHERE date=? AND name=?",
        (date, name),
    )
    conn.commit()


def _save_if_changed(conn, date, name, **kw):
    """记录无变化时不写库（幂等：重复执行零写入）"""
    rec = get_folder_record(conn, date, name)
    if rec:
        for k, v in kw.items():
            old_v = rec.get(k)
            if old_v is None and k == "pid" and v:
                continue
            if str(old_v or "") != str(v or ""):
                save_folder_record(conn, date, name, **kw)
                return
    else:
        save_folder_record(conn, date, name, **kw)

def organize_batch(year=None, month=None, name=None, dry_run=True, limit=None):
    """批量整理: 按**展示年月**(dn权威，Phase3已重标注)筛选；非dry-run时自动备份DB"""
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
        if limit:
            rows = rows[: int(limit)]

        results = []
        year_dirs = {}  # /GAL/GAL-{year} -> cid 缓存
        for date, gname in rows:
            r = organize_single(date, gname, dry_run=dry_run, conn=conn, year_dirs=year_dirs)
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
