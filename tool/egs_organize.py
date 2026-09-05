"""EGS 115 整理：按磁链 dn 时间戳命名并归入年份目录；EGS 原始日期保留为身份层。"""

import json
import os
import re
import time
from urllib.parse import unquote

from .egs_core import open_egs_db as open_db
from .runtime import read_config, repo_root
from .p115_client import (
    get_item_info,
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
CREATE TABLE IF NOT EXISTS egs_115_folders (
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
        " date_code, company, status, updated_at FROM egs_115_folders WHERE date=? AND name=?",
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
        INSERT OR REPLACE INTO egs_115_folders
        (date, name, cid, pid, pick_code, folder_name, folder_path, target_name,
         date_code, company, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, name, rec.get("cid"), rec.get("pid"), rec.get("pick_code"),
         rec.get("folder_name"), rec.get("folder_path"), rec.get("target_name"),
         rec.get("date_code"), rec.get("company"), rec.get("status"), int(time.time())),
    )
    conn.commit()


GAL_ROOT = "/GAL"


def resolve_dn_timestamp(link, release_ts=None):
    """优先使用磁链 dn 时间戳；缺失/无效时回退 EGS release_ts。"""
    from datetime import datetime
    dn = parse_magnet_simple(link or "").get("dn") or ""
    dates = set()
    for code in re.findall(r"\[(\d{6})\]", dn):
        try:
            dates.add(datetime.strptime(code, "%y%m%d"))
        except ValueError:
            pass
    if len(dates) == 1:
        value = dates.pop()
        return value.strftime("%Y-%m-%d"), value.strftime("%y%m%d")
    if release_ts:
        try:
            value = datetime.strptime(str(release_ts), "%Y-%m-%d")
            return value.strftime("%Y-%m-%d"), value.strftime("%y%m%d")
        except ValueError:
            pass
    return None, None


def compute_target_name(dn_date, company, name, config=None, date_code=None):
    """目标文件夹名: [{compact_date}][{company}]{name}，如 [20260116][みなとそふと]真剣…S

    占位符: {compact_date}=YYYYMMDD（推荐）；{dn_date}=YYYY-MM-DD；{date}/{date_code}=旧6位码（兼容）。
    """
    if not dn_date:
        return None
    config = config or {}
    compact_date = str(dn_date).replace('-', '')
    fmt = config.get("organize_name_format", "[{compact_date}][{company}]{name}")
    if company:
        return fmt.format(dn_date=dn_date, compact_date=compact_date,
                          date=date_code or compact_date,
                          date_code=date_code or compact_date,
                          company=company, name=name)
    # 无公司名：去掉公司占位段
    fmt2 = re.sub(r"\[{company}\]", "", fmt)
    return fmt2.format(dn_date=dn_date, compact_date=compact_date,
                       date=date_code or compact_date,
                       date_code=date_code or compact_date,
                       name=name)


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
        resp = check_response(client.fs_mkdir({"cname": f"GAL-{year}", "pid": root_cid}))
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


def _find_offline_task(magnet, infohash_hex=None):
    """按 infohash 查找 115 离线任务；找不到返回 None。"""
    from .p115_client import offline_list, parse_magnet_simple

    if not infohash_hex:
        try:
            infohash_hex = parse_magnet_simple(magnet or "").get("infohash_hex")
        except Exception:
            infohash_hex = None
    infohash_hex = str(infohash_hex or "").lower()
    if not infohash_hex:
        return None
    ol = offline_list()
    if not ol.get("success"):
        return None
    for task in (ol.get("tasks") or []):
        if not isinstance(task, dict):
            continue
        task_hash = str(task.get("info_hash") or task.get("infohash") or "").lower()
        task_url = str(task.get("url") or "").lower()
        if infohash_hex and (infohash_hex == task_hash or infohash_hex in task_url):
            return task
    return None


def _offline_task_finished(task):
    """115任务 status=2/finished/percent=100 视为下载完成。"""
    if not isinstance(task, dict):
        return False
    if str(task.get("display_status") or "").strip().lower() == "finished":
        return True
    try:
        if float(task.get("percentDone") or task.get("percent_done") or 0) >= 100:
            return True
    except (TypeError, ValueError):
        pass
    try:
        text = str(task.get("display_percent") or "").replace("%", "").strip()
        if text and float(text) >= 100:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if int(task.get("status")) == 2:
            return True
    except (TypeError, ValueError):
        pass
    return False


def magnet_in_offline_tasks(magnet, infohash_hex=None):
    """磁链是否仍在115离线下载任务中；已完成任务不算。"""
    task = _find_offline_task(magnet, infohash_hex)
    return bool(task and not _offline_task_finished(task))


def _item_is_single_file(cid, name, fc=None):
    """判断115条目是否是文件。

    115的 fc 在目录/文件上都可能为0，不能单独依赖；这里用扩展名 + 目录列表校验。
    """
    name = name or ""
    ext = os.path.splitext(name)[1].lower()
    if ext in {".zip", ".rar", ".7z", ".001", ".iso", ".exe", ".mp4", ".mkv"}:
        children = list_dir_children(cid)
        if children:
            # 文件会把自己作为一个子项返回；目录则返回真实内容。
            return len(children) == 1 and str(children[0].get("cid")) == str(cid)
        return True
    children = list_dir_children(cid)
    if children:
        return (len(children) == 1 and str(children[0].get("cid")) == str(cid)
                and str(children[0].get("fc")) == "1")
    return str(fc or "") not in ("0",)


def locate_offline_task_product(magnet, infohash_hex=None):
    """定位已完成的115离线任务产物。

    返回：
      None                         没有任务
      {"offline_pending": True}    任务未完成
      {"missing_product": True}    任务完成但产物不可访问
      标准 locate 产物             任务完成且可定位
    """
    task = _find_offline_task(magnet, infohash_hex)
    if not task:
        return None
    if not _offline_task_finished(task):
        return {"offline_pending": True, "offline_task": task}
    file_id = str(task.get("file_id") or task.get("delete_file_id") or "")
    if not file_id:
        return {"missing_product": True, "offline_task": task}
    info = get_item_info(file_id)
    if not info:
        return {"missing_product": True, "offline_task": task}
    cid = str(info.get("cid") or file_id)
    name = info.get("n") or task.get("name") or ""
    pid = info.get("pid") or task.get("wp_path_id")
    return {
        "cid": cid,
        "pid": pid,
        "name": name,
        "parent_path": parent_crumbs_path(pid) if pid else None,
        "is_dir": not _item_is_single_file(cid, name, info.get("fc")),
        "pick_code": info.get("pc") or task.get("pick_code"),
        "located_by": "offline_task",
        "offline_task": task,
    }


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
            elif score == best[0]:
                if is_dir:
                    # 同分目录优先（目录才是整理目标；文件可能只是目录内的种子内容）
                    best = (score, it)
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


def _wrap_file_torrent(conn, loc, dn, target, year_dir_path, dry_run, year_dirs,
                       date_code, company, date, name, result):
    """单文件种子处理：在 /GAL/GAL-{年} 下创建规范名文件夹，把匹配文件移入。

    匹配范围 = 文件所在同级目录中所有 _names_match(dn) 的文件（兼容分包rar）。
    """
    result["old_name"] = loc.get("name")
    result["old_path"] = ((loc.get("parent_path") or "").rstrip("/") + "/" + loc["name"])
    result["cid"] = loc.get("cid")
    result["located_by"] = "search_file"
    if dry_run:
        result["status"] = "would_wrap_file"
        result["target_path"] = f"{year_dir_path}/{target}"
        result["message"] = "预览: 单文件种子 → 创建文件夹并移入 " + year_dir_path
        return result

    from .p115_client import _normalize_for_comparison, _names_match

    # 1) 确保年份目录
    if year_dir_path in year_dirs:
        year_cid = year_dirs[year_dir_path]
    else:
        year_cid = resolve_cid(year_dir_path)
        if not year_cid:
            year_cid = mkdir_year_dir(year_dir_path.rsplit("-", 1)[-1])
        if not year_cid:
            result["status"] = "error"
            result["message"] = f"创建年份目录失败: {year_dir_path}"
            return result
        year_dirs[year_dir_path] = year_cid

    # 2) 同级目录中收集所有匹配 dn 的文件
    parent_pid = loc.get("pid")
    siblings = list_dir_children(parent_pid) if parent_pid else None
    matched = []
    norm_dn = _normalize_for_comparison(dn)
    for it in (siblings or []):
        if str(it.get("fc", "1")) == "0":
            continue  # 只要文件
        if _names_match(norm_dn, _normalize_for_comparison(it.get("n") or "")):
            matched.append(it)
    if not matched:
        matched = [{"cid": loc["cid"], "n": loc["name"], "pid": parent_pid}]

    # 3) 创建规范文件夹（冲突检查）
    children = list_dir_children_names(year_cid)
    if children is None:
        result["status"] = "error"
        result["message"] = "无法读取目标目录，未执行整理"
        return result
    if target in children:
        result["status"] = "conflict"
        result["message"] = f"目标目录已存在同名文件夹: {target}"
        return result
    from .p115_client import _import_p115client

    client = _load_client()
    try:
        _, check_response = _import_p115client()
        resp = check_response(client.fs_mkdir({"cname": target, "pid": year_cid}))
        data = resp.get("data") if isinstance(resp, dict) else {}
        new_cid = (data or {}).get("file_id") or (data or {}).get("cid")
        if not new_cid:
            new_cid = resolve_cid(f"{year_dir_path}/{target}")
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"创建文件夹失败: {e}"
        return result
    if not new_cid:
        result["status"] = "error"
        result["message"] = "创建文件夹后无法取到cid"
        return result

    result["source_files"] = matched
    record_operation(conn, date, name, result)

    # 4) 逐个移入
    moved = 0
    for it in matched:
        fid = str(it.get("cid") or "")
        if not fid:
            continue
        mr = move_item(fid, new_cid)
        if not mr.get("success"):
            result["status"] = "error"
            result["message"] = f"移入失败({it.get('n', '')[:30]}): {mr.get('message')}"
            return result
        moved += 1
        time.sleep(0.3)
    time.sleep(0.5)
    if get_item_name(new_cid) != target:
        result["status"] = "error"
        result["message"] = "文件夹创建后校验失败"
        return result

    result["cid"] = str(new_cid)
    result["status"] = "wrapped_file"
    result["actions"] = ["wrap_file"]
    result["target_path"] = f"{year_dir_path}/{target}"
    result["message"] = f"已创建文件夹并移入{moved}个文件"
    save_folder_record(conn, date, name, cid=str(new_cid), pid=str(year_cid),
                       folder_name=target, folder_path=result["target_path"],
                       target_name=target, date_code=date_code, company=company,
                       status="wrapped_file")
    # ⑦ 补记 downloaded
    row = conn.execute(
        "SELECT COALESCE(downloaded,0) FROM egs_games WHERE date=? AND name=?",
        (date, name),
    ).fetchone()
    if row and row[0] == 0:
        set_downloaded(conn, date, name, str(new_cid))
        result["actions"].append("set_dl")
    return result


def _source_year(parent_path=None, old_name=None):
    """从旧目录路径/名称中提取来源年份，用于跨年移动保护。"""
    text = f"{parent_path or ''}/{old_name or ''}"
    m = re.search(r'/GAL-(\d{4})(?:/|$)', f"{parent_path or ''}/")
    if m:
        return int(m.group(1))
    m = re.search(r'\[(\d{4})-\d{1,2}-\d{1,2}\]', old_name or '')
    if m:
        return int(m.group(1))
    m = re.search(r'\[(\d{2})(\d{2})\d{2}\]', old_name or '')
    if m:
        return 2000 + int(m.group(1))
    return None


def _apply_month_shift(conn, date, name, dn_date):
    """用户批准搬月后，写入实际发售日并切换 EGS 展示月份。"""
    target_month = str(dn_date)[:7]
    conflict = conn.execute(
        "SELECT 1 FROM egs_games WHERE date=? AND name=? AND NOT (date=? AND name=?)",
        (target_month, name, date, name),
    ).fetchone()
    if conflict:
        return None, {"status": "conflict", "message": f"实际发售月已存在同名记录: {target_month}/{name}"}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        UPDATE egs_games
           SET date=?, release_ts=?, actual_release_ts=?, updated_at=?
         WHERE date=? AND name=?
        """,
        (target_month, dn_date, dn_date, now, date, name),
    )
    conn.commit()
    return target_month, None


def organize_single(date, name, dry_run=True, conn=None, year_dirs=None,
                    confirmed_cross_year=False, confirmed_month_shift=False):
    """整理单个游戏: 磁链dn日期规范命名([YYYYMMDD][公司]名) → 位置校验(/GAL/GAL-{dn年})

    status:
      already_ok              名称与位置均合规（含本轮已补 set_dl/reset_sub）
      renamed / moved / renamed_moved    实际执行的重命名/移动
      would_rename / would_move / would_rename_moved / would_set_downloaded  预览
      cross_year_confirm      来源目录年份与目标年份不同，需人工确认后才能移动
      month_shift_confirm     磁链日期与EGS展示月份不同，需人工批准搬月
      found_set_downloaded    115存在但DB未标记 → 已补记 downloaded=1
      in_offline              磁链在115离线任务中（提交未完成，等待）
      missing_in_115          ⚠ 标记已下载/已提交但115找不到（execute时重置 submitted_115=0）
      not_downloaded          未下载（无磁链提交记录，正常）
      wrapped_file / would_wrap_file（单文件种子包文件夹）
      conflict / ambiguous / shared_cid / no_dn_date / no_link / error
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
            "SELECT company, link, COALESCE(downloaded,0), COALESCE(submitted_115,0), release_ts,"
            " egs_date, actual_release_ts"
            " FROM egs_games WHERE date=? AND name=?",
            (date, name),
        ).fetchone()
        if not row:
            result["status"] = "error"
            result["message"] = "游戏记录不存在"
            return result
        company, link, downloaded, submitted, release_ts, egs_date, actual_release_ts = row
        result["egs_date"] = egs_date
        result["release_ts"] = release_ts
        result["actual_release_ts"] = actual_release_ts

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
                # ③ 存在性检查：离线任务（已完成产物可继续整理）→ 缺失 → 未下载
                off = locate_offline_task_product(link)
                if off and off.get("offline_pending"):
                    result["status"] = "in_offline"
                    result["message"] = "磁链在115离线任务中，等待下载完成"
                    return result
                if off and off.get("missing_product"):
                    if downloaded or submitted:
                        result["status"] = "missing_in_115"
                        result["message"] = "⚠ 标记已下载/已提交，但115中未找到文件夹"
                        if not dry_run and submitted:
                            conn.execute(
                                "UPDATE egs_games SET submitted_115=0 WHERE date=? AND name=?",
                                (date, name),
                            )
                            conn.commit()
                            result["actions"].append("reset_sub")
                            result["message"] += "；已重置 submitted_115=0 供重提"
                        return result
                    result["status"] = "not_downloaded"
                    result["message"] = "115中未找到（尚未下载）"
                    return result
                if off and off.get("cid") and off.get("name"):
                    loc = off
                elif magnet_in_offline_tasks(link):
                    result["status"] = "in_offline"
                    result["message"] = "磁链在115离线任务中，等待下载完成"
                    return result
                if loc is None:
                    if downloaded or submitted:
                        result["status"] = "missing_in_115"
                        result["message"] = "⚠ 标记已下载/已提交，但115中未找到文件夹"
                        if not dry_run and submitted:
                            conn.execute(
                                "UPDATE egs_games SET submitted_115=0 WHERE date=? AND name=?",
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
                # 命中的是文件：先取真实父目录信息（搜索结果无pid，用fs_file查）
                info = get_item_info(loc["cid"]) or {}
                if info and not _item_is_single_file(str(info.get("cid") or loc["cid"]),
                                                     info.get("n") or loc.get("name"),
                                                     info.get("fc")):
                    # 实际是目录（搜索索引把目录内文件当命中）→ 按目录处理
                    loc = {"cid": info["cid"], "pid": info.get("pid"), "name": info.get("n"),
                           "parent_path": parent_crumbs_path(info.get("pid")) if info.get("pid") else None,
                           "is_dir": True, "pick_code": info.get("pc")}
                else:
                    # 单文件种子 → 建规范文件夹包进去（用户指定策略）
                    if info.get("pid"):
                        loc["pid"] = info["pid"]
                        loc["parent_path"] = parent_crumbs_path(info["pid"])
                    wrap = _wrap_file_torrent(conn, loc, dn_of(link), target, year_dir_path,
                                              dry_run, year_dirs, date_code, company,
                                              date, name, result)
                    return wrap
            cid = loc.get("cid")
            pid = loc.get("pid")
            old_name = loc.get("name")
            parent_path = loc.get("parent_path")
            located_by = "search"

        result["cid"] = cid
        result["old_name"] = old_name
        result["old_path"] = (parent_path.rstrip("/") + "/" + old_name) if parent_path else old_name
        result["located_by"] = located_by

        # ③.5 共享cid守卫（无论定位方式）：同一115目录被多行引用 → 只处理"记录中目标名
        #     与当前目录名一致"的那一行，其余跳过待人工审阅（防重复行/共用磁链行改名互踢）
        others = conn.execute(
            "SELECT date, name, target_name FROM egs_115_folders"
            " WHERE cid=? AND NOT (date=? AND name=?)",
            (cid, date, name),
        ).fetchall()
        if others:
            my_record_holds = (located_by == "db_record" and old_name == target
                               and not any(o[2] == old_name for o in others))
            if not my_record_holds:
                result["status"] = "shared_cid"
                result["message"] = ("115目录被多行引用(" + "; ".join(
                    f"{d}/{n[:20]}" for d, n, _t in others) + ")，跳过待人工审阅")
                return result

        # ④ 动作判定: 改名 + 移动
        need_rename = old_name != target
        cur_parent_norm = (parent_path or "").rstrip("/")
        need_move = cur_parent_norm != year_dir_path

        # 搬月护栏：磁链 dn 日期与 EGS 当前展示月份不同，必须人工批准后才改展示月份。
        approved_month = str(actual_release_ts or release_ts or egs_date or "")[:7]
        if str(dn_date)[:7] != approved_month and not confirmed_month_shift:
            result["status"] = "month_shift_confirm"
            result["confirmation_kind"] = "month_shift"
            result["proposed_actual_release_ts"] = dn_date
            result["proposed_actual_release_month"] = str(dn_date)[:7]
            result["requires_confirmation"] = True
            result["message"] = (
                f"磁链日期 {dn_date} 与 EGS 展示月份 {approved_month} 不同，"
                "需确认跳票/搬月后才可整理。"
            )
            return result

        # 跨年移动护栏：旧作/复刻目录可能因名称包含而误定位。
        # 这种移动影响老数据，必须人工确认后才能执行。
        source_year = _source_year(parent_path, old_name)
        target_year = int(dn_date[:4])
        if need_move and source_year and source_year != target_year and not confirmed_cross_year:
            result["status"] = "cross_year_confirm"
            result["source_year"] = source_year
            result["target_year"] = target_year
            result["requires_confirmation"] = True
            result["message"] = (
                f"跨年移动需人工确认：{source_year} → {target_year}。"
                "请确认这不是把前作/旧数据误判成当前作品。"
            )
            return result

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
                if submitted == 0 and dry_run:
                    result["status"] = "would_set_downloaded"
                    result["message"] = "预览: 将恢复 submitted_115=1"
                elif submitted == 0:
                    conn.execute(
                        "UPDATE egs_games SET submitted_115=1 WHERE date=? AND name=?",
                        (date, name),
                    )
                    conn.commit()
                    result["actions"].append("restore_sub")
                    result["status"] = "found_set_downloaded"
                    result["message"] = "文件夹名与位置均合规；已恢复 submitted_115=1"
                else:
                    result["status"] = "already_ok"
                    result["message"] = "文件夹名与位置均已符合规范"
            if not dry_run:
                record_date, shift_error = date, None
                if confirmed_month_shift:
                    record_date, shift_error = _apply_month_shift(conn, date, name, dn_date)
                    if shift_error:
                        result.update(shift_error)
                        return result
                    result["new_date"] = record_date
                    result["actions"].append("month_shift")
                _save_if_changed(conn, record_date, name, cid=cid, pid=pid, folder_name=old_name,
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
        if (need_rename or need_move) and target_pid:
            children = list_dir_children_names(target_pid)
            if children is None:
                result["status"] = "error"
                result["message"] = "无法读取目标目录，未执行整理"
                return result
            if target in children:
                result["status"] = "conflict"
                result["message"] = f"目标目录已存在同名项: {target}"
                if not dry_run:
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

        record_operation(conn, date, name, result)

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

        record_date, shift_error = date, None
        if confirmed_month_shift:
            record_date, shift_error = _apply_month_shift(conn, date, name, dn_date)
            if shift_error:
                result.update(shift_error)
                return result
            result["new_date"] = record_date
            result["actions"].append("month_shift")

        save_folder_record(conn, record_date, name, cid=cid, pid=pid, folder_name=target,
                           folder_path=new_path, target_name=target,
                           date_code=date_code, company=company, status=result["status"])

        # ⑦ 存在即补记流水线状态（downloaded=0 → 补记；曾被误重置 submitted → 恢复）
        if downloaded == 0:
            set_downloaded(conn, record_date, name, cid)
            result["actions"].append("set_dl")
        elif submitted == 0:
            conn.execute(
                "UPDATE egs_games SET submitted_115=1 WHERE date=? AND name=?",
                (record_date, name),
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
        "UPDATE egs_games SET downloaded=1, submitted_115=1 WHERE date=? AND name=?",
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



def record_operation(conn, date, name, result):
    """Keep original cloud locations even when a later rename/move fails."""
    conn.execute("CREATE TABLE IF NOT EXISTS egs_115_operations (id INTEGER PRIMARY KEY, date TEXT, name TEXT, payload TEXT, created_at INTEGER)")
    conn.execute("INSERT INTO egs_115_operations(date,name,payload,created_at) VALUES (?,?,?,?)",
                 (date, name, json.dumps(result, ensure_ascii=False), int(time.time())))
    conn.commit()
