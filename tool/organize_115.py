"""
整理115：定位游戏文件夹并重命名为 [发布年月日][公司]游戏名

- 日期码优先级: 磁链dn中的[YYMMDD]（与已手动修正的文件夹一致）> DB release_date
- 修正记录写入 getchu.db 的 getchu_115_folders 表:
  后续整理/定位直接用 cid 精确寻址，不再依赖搜索
"""

import re
import time
from urllib.parse import unquote

from .core import open_db
from .runtime import read_config
from .p115_client import (
    locate_game_folder,
    rename_item,
    get_item_name,
    list_dir_children_names,
    parent_crumbs_path,
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


def save_folder_record(conn, date, name, cid=None, pid=None, pick_code=None,
                       folder_name=None, folder_path=None, target_name=None,
                       date_code=None, company=None, status=None):
    conn.execute(
        """
        INSERT OR REPLACE INTO getchu_115_folders
        (date, name, cid, pid, pick_code, folder_name, folder_path, target_name,
         date_code, company, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, name, cid, pid, pick_code, folder_name, folder_path, target_name,
         date_code, company, status, int(time.time())),
    )
    conn.commit()


def compute_date_code(magnet=None, release_date=None, date_field=None):
    """确定[YYMMDD]日期码: dn日期码 > release_date > 无(None)"""
    if magnet:
        m = re.search(r"dn=([^&]+)", magnet)
        if m:
            dn = unquote(m.group(1))
            codes = re.findall(r"\[(\d{6})\]", dn)
            if codes:
                return codes[0]
    if release_date:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(release_date).strip())
        if m:
            return m.group(1)[2:] + m.group(2) + m.group(3)
    return None


def compute_target_name(date_code, company, name, config=None):
    """目标文件夹名: [YYMMDD][公司]游戏名（格式可在config.json调整）"""
    if not date_code:
        return None
    config = config or {}
    fmt = config.get("organize_name_format", "[{date}][{company}]{name}")
    if company:
        return fmt.format(date=date_code, company=company, name=name)
    return "[{date}]{name}".format(date=date_code, name=name)


def organize_single(date, name, company=None, magnet=None, release_date=None,
                    dry_run=True, conn=None):
    """整理单个游戏: 定位 → (按需)重命名 → 记录到DB

    status:
      already_ok   文件夹名已是目标名（记录入库）
      renamed      已完成重命名（记录入库）
      would_rename 预览模式，将执行的重命名
      not_found    未在115中定位到文件夹
      ambiguous    命中多个同名候选目录，为安全跳过
      not_dir      命中的是文件而非目录
      conflict     目标名已存在于同级目录
      no_date_code 无法确定日期码
      error        其他错误
    """
    result = {
        "date": date, "name": name, "status": None,
        "old_name": None, "old_path": None, "target_name": None,
        "date_code": None, "message": None, "cid": None, "located_by": None,
    }

    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_folder_schema(conn)

    try:
        # 1) 游戏信息补全（company/magnet/release_date）
        if not company or not magnet or release_date is None:
            row = conn.execute(
                "SELECT company, link, release_date FROM getchu_games WHERE date=? AND name=?",
                (date, name),
            ).fetchone()
            if row:
                company = company or row[0]
                magnet = magnet or row[1]
                release_date = release_date if release_date is not None else row[2]

        date_code = compute_date_code(magnet, release_date, date)
        result["date_code"] = date_code
        if not date_code:
            result["status"] = "no_date_code"
            result["message"] = "无法确定发布日期码（dn无[YYMMDD]且无release_date）"
            return result

        target = compute_target_name(date_code, company, name, read_config())
        result["target_name"] = target

        old_name = None
        cid = None
        pid = None
        parent_path = None
        located_by = None

        # 2) DB记录精确寻址（不搜索）
        rec = get_folder_record(conn, date, name)
        if rec and rec.get("cid"):
            cur_name = get_item_name(rec["cid"])
            if cur_name:
                cid = rec["cid"]
                old_name = cur_name
                pid = rec.get("pid")
                located_by = "db_record"
                if pid:
                    parent_path = parent_crumbs_path(pid) or rec.get("folder_path", "").rsplit("/", 1)[0]
                else:
                    parent_path = rec.get("folder_path", "").rsplit("/", 1)[0] or None
            else:
                rec = None  # 记录失效（目录被删等），回退搜索

        # 3) 搜索定位
        if old_name is None:
            loc = locate_game_folder(magnet=magnet, name=name)
            if not loc or not loc.get("found"):
                result["status"] = "not_found"
                result["message"] = "未在115中定位到游戏文件夹"
                return result
            if loc.get("dir_matches", 0) > 1:
                result["status"] = "ambiguous"
                result["message"] = "命中多个候选目录，为安全起见跳过"
                result["candidates"] = [c.get("name") for c in loc.get("candidates", [])]
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

        # 4) 已是目标名 → 记录入库
        if old_name == target:
            result["status"] = "already_ok"
            result["message"] = "文件夹名已符合规范"
            save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=old_name,
                               folder_path=result["old_path"], target_name=target,
                               date_code=date_code, company=company, status="already_ok")
            return result

        # 5) 预览模式
        if dry_run:
            result["status"] = "would_rename"
            result["message"] = "预览: 将重命名"
            return result

        # 6) 重命名前冲突检查
        if pid:
            children = list_dir_children_names(pid)
            if children is not None and target in children:
                result["status"] = "conflict"
                result["message"] = "目标名已存在于同级目录，跳过"
                save_folder_record(conn, date, name, cid=cid, pid=pid, folder_name=old_name,
                                   folder_path=result["old_path"], target_name=target,
                                   date_code=date_code, company=company, status="conflict")
                return result

        # 7) 执行重命名
        rr = rename_item(cid, target)
        if not rr.get("success"):
            result["status"] = "error"
            result["message"] = "重命名失败: " + str(rr.get("message", ""))
            return result

        # 8) 校验并记录
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
                           date_code=date_code, company=company, status="renamed")
        return result
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        return result
    finally:
        if own_conn:
            conn.close()


def organize_batch(year, month=None, name=None, dry_run=True):
    """批量整理: 默认处理已下载(downloaded=1)的游戏"""
    conn = open_db()
    ensure_folder_schema(conn)
    try:
        sql = ("SELECT date, name, company, link, release_date FROM getchu_games "
               "WHERE substr(date,1,4)=? AND COALESCE(downloaded,0)=1 "
               "AND link IS NOT NULL AND link != ''")
        params = [str(year)]
        if month:
            sql += " AND CAST(substr(date,6) AS INTEGER)=?"
            params.append(int(month))
        if name:
            sql += " AND name=?"
            params.append(name)
        sql += " ORDER BY date, release_date, name"
        rows = conn.execute(sql, params).fetchall()

        results = []
        for date, gname, company, link, release_date in rows:
            r = organize_single(date, gname, company=company, magnet=link,
                                release_date=release_date, dry_run=dry_run, conn=conn)
            results.append(r)
            time.sleep(0.5)  # 温和的API节奏

        summary = {}
        for r in results:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        return {
            "year": year,
            "month": month,
            "name": name,
            "dry_run": dry_run,
            "total": len(results),
            "summary": summary,
            "results": results,
        }
    finally:
        conn.close()
