#!/usr/bin/env python3
"""
Getchu 缩略图数据刷新工具

解决历史数据缺失 getchu_id/thumb_url 的问题，分三个阶段：

  merge  (A) 合并新旧日期格式产生的重复记录
         - 新格式行 (YYYY-MM-DD) 的 gid/thumb_url 等字段"只填不覆盖"地并入旧格式行 (YYYY-MM)
         - 115 下载数据（link/downloaded/submitted等）只在新旧行互补时迁移，绝不丢失
         - 无独有数据的新格式行删除；无旧格式对应行的新格式行把日期规范为 YYYY-MM

  crawl  (B) 重新爬取清单，为旧记录补全 getchu_id/thumb_url/price/detail_url/release_date
         - 复用 core.get_all_getchu_games（内部 upsert 只更新扩展字段，不触碰115数据）

  thumbs (C) 批量下载缺失的缩略图文件（循环到没有待下载项为止）

用法:
  python tool/refresh_thumbnails.py --phase merge
  python tool/refresh_thumbnails.py --phase crawl --start-year 2008 --end-year 2026
  python tool/refresh_thumbnails.py --phase thumbs --sleep 0.25
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tool
from tool.core import open_db, ensure_getchu_schema
from tool.runtime import runtime_paths
from tool.getchu_detail import batch_download_thumbnails, get_games_without_thumbnails

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("refresh_thumbnails")


def _sidecar_stamp():
    """生成不会因同一秒连续执行而碰撞的侧车时间戳。"""
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}"


def phase_merge(conn):
    """合并新格式(YYYY-MM-DD)重复行到旧格式(YYYY-MM)行，返回统计 dict"""
    cursor = conn.cursor()
    stats = {"new_format_total": 0, "merged": 0, "renamed": 0,
             "deleted": 0, "kept_conflict": 0}

    # 扩展字段：只填不覆盖（旧行为空才写入）
    FILL_FIELDS = ["getchu_id", "thumb_url", "price", "detail_url", "release_date"]
    # 115相关字段：同样只填不覆盖，确保绝不丢失
    TRANSFER_FIELDS = ["link", "nyaa_name", "comment", "infohash_hex",
                       "submitted_pick_code"]

    cursor.execute("SELECT rowid FROM getchu_games WHERE date LIKE '____-__-__' ORDER BY rowid")
    new_ids = [r[0] for r in cursor.fetchall()]
    stats["new_format_total"] = len(new_ids)
    logger.info("发现 %d 条新格式(YYYY-MM-DD)记录待处理", len(new_ids))

    for nid in new_ids:
        cursor.execute("SELECT * FROM getchu_games WHERE rowid = ?", (nid,))
        cols = [d[0] for d in cursor.description]
        n = dict(zip(cols, cursor.fetchone()))
        if n is None or not n["date"].count("__") and len(n["date"]) != 10:
            pass  # 占位，下方统一判断
        ym = n["date"][:7]
        if len(n["date"]) != 10:
            continue  # 已被之前的操作改变，跳过

        # 查找旧格式对应行：同月同名，优先同公司
        cursor.execute(
            """SELECT rowid FROM getchu_games
               WHERE date = ? AND name = ? AND company = ? AND rowid != ? AND length(date) = 7""",
            (ym, n["name"], n["company"], nid),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                """SELECT rowid FROM getchu_games
                   WHERE date = ? AND name = ? AND rowid != ? AND length(date) = 7
                   ORDER BY rowid LIMIT 1""",
                (ym, n["name"], nid),
            )
            row = cursor.fetchone()

        if row:
            oid = row[0]
            # 1) 只填不覆盖：迁移扩展字段
            for f in FILL_FIELDS:
                cursor.execute(
                    f"""UPDATE getchu_games SET {f} = ?
                        WHERE rowid = ? AND ({f} IS NULL OR {f} = '')""",
                    (n[f], oid),
                )
            # size（媒体类型）也为空才填
            cursor.execute(
                "UPDATE getchu_games SET size = ? WHERE rowid = ? AND (size IS NULL OR size = '')",
                (n["size"], oid),
            )
            # 2) 115相关字段：旧行为空/0才迁移
            for f in TRANSFER_FIELDS:
                cursor.execute(
                    f"""UPDATE getchu_games SET {f} = ?
                        WHERE rowid = ? AND ({f} IS NULL OR {f} = '')""",
                    (n[f], oid),
                )
            for f in ["downloaded", "submitted_115"]:
                cursor.execute(
                    f"""UPDATE getchu_games SET {f} = ?
                        WHERE rowid = ? AND COALESCE({f}, 0) = 0""",
                    (n[f], oid),
                )
            # 3) 安全检查：迁移后新行是否还有未保留的115数据
            has_unique_link = bool(n["link"]) and _field_now_empty(cursor, oid, "link") is False
            # 重新读取旧行确认关键字段已保留
            kept = _row_has_115_data(cursor, oid)
            new_still_has = _row_has_115_data_by_values(n)
            if new_still_has and not _transferred_ok(cursor, oid, n):
                # 理论上不会走到这里：存在无法安全合并的115数据，保留新行
                stats["kept_conflict"] += 1
                logger.warning("⚠️ rowid=%s 与旧行存在115数据冲突，保留待人工检查: %s | %s",
                               nid, n["date"], n["name"][:30])
                continue
            cursor.execute("DELETE FROM getchu_games WHERE rowid = ?", (nid,))
            stats["merged"] += 1
            stats["deleted"] += 1
        else:
            # 没有旧格式对应行 → 日期规范化为 YYYY-MM
            cursor.execute(
                """UPDATE getchu_games SET date = ?
                   WHERE rowid = ? AND NOT EXISTS (
                       SELECT 1 FROM getchu_games g2
                       WHERE g2.date = ? AND g2.name = getchu_games.name AND g2.rowid != getchu_games.rowid
                   )""",
                (ym, nid, ym),
            )
            if cursor.rowcount > 0:
                stats["renamed"] += 1
            else:
                stats["kept_conflict"] += 1

    conn.commit()
    return stats


def _field_now_empty(cursor, rowid, field):
    cursor.execute(f"SELECT {field} FROM getchu_games WHERE rowid = ?", (rowid,))
    v = cursor.fetchone()[0]
    return v in (None, "")


def _dedup_key(name, aggressive=False):
    """DB端去重键：剥离补丁对应版后缀（if エロパッチ対応...）"""
    if not name:
        return ""
    import re as _re
    # 补丁对应版：if 与 エロパッチ対応 之间可有/无空格，尾部可能是 ＜入荷予定＞ 等
    key = _re.sub(r"\s*if\s*エロパッチ対応.*$", "", name).strip()
    if aggressive:
        # 额外剥离常见版本/店铺特典尾缀（仅用于精确匹配已有基底时）
        key = _re.sub(
            r"\s*(?:TREASURE BOX.*|プレミアム版|プレミアムエディション|プレミアム\s*$|4版|5版|2版|"
            r"DL版|ダウンロード版|パッケージ版|版$|特別版.*$|通常版|初回版|限定版|豪華版|同梱版|"
            r"セット版|人妻セット|メモリアル特装版.*|げっちゅ屋限定.*|Getchu.com限定.*|"
            r"描き下ろし.*|抱き枕カバー付.*|ドラマCDセット.*|B2タペストリーセット.*|"
            r"B2タペストリー.*|タペストリー.*|アクリルパネル付き|＋アクリルパネル付き|"
            r"アクリルジオラマつき|特製痛DVDドライブ付き|初回特典版.*|早期予約.*|＜.*予約＞|"
            r"シークレットBOX.*|Wスエード.*|アクアヴェール.*|復刻.*|"
            r"「.*」WスエードB2タペストリー付.*|森山しじみ.*|さいとうつかさ.*|noyF先生.*|"
            r"＋(?:noyF先生|.*描き下ろし).*)$",
            "", key,
        ).strip()
        # 同一内容的套装命名差异：先经过上面的“版”尾缀清理，再剥离套装类型。
        key = _re.sub(r"\s+(?:ゲーム|3点)セット$", "", key).strip()
        # “with パワーアップキット”和单独写“パワーアップキット”按同一版本处理。
        key = _re.sub(r"\s+with\s+(?=パワーアップキット$)", " ", key).strip()
    return key


def _is_complete_game_bundle(name):
    name = name or ""
    return bool(
        "ゲームセット" in name
        or name.endswith(" with パワーアップキット")
    )


def _edition_primary_rank(row):
    """版本变体主记录排序：数据完整性优先，其次保留包含完整游戏的套装。"""
    name = row.get("name") or ""
    return (
        -(1 if row.get("downloaded") else 0),
        -(1 if row.get("submitted_115") else 0),
        -(1 if row.get("link") else 0),
        -(1 if _is_complete_game_bundle(name) else 0),
        len(name),
    )


def phase_dedupe(conn, dry_run=False, start_date=None, end_date=None):
    """
    DB端合并补丁对应版重复记录（如：X 和 X if エロパッチ対応、X＆Y if エロパッチ対応）

    规则：
    - 只处理名称含 ' if エロパッチ対応' 的行（其他 if 扩展版是独立游戏，不动）
    - 优先精确合并：剥后缀后与同月同公司同名记录合并
    - 其次捆绑合并：剥后缀后含 ＆ 取第一段，与同月同公司同前缀记录合并
    - 主记录优先保留115下载数据（downloaded/submitted/link），其次保留短名称（基底版）
    - 从记录只填不覆盖地并入主记录，再删除；被删行写入JSON侧车（非DB备份）
    """
    import json
    import re as _re
    from pathlib import Path

    cursor = conn.cursor()
    where = ["name LIKE '%ifエロパッチ対応%'"]
    params = []
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)
    cursor.execute(
        f"SELECT rowid, * FROM getchu_games WHERE {' AND '.join(where)} ORDER BY rowid",
        tuple(params),
    )
    cols = [d[0] for d in cursor.description]
    patch_rows = [dict(zip(cols, r)) for r in cursor.fetchall()]

    stats = {"patch_rows": len(patch_rows), "merged": 0, "deleted": 0,
             "no_target": 0, "skipped_conflict": 0}
    deleted_rows = []

    for row in patch_rows:
        if row["date"].count("-") == 2:  # 新格式残留（理论上已被merge阶段清掉）
            continue
        base = _dedup_key(row["name"])
        target = None

        # 1) 精确匹配：同月同公司同名
        cursor.execute(
            "SELECT rowid FROM getchu_games WHERE date=? AND company=? AND name=? AND rowid!=?",
            (row["date"], row["company"], base, row["rowid"]),
        )
        r = cursor.fetchone()
        if r:
            target = r[0]
        # 2) 捆绑匹配：取＆第一段做前缀
        if target is None and "＆" in base:
            seg = base.split("＆")[0].strip()
            if len(seg) >= 3:
                cursor.execute(
                    "SELECT rowid FROM getchu_games WHERE date=? AND company=? AND name LIKE ? AND rowid!=? AND name NOT LIKE '% if エロパッチ対応%' ORDER BY length(name) LIMIT 1",
                    (row["date"], row["company"], seg + "%", row["rowid"]),
                )
                r = cursor.fetchone()
                if r:
                    target = r[0]

        if target is None:
            stats["no_target"] += 1
            continue

        # 选择主记录：若目标本身无115数据而补丁行有，则交换主从（保守起见仍以目标为主，数据合并到目标）
        # 为保证不丢数据，先把补丁行的115字段按“只填不覆盖”并入目标
        FILL_FIELDS = ["getchu_id", "thumb_url", "thumb_path", "price",
                       "detail_url", "release_date", "size"]
        TRANSFER_FIELDS = ["link", "nyaa_name", "comment", "infohash_hex",
                           "submitted_pick_code", "downloaded", "submitted_115"]

        for f in FILL_FIELDS:
            cursor.execute(
                f"UPDATE getchu_games SET {f} = ? WHERE rowid=? AND ({f} IS NULL OR {f}='')",
                (row[f], target),
            )
        for f in TRANSFER_FIELDS:
            if f in ("downloaded", "submitted_115"):
                cursor.execute(
                    f"UPDATE getchu_games SET {f} = ? WHERE rowid=? AND COALESCE({f},0)=0",
                    (row[f], target),
                )
            else:
                cursor.execute(
                    f"UPDATE getchu_games SET {f} = ? WHERE rowid=? AND ({f} IS NULL OR {f}='')",
                    (row[f], target),
                )

        # 校验：若补丁行有115数据但目标仍没有，保留补丁行（避免丢数据）
        cursor.execute(
            "SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid=?",
            (target,),
        )
        t_link, t_dl, t_sub = cursor.fetchone()
        has_loss = False
        if row["link"] and not t_link:
            has_loss = True
        if int(row["downloaded"] or 0) == 1 and int(t_dl or 0) != 1:
            has_loss = True
        if int(row["submitted_115"] or 0) == 1 and int(t_sub or 0) != 1:
            has_loss = True
        if has_loss:
            stats["skipped_conflict"] += 1
            continue

        # 补丁行有独立磁链但目标已有磁链时，把补丁磁链归档到目标备注（尽量保存115信息）
        if row["link"] and t_link and row["link"] != t_link:
            cursor.execute("SELECT comment FROM getchu_games WHERE rowid=?", (target,))
            t_comment = cursor.fetchone()[0]
            note = f"【补丁版原磁链】{row['link']}"
            if not (t_comment and note in t_comment):
                new_comment = (t_comment + "\n" + note) if t_comment else note
                cursor.execute("UPDATE getchu_games SET comment=? WHERE rowid=?", (new_comment, target))

        deleted_rows.append(row)
        cursor.execute("DELETE FROM getchu_games WHERE rowid=?", (row["rowid"],))
        stats["merged"] += 1
        stats["deleted"] += 1

    conn.commit()

    # 侧车：被删行JSON（轻量，不占DB空间）
    if deleted_rows:
        sidecar_dir = Path("/var/www/html/pyGal/db_backups")
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"dedup_deleted_{_sidecar_stamp()}.json"
        sidecar.write_text(json.dumps(deleted_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("被删行已写入侧车: %s", sidecar)

    return stats


def _row_has_115_data(cursor, rowid):
    cursor.execute(
        """SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid = ?""",
        (rowid,),
    )
    link, dl, sub = cursor.fetchone()
    return bool(link) or int(dl or 0) == 1 or int(sub or 0) == 1


def _row_has_115_data_by_values(n):
    return bool(n["link"]) or int(n["downloaded"] or 0) == 1 or int(n["submitted_115"] or 0) == 1


def _transferred_ok(cursor, oid, n):
    """确认新行的115数据都已保留在旧行（或新行本来就没有）"""
    cursor.execute(
        "SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid = ?", (oid,)
    )
    olink, odl, osub = cursor.fetchone()
    if n["link"] and not olink:
        return False
    if int(n["downloaded"] or 0) == 1 and int(odl or 0) != 1:
        return False
    if int(n["submitted_115"] or 0) == 1 and int(osub or 0) != 1:
        return False
    return True


def phase_crawl(conn, start_year, end_year):
    """重新爬取清单补全扩展字段（内部 upsert 不触碰115数据）"""
    logger.info("开始重爬 %d-%d 年清单，补全 getchu_id/thumb_url ...", start_year, end_year)
    ok = tool.get_all_getchu_games(start_year, end_year, 1, 12)
    logger.info("重爬完成: %s", "成功" if ok else "失败")
    return ok


def phase_analyze(conn):
    """只读分析：量化保守版本变体合并候选（不动DB）"""
    cursor = conn.cursor()
    cursor.execute("SELECT rowid, date, name, company, getchu_id, link, downloaded, submitted_115 FROM getchu_games ORDER BY date, company")
    rows = [dict(zip([d[0] for d in cursor.description], r)) for r in cursor.fetchall()]

    from collections import defaultdict
    groups = defaultdict(list)
    for row in rows:
        key = _dedup_key(row["name"], aggressive=True)
        if not key:
            continue
        groups[(row["date"], row["company"], key)].append(row)

    candidate_groups = []
    for (date, company, key), members in groups.items():
        if len(members) < 2:
            continue
        # 主记录：115数据优先，其次最短名称
        primary = min(members, key=lambda r: (
            -(1 if r["downloaded"] else 0),
            -(1 if r["submitted_115"] else 0),
            -(1 if r["link"] else 0),
            len(r["name"]),
        ))
        secondaries = [r for r in members if r["rowid"] != primary["rowid"]]
        # 仅当组内存在明显基底（最短名）时才视为可合并，避免把系列不同卷合并
        if len(primary["name"]) >= min(len(r["name"]) for r in members):
            candidate_groups.append({
                "date": date, "company": company,
                "primary": primary["name"], "primary_115": bool(primary["link"] or primary["downloaded"] or primary["submitted_115"]),
                "secondaries": [{"name": r["name"], "has_115": bool(r["link"] or r["downloaded"] or r["submitted_115"])} for r in secondaries],
            })

    total_rows = sum(1 + len(g["secondaries"]) for g in candidate_groups)
    rows_with_115_secondary = sum(1 for g in candidate_groups for s in g["secondaries"] if s["has_115"])
    print(f"候选去重组: {len(candidate_groups)} 组")
    print(f"涉及记录: {total_rows} 条（含主记录）")
    print(f"次记录中带115数据的: {rows_with_115_secondary} 条（合并时需归档磁链）")
    print("\n示例（前8组）:")
    for g in candidate_groups[:8]:
        print(f"  [{g['date']} {g['company']}] 主={g['primary'][:40]} (115={g['primary_115']})")
        for s in g["secondaries"]:
            print(f"     × {s['name'][:50]} (115={s['has_115']})")
    return {"groups": len(candidate_groups), "rows": total_rows, "rows_with_115_secondary": rows_with_115_secondary}


def phase_purge_platform_editions(conn, start_date=None, end_date=None):
    """
    排除主机平台版记录（对收集GALGAME不属于目标）

    识别规则：
    - 名称以空白+数字版结尾（2版=NS2版 / 4版=PS4版 / 5版=PS5版）
    - 或含 PlayStation 4版/5版
    - V2版 等前面带字母的版本号不误伤（前面不是空白）

    删除策略：
    - 同月同公司存在PC基底（去平台版后同名的记录）→ 只删除平台版行
    - 无PC基底 → 该主机版游戏整组删除（含 プレミアムボックス/TREASURE BOX 等变体）
    - 被删行写JSON侧车（非DB备份）
    """
    import re as _re
    import json
    from pathlib import Path

    cursor = conn.cursor()
    where = []
    params = []
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)
    sql = "SELECT rowid, * FROM getchu_games"
    if where:
        sql += f" WHERE {' AND '.join(where)}"
    sql += " ORDER BY date, company"
    cursor.execute(sql, tuple(params))
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]

    # 平台版行：\s+数字版 结尾 或 PlayStation X版
    pat = _re.compile(r"(?:\s+(?:2|4|5)版|PlayStation\s*(?:4|5)版)$")
    platform_rows = [r for r in rows if pat.search(r["name"])]

    # 按 (date, company, core) 分组：core = 去掉平台版尾缀后的名字
    groups = {}
    for r in platform_rows:
        core = _re.sub(r"\s+(?:2|4|5)版$", "", r["name"]).strip()
        core = _re.sub(r"PlayStation\s*(?:4|5)版$", "", core).strip()
        groups.setdefault((r["date"], r["company"], core), []).append(r)

    stats = {"platform_rows": len(platform_rows), "groups": len(groups),
             "deleted": 0, "with_pc_base": 0, "no_pc_base": 0, "skipped_115": 0}
    deleted_rows = []

    for (date, company, core), members in groups.items():
        # 是否存在PC基底：同月同公司 name == core
        cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE date=? AND company=? AND name=?",
                       (date, company, core))
        has_pc_base = cursor.fetchone()[0] > 0

        # 确定要删的行：有PC基底只删平台版成员；无PC基底删该组全部同前缀记录
        if has_pc_base:
            to_delete = members
            stats["with_pc_base"] += 1
        else:
            cursor.execute(
                "SELECT rowid, * FROM getchu_games WHERE date=? AND company=? AND name LIKE ?",
                (date, company, core + "%"),
            )
            sub_cols = [d[0] for d in cursor.description]
            to_delete = [dict(zip(sub_cols, r)) for r in cursor.fetchall()]
            stats["no_pc_base"] += 1

        for r in to_delete:
            # 有115数据则保留（避免丢失），否则删除
            if r.get("link") or int(r.get("downloaded") or 0) == 1 or int(r.get("submitted_115") or 0) == 1:
                stats["skipped_115"] += 1
                continue
            deleted_rows.append(r)
            cursor.execute("DELETE FROM getchu_games WHERE rowid=?", (r["rowid"],))
            stats["deleted"] += 1

    conn.commit()
    if deleted_rows:
        sidecar_dir = Path("/var/www/html/pyGal/db_backups")
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"purge_platform_deleted_{_sidecar_stamp()}.json"
        sidecar.write_text(json.dumps(deleted_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("被删主机版行已写入侧车: %s", sidecar)
    return stats


def phase_dedupe_cross_month(conn, start_date=None, end_date=None):
    """
    DB端合并跳票（跨月重复）记录：同公司+完整同名 出现在不同月份

    规则：
    - 主记录：优先有 getchu_id（正式发售记录）→ 最新 date → 有115数据 → rowid 大
    - 从记录：只填不覆盖迁移 扩展字段+115字段 到主记录；独立磁链归档到备注
    - 校验115无丢失后删除旧月份行；被删行写JSON侧车（非DB备份）
    """
    import json
    from pathlib import Path
    from collections import defaultdict

    cursor = conn.cursor()
    if start_date or end_date:
        where = []
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        cursor.execute(
            f"SELECT rowid, * FROM getchu_games WHERE {' AND '.join(where)} ORDER BY date, company",
            tuple(params),
        )
    else:
        cursor.execute("SELECT rowid, * FROM getchu_games ORDER BY date, company")
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]

    groups = defaultdict(list)
    for r in rows:
        groups[(r["company"], r["name"])].append(r)

    stats = {"cross_groups": 0, "rows": 0, "merged": 0, "deleted": 0,
             "skipped_conflict": 0}
    deleted_rows = []

    for (company, name), members in groups.items():
        months = set(m["date"] for m in members)
        if len(months) < 2:
            continue
        stats["cross_groups"] += 1
        stats["rows"] += len(members)

        # 主记录：有gid > 最新date > 有115 > rowid大
        primary = max(members, key=lambda r: (
            bool(r["getchu_id"]),
            r["date"],
            bool(r["link"] or r["downloaded"] or r["submitted_115"]),
            r["rowid"],
        ))
        secondaries = [r for r in members if r["rowid"] != primary["rowid"]]

        for row in secondaries:
            # 只填不覆盖扩展字段（getchu_id 唯一冲突跳过）
            for f in ["getchu_id", "thumb_url", "thumb_path", "price",
                      "detail_url", "release_date", "size"]:
                try:
                    cursor.execute(
                        f"UPDATE getchu_games SET {f}=? WHERE rowid=? AND ({f} IS NULL OR {f}='')",
                        (row[f], primary["rowid"]),
                    )
                except sqlite3.IntegrityError:
                    pass
            # 115字段互补
            for f in ["link", "nyaa_name", "comment", "infohash_hex", "submitted_pick_code"]:
                cursor.execute(
                    f"UPDATE getchu_games SET {f}=? WHERE rowid=? AND ({f} IS NULL OR {f}='')",
                    (row[f], primary["rowid"]),
                )
            for f in ["downloaded", "submitted_115"]:
                cursor.execute(
                    f"UPDATE getchu_games SET {f}=? WHERE rowid=? AND COALESCE({f},0)=0",
                    (row[f], primary["rowid"]),
                )
            # 独立磁链归档
            if row["link"]:
                cursor.execute("SELECT link FROM getchu_games WHERE rowid=?", (primary["rowid"],))
                p_link = cursor.fetchone()[0]
                if p_link and p_link != row["link"]:
                    cursor.execute("SELECT comment FROM getchu_games WHERE rowid=?", (primary["rowid"],))
                    p_comment = cursor.fetchone()[0]
                    note = f"【跳票前磁链】{row['link']}"
                    if not (p_comment and note in p_comment):
                        cursor.execute("UPDATE getchu_games SET comment=? WHERE rowid=?",
                                       ((p_comment + "\n" + note) if p_comment else note, primary["rowid"]))
            # 校验115无丢失
            cursor.execute("SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid=?", (primary["rowid"],))
            p_link, p_dl, p_sub = cursor.fetchone()
            if (row["link"] and not p_link) or \
               (int(row["downloaded"] or 0) == 1 and int(p_dl or 0) != 1) or \
               (int(row["submitted_115"] or 0) == 1 and int(p_sub or 0) != 1):
                stats["skipped_conflict"] += 1
                continue

            deleted_rows.append(row)
            cursor.execute("DELETE FROM getchu_games WHERE rowid=?", (row["rowid"],))
            stats["merged"] += 1
            stats["deleted"] += 1

    conn.commit()
    if deleted_rows:
        sidecar_dir = Path("/var/www/html/pyGal/db_backups")
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"dedup_cross_month_deleted_{_sidecar_stamp()}.json"
        sidecar.write_text(json.dumps(deleted_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("被删跨月重复行已写入侧车: %s", sidecar)
    return stats


def phase_dedupe_editions(conn, start_date=None, end_date=None):
    """
    DB端合并店铺特典/版本变体重复记录（保守规则：同月同公司剥后缀后同名）

    与 phase_dedupe（if エロパッチ対応）互补，覆盖 TREASURE BOX/早期予約/抱き枕カバー等。
    - 只合并组内存在明显基底（最短名）的情况
    - 主记录优先115数据，其次短名称；同gid多行以新记录(rowid最大)为准
    - 从记录115数据并入主记录；独立磁链归档到主记录备注
    - 被删行写JSON侧车（非DB备份）
    """
    import json
    from pathlib import Path
    from collections import defaultdict

    cursor = conn.cursor()
    cursor = conn.cursor()
    if start_date or end_date:
        where = []
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        cursor.execute(
            f"SELECT rowid, * FROM getchu_games WHERE {' AND '.join(where)} ORDER BY date, company",
            tuple(params),
        )
    else:
        cursor.execute("SELECT rowid, * FROM getchu_games ORDER BY date, company")
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]

    groups = defaultdict(list)
    for row in rows:
        key = _dedup_key(row["name"], aggressive=True)
        if not key:
            continue
        groups[(row["date"], row["company"], key)].append(row)

    stats = {"candidate_groups": 0, "merged": 0, "deleted": 0,
             "no_primary": 0, "skipped_conflict": 0}
    deleted_rows = []

    for (date, company, key), members in groups.items():
        if len(members) < 2:
            continue
        # 主记录选择：
        # 1) 同gid多行（名称不一致）→ 以新记录为准（rowid最大）
        # 2) 否则 115数据优先，其次短名称（基底版）
        gid_groups = defaultdict(list)
        for m in members:
            gid_groups[m["getchu_id"]].append(m)
        if len(gid_groups) == 1 and len(members) > 1 and members[0]["getchu_id"]:
            # 同gid不同名：保留最新记录
            primary = max(members, key=lambda r: r["rowid"])
        else:
            primary = min(members, key=_edition_primary_rank)
        secondaries = [r for r in members if r["rowid"] != primary["rowid"]]
        if len(primary["name"]) > min(len(r["name"]) for r in members) and not (
                len(gid_groups) == 1 and members[0]["getchu_id"]) and not \
                _is_complete_game_bundle(primary["name"]):
            stats["no_primary"] += 1
            continue

        stats["candidate_groups"] += 1
        for row in secondaries:
            # 只填不覆盖扩展字段（getchu_id 有唯一索引，冲突时跳过）
            for f in ["getchu_id", "thumb_url", "thumb_path", "price",
                      "detail_url", "release_date", "size"]:
                try:
                    cursor.execute(
                        f"UPDATE getchu_games SET {f}=? WHERE rowid=? AND ({f} IS NULL OR {f}='')",
                        (row[f], primary["rowid"]),
                    )
                except sqlite3.IntegrityError:
                    # getchu_id 已被其他行占用：跳过该字段
                    pass
            # 115字段互补
            for f in ["link", "nyaa_name", "comment", "infohash_hex", "submitted_pick_code"]:
                cursor.execute(
                    f"UPDATE getchu_games SET {f}=? WHERE rowid=? AND ({f} IS NULL OR {f}='')",
                    (row[f], primary["rowid"]),
                )
            for f in ["downloaded", "submitted_115"]:
                cursor.execute(
                    f"UPDATE getchu_games SET {f}=? WHERE rowid=? AND COALESCE({f},0)=0",
                    (row[f], primary["rowid"]),
                )
            # 独立磁链归档到主记录备注
            if row["link"]:
                cursor.execute("SELECT link FROM getchu_games WHERE rowid=?", (primary["rowid"],))
                p_link = cursor.fetchone()[0]
                if p_link and p_link != row["link"]:
                    cursor.execute("SELECT comment FROM getchu_games WHERE rowid=?", (primary["rowid"],))
                    p_comment = cursor.fetchone()[0]
                    note = f"【版本变体原磁链】{row['link']}"
                    if not (p_comment and note in p_comment):
                        cursor.execute("UPDATE getchu_games SET comment=? WHERE rowid=?",
                                       ((p_comment + "\n" + note) if p_comment else note, primary["rowid"]))
            # 校验115无丢失
            cursor.execute("SELECT link, downloaded, submitted_115 FROM getchu_games WHERE rowid=?", (primary["rowid"],))
            p_link, p_dl, p_sub = cursor.fetchone()
            if (row["link"] and not p_link) or \
               (int(row["downloaded"] or 0) == 1 and int(p_dl or 0) != 1) or \
               (int(row["submitted_115"] or 0) == 1 and int(p_sub or 0) != 1):
                stats["skipped_conflict"] += 1
                continue

            deleted_rows.append(row)
            cursor.execute("DELETE FROM getchu_games WHERE rowid=?", (row["rowid"],))
            stats["merged"] += 1
            stats["deleted"] += 1

    conn.commit()
    if deleted_rows:
        sidecar_dir = Path("/var/www/html/pyGal/db_backups")
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sidecar_dir / f"dedup_editions_deleted_{_sidecar_stamp()}.json"
        sidecar.write_text(json.dumps(deleted_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("被删行已写入侧车: %s", sidecar)
    return stats


def phase_thumbs(conn, sleep_seconds=0.25, batch_size=500):
    """循环下载缺失的缩略图，直到没有待下载项"""
    cursor = conn.cursor()
    total_stats = {"success": 0, "failed": 0, "skipped": 0}
    round_no = 0
    while True:
        round_no += 1
        pending = get_games_without_thumbnails(conn, batch_size)
        if not pending:
            logger.info("✅ 第%d轮：没有待下载的缩略图，全部完成", round_no)
            break
        logger.info("第%d轮：待下载 %d 条 (sleep=%.2fs)", round_no, len(pending), sleep_seconds)
        stats = batch_download_thumbnails(pending, conn, sleep_seconds=sleep_seconds)
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
        logger.info("第%d轮完成: 累计 成功%d 失败%d 跳过%d",
                    round_no, total_stats["success"], total_stats["failed"], total_stats["skipped"])
        if stats.get("success", 0) == 0 and stats.get("skipped", 0) == len(pending):
            # 本轮全部跳过，避免死循环
            logger.info("本轮全部跳过，结束")
            break
    return total_stats


def print_summary(conn, tag):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM getchu_games")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE getchu_id IS NOT NULL")
    with_gid = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE thumb_url IS NOT NULL")
    with_url = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE thumb_path IS NOT NULL")
    with_path = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE COALESCE(downloaded,0)=1")
    downloaded = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE COALESCE(submitted_115,0)=1")
    submitted = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE link IS NOT NULL")
    with_link = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE date LIKE '____-__-__'")
    new_fmt = cursor.fetchone()[0]
    logger.info(
        "[%s] 总数:%d | gid:%d thumb_url:%d 已下载图:%d | 115数据 downloaded:%d submitted:%d 磁链:%d | 新格式残留:%d",
        tag, total, with_gid, with_url, with_path, downloaded, submitted, with_link, new_fmt,
    )


def main():
    parser = argparse.ArgumentParser(description="Getchu 缩略图数据刷新")
    parser.add_argument("--phase", required=True, choices=["merge", "dedupe", "dedupe_editions", "dedupe_cross_month", "purge_platform", "analyze", "crawl", "thumbs", "status"],
                        help="执行阶段")
    parser.add_argument("--start-year", type=int, default=2008)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--sleep", type=float, default=0.25, help="缩略图下载限速秒数")
    parser.add_argument("--start-date", default=None, help="清理阶段日期过滤起点(YYYY-MM)")
    parser.add_argument("--end-date", default=None, help="清理阶段日期过滤终点(YYYY-MM)")
    args = parser.parse_args()

    paths = runtime_paths()
    conn = open_db(db_path=paths["db_path"])
    ensure_getchu_schema(conn)

    if args.phase == "status":
        print_summary(conn, "当前状态")
    elif args.phase == "merge":
        print_summary(conn, "merge前")
        stats = phase_merge(conn)
        logger.info("合并统计: %s", stats)
        print_summary(conn, "merge后")
    elif args.phase == "dedupe":
        print_summary(conn, "dedupe前")
        stats = phase_dedupe(conn, start_date=args.start_date, end_date=args.end_date)
        logger.info("补丁变体去重统计: %s", stats)
        print_summary(conn, "dedupe后")
    elif args.phase == "analyze":
        stats = phase_analyze(conn)
        logger.info("分析完成: %s", stats)
    elif args.phase == "purge_platform":
        print_summary(conn, "purge前")
        stats = phase_purge_platform_editions(conn, start_date=args.start_date, end_date=args.end_date)
        logger.info("主机平台版排除统计: %s", stats)
        print_summary(conn, "purge后")
    elif args.phase == "dedupe_cross_month":
        print_summary(conn, "dedupe_cross_month前")
        stats = phase_dedupe_cross_month(conn, start_date=args.start_date, end_date=args.end_date)
        logger.info("跨月跳票去重统计: %s", stats)
        print_summary(conn, "dedupe_cross_month后")
    elif args.phase == "dedupe_editions":
        print_summary(conn, "dedupe_editions前")
        stats = phase_dedupe_editions(conn, start_date=args.start_date, end_date=args.end_date)
        logger.info("版本变体去重统计: %s", stats)
        print_summary(conn, "dedupe_editions后")
    elif args.phase == "crawl":
        print_summary(conn, "crawl前")
        phase_crawl(conn, args.start_year, args.end_year)
        print_summary(conn, "crawl后")
    elif args.phase == "thumbs":
        print_summary(conn, "thumbs前")
        stats = phase_thumbs(conn, sleep_seconds=args.sleep)
        logger.info("下载统计: %s", stats)
        print_summary(conn, "thumbs后")

    conn.close()


if __name__ == "__main__":
    main()
