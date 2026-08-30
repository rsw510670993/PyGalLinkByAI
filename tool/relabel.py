"""磁链解析与重标注（Phase 3 核心）。

dn 为唯一时间权威（延续 organize v2 哲学）：
- release_ts  <- dn [YYMMDD] -> YYYY-MM-DD（真实发布时间）
- date        <- dn 发布月（展示月/日历桶/115保存年月目录随之变化）
- name        <- dn 游戏名段（去尾部标记/特典/版次后缀，dn与getchu表记以dn为准）
- company     <- dn 公司段
- getchu_date / getchu_name / getchu_company：getchu 登记身份，永不改动
  （爬虫/去重/对账按此匹配，防止重标注后被重复爬取/重复入库）

安全护栏：
- 关联性校验：dn 文件名与游戏名评分（复用 nyaa_match 名字维度）>=12 才可信，
  无关磁链（历史旧流程"取第一条"兜底产物）不重标注不搬月（status=dn_mismatch）
- PK 冲突降级：目标 (月,名) 被占 -> 依次退化为 仅改名 / 仅搬月 / 放弃
- 原值保留：name_orig / company_orig（仅真实变更时记录，可回溯）

幂等：release_ts 已设置的行跳过（--force 重做；迁移自动补做 date 搬月）
纯 DB 操作，无网络/AI；默认 dry-run。
"""
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from .core import open_db, ensure_getchu_schema
from .dedup_service import (
    load_delete_list,
    ensure_identity_schema,
)
from .nyaa_match import score_candidate
from .runtime import read_config

# 公司段的噪音词（避免把尾部标记误判为公司）
_COMPANY_NOISE = {
    "serial", "crack", "crack updated", "update", "repack", "dl版",
    "特典", "manual", "trim", "1080p", "720p", "complete",
}
_EXTRA_EDITION_TOKENS = [
    "DL版", "ダウンロード版", "パッケージ版",
    # 汉化/repack 组后缀（其日期码仍为游戏原发售日，可安全剥离）
    "完全汉化硬盘版", "汉化硬盘版", "完全汉化版", "汉化中文版", "简体中文版",
    "繁体中文版", "官方中文版", "汉化版", "中文版", "硬盘版",
]
# dn 文件名与游戏名的最低关联分（>=12 = 部分匹配/主标题命中；0 = 大概率无关磁链）
_NAME_RELATED_MIN = 12


def ensure_relabel_schema(conn):
    """relabel 专属列 + 爬虫身份列（幂等）"""
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
    ensure_identity_schema(conn)


def parse_magnet_dn(link):
    """磁链 -> 解码后的 dn 原文（无 dn 返回 None）。"""
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


_BARE_DATE_RE = re.compile(
    r"(?<!\d)("
    r"(?:19|20)\d{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12]\d|3[01])日?"  # 2016-10-28 / 2026.07.24
    r"|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"                     # 20161028
    r"|\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"                              # 161028
    r")(?!\d)"
)


def _bare_date_in_text(text, release_date=None):
    """dn 文本中的裸日期（文件名时间兜底）：优先取与 getchu 发售日一致的，否则取最后一个。

    返回 YYYY-MM-DD 或 None。
    """
    if not text:
        return None
    hits = []
    for m in _BARE_DATE_RE.finditer(text):
        raw = m.group(1)
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 8 and ("-" in raw or "/" in raw or "." in raw or "年" in raw):
            iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            code = digits[2:]
        elif len(digits) == 8:
            iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            code = digits[2:]
        elif len(digits) == 6:
            iso = _fmt_release_ts(digits)
            code = digits
        else:
            continue
        if iso:
            hits.append((iso, code, digits))
    if not hits:
        return None
    rel_n = (release_date or "").replace("-", "") if release_date else ""
    for iso, code, digits in hits:
        if rel_n and digits == rel_n:
            return iso
    iso, _c, _d = hits[-1]
    return iso


def _fmt_release_ts(date_code):
    """[YYMMDD] 或 [YYYYMMDD] -> YYYY-MM-DD；非法返回 None。"""
    if not date_code:
        return None
    if re.fullmatch(r"\d{8}", date_code):
        yy, mm, dd = date_code[:4], date_code[4:6], date_code[6:8]
        year = yy
    elif re.fullmatch(r"\d{6}", date_code):
        yy, mm, dd = date_code[:2], date_code[2:4], date_code[4:6]
        year = f"20{yy}"
    else:
        return None
    try:
        mi, di = int(mm), int(dd)
    except ValueError:
        return None
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return None
    return f"{year}-{mm}-{dd}"


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
    # girlcelly 命名习惯「名 名 + 特典」-> 压缩连续重复词（长度>=4才压，避免误伤叠词）
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


def extract_dn_parts(link, delete_list=None, release_date=None):
    """磁链 -> {dn, date_code, release_ts, getchu_id, company, name}。

    release_date: getchu 登记发售日（YYYY-MM-DD），用于多日期码时的优先匹配。
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

    # 1. 前导日期码段：连续的 6 位[YYMMDD] 或 8 位[YYYYMMDD] 有效日期码
    #    约定：[种子发布日][游戏发售日]（最后一码多为发售日）；
    #    汉化合集包格式为 [游戏A发售日][游戏B发售日] —— 两码都可能是"本作"发售日。
    #    选择规则：优先取与 getchu 登记发售日精确一致的码（真实发售时间）；
    #    无一致者取最后一码；非法月日的数字段（getchu id 等）不参与。
    def _valid_ymd(code):
        if re.fullmatch(r"\d{8}", code):
            return 1 <= int(code[4:6]) <= 12 and 1 <= int(code[6:8]) <= 31
        return re.fullmatch(r"\d{6}", code) and 1 <= int(code[2:4]) <= 12 \
            and 1 <= int(code[4:6]) <= 31

    def _is_datecode(content):
        return bool(re.fullmatch(r"\d{6}", content) and _valid_ymd(content)) \
            or bool(re.fullmatch(r"\d{8}", content) and _valid_ymd(content))

    di = None
    for i, (s0, e0, c0) in enumerate(segs):
        if _is_datecode(c0):
            di = i
            break
    if di is None:
        return out
    run = [di]
    while run[-1] + 1 < len(segs):
        nxt = segs[run[-1] + 1][2]
        if _is_datecode(nxt):
            run.append(run[-1] + 1)
        else:
            break
    # 多码选择：优先与 getchu 发售日精确一致的码（YYMMDD 与 YYYYMMDD 均可比）
    rel_n8 = (release_date or "").replace("-", "") if release_date else ""
    rel_n6 = rel_n8[2:] if len(rel_n8) == 8 else ""
    chosen = None
    if rel_n8 and len(run) > 1:
        for idx in run:
            c = segs[idx][2]
            if c == rel_n8 or (rel_n6 and c == rel_n6):
                chosen = idx
                break
    if chosen is None:
        chosen = run[-1]
    out["date_code"] = segs[chosen][2]
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


def _name_related_norm(s):
    """与 nyaa_match._norm 相同的规范化（供遮字通配匹配 dn 侧使用）。"""
    from .nyaa_match import _norm as _n

    return _n(s)


def _name_related(getchu_name, getchu_company, dn_text):
    """dn 文件名与游戏名的关联性（防历史错配磁链污染日历）。

    返回 (bool, name_score, detail)。复用 nyaa_match 名字维度打分。
    """
    game = {"name": getchu_name or "", "company": getchu_company or ""}
    _, detail = score_candidate(game, {"nyaa_title": dn_text or "", "nyaa_date": None})
    # detail 值语义不统一：name_partial 存比率(frac)，其余存分数 → 按键归一化为分数
    if "name_exact" in detail:
        pts = 30
    elif "name_main" in detail:
        pts = 25
    elif "name_partial" in detail:
        v = detail["name_partial"]
        pts = 25 if isinstance(v, float) and v >= 0.85 else 12
    else:
        pts = 0
    # 短名（规范化<4字符）低于评分门槛：行名直接包含于dn即视为相关
    if pts < _NAME_RELATED_MIN:
        from .nyaa_match import _norm as _n2

        gn = _n2(getchu_name or "")
        if 1 <= len(gn) < 4 and gn and gn in _n2(dn_text or ""):
            pts = _NAME_RELATED_MIN
            detail["name_short"] = True
    # 仍不足 → 遮字通配复检：getchu 的审查遮字（○●等）在 dn 中是真实文字，
    # 把遮字位置当"任意一字符"用正则匹配 dn
    if pts < _NAME_RELATED_MIN:
        raw_g = re.sub(r"\s+", "", (getchu_name or "").lower())
        raw_g = re.sub(r"[〜～〰]", "~", raw_g)
        raw_g = re.sub(r"[＋]", "+", raw_g)
        raw_g = re.sub(r"[＆]", "&", raw_g)
        raw_g = re.sub(r"[：]", ":", raw_g)
        raw_g = re.sub(r"[；]", ";", raw_g)
        raw_g = re.sub(r"[！]", "!", raw_g)
        raw_g = re.sub(r"[？]", "?", raw_g)
        raw_g = re.sub(r"[-－−―‐﹣]", "", raw_g)
        pattern = "".join(
            "." if re.match(r"[○●◎◯⭘]", ch) else re.escape(ch)
            for ch in raw_g
        )
        if 2 <= len(pattern) <= 80 and dn_text:
            dn_norm = _name_related_norm(dn_text)
            if re.search(pattern, dn_norm):
                pts = _NAME_RELATED_MIN
                detail["name_masked"] = True
    # 仍不足 → 子序列包含：dn 中插入了注释（如「ボクと彼女（ナース）の研修日誌」），
    # 行名字符按序出现在 dn 中即视为相关（长名要求更高的字符覆盖率）
    if pts < _NAME_RELATED_MIN:
        from .nyaa_match import _norm as _n4

        gn4 = _n4(getchu_name or "")
        dn4 = _n4(dn_text or "")
        if len(gn4) >= 6 and len(gn4) <= 60 and dn4:
            it = iter(dn4)
            if all(ch in it for ch in gn4):
                pts = _NAME_RELATED_MIN
                detail["name_subseq"] = True
    return pts >= _NAME_RELATED_MIN, pts, detail


def _cascade_move(conn, old_date, old_name, new_date, new_name):
    """展示键 (date,name) 变更后同步引用表。"""
    # getchu_115_folders（PK=date,name，目标冲突则合并填充后删除旧行）
    fr = conn.execute(
        "SELECT cid, pid, pick_code, folder_name, folder_path, target_name,"
        " date_code, company, status FROM getchu_115_folders WHERE date=? AND name=?",
        (old_date, old_name),
    ).fetchone()
    if fr:
        exists = conn.execute(
            "SELECT 1 FROM getchu_115_folders WHERE date=? AND name=?",
            (new_date, new_name),
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
                (*fr[:7], new_date, new_name),
            )
            conn.execute(
                "DELETE FROM getchu_115_folders WHERE date=? AND name=?",
                (old_date, old_name),
            )
        else:
            conn.execute(
                "UPDATE getchu_115_folders SET date=?, name=? WHERE date=? AND name=?",
                (new_date, new_name, old_date, old_name),
            )
    # nyaa_candidates（UNIQUE date,name,infohash；目标冲突则删旧行保新语义）
    dup = conn.execute(
        "SELECT 1 FROM nyaa_candidates WHERE date=? AND name=? LIMIT 1",
        (new_date, new_name),
    ).fetchone()
    if dup:
        conn.execute(
            "DELETE FROM nyaa_candidates WHERE date=? AND name=?",
            (old_date, old_name),
        )
    else:
        conn.execute(
            "UPDATE nyaa_candidates SET date=?, name=? WHERE date=? AND name=?",
            (new_date, new_name, old_date, old_name),
        )


def _apply_row(conn, row, parts, dry_run, plan):
    """对单行应用重标注（含搬月与降级守卫）。row 为 dict。"""
    name = row["name"]
    company = row["company"] or ""
    date = row["date"]

    release_ts = parts["release_ts"]
    nd = release_ts[:7] if release_ts else None
    nd = nd if nd and nd != date else None
    new_company = parts["company"] if parts["company"] and parts["company"] != company else None
    nn = parts["name"] if parts["name"] and parts["name"] != name else None
    # 改名护栏：提取名与 getchu 名需有关联（合集包dn按" + "切分可能取出另一作的名）
    if nn is not None:
        ok, _pts, _d = _name_related(row.get("getchu_name") or name, row.get("getchu_company") or "", nn)
        if not ok:
            nn = None

    # 与 getchu 登记发售日的偏差（天数，供人工复核大偏差搬月）
    gap_days = None
    rel = row.get("release_date")
    if rel and release_ts:
        try:
            from datetime import date as _d

            y1, m1, d1 = (int(x) for x in rel.split("-"))
            y2, m2, d2 = (int(x) for x in release_ts.split("-"))
            gap_days = abs((_d(y2, m2, d2) - _d(y1, m1, d1)).days)
        except Exception:
            gap_days = None

    change = {
        "name": name,
        "date": date,
        "release_ts": release_ts,
        "company_new": new_company,
        "name_new": nn,
        "date_new": nd,
        "status": "ok",
        "related": None,
        "gap_days": gap_days,
    }

    if dry_run:
        return change

    # 目标键降级尝试: (nd,nn) -> (date,nn) 仅改名 -> (nd,name) 仅搬月 -> 放弃键变更
    def key_free(d, n):
        if d == date and n == name:
            return True
        return not conn.execute(
            "SELECT 1 FROM getchu_games WHERE date=? AND name=?", (d, n)
        ).fetchone()

    if nd is not None and nn is not None:
        if not key_free(nd, nn):
            if key_free(date, nn):
                change["status"] = "partial_date_only"
                nd = None
            elif key_free(nd, name):
                change["status"] = "partial_name_only"
                nn = None
            else:
                change["status"] = "key_conflict"
                nd = nn = None
    elif nn is not None:
        if not key_free(date, nn):
            change["status"] = "name_conflict"
            nn = None
    elif nd is not None:
        if not key_free(nd, name):
            change["status"] = "date_conflict"
            nd = None

    gid_val = None
    if parts["getchu_id"] and not row["getchu_id"]:
        dup = conn.execute(
            "SELECT 1 FROM getchu_games WHERE getchu_id=? AND NOT (date=? AND name=?)",
            (parts["getchu_id"], date, name),
        ).fetchone()
        if not dup:
            gid_val = parts["getchu_id"]
            change["getchu_id_backfill"] = parts["getchu_id"]

    # nyaa_name 以实际磁链 dn 为准（修正历史元数据漂移）
    nyaa_synced = bool(parts.get("dn")) and (row.get("nyaa_name") or "") != parts["dn"]
    conn.execute(
        """
        UPDATE getchu_games
        SET date=?, name=?, company=?, release_ts=?, nyaa_name=?,
            name_orig=COALESCE(name_orig, ?),
            company_orig=COALESCE(company_orig, ?),
            getchu_id=COALESCE(getchu_id, ?)
        WHERE date=? AND name=?
        """,
        (nd or date, nn or name, new_company or company, release_ts,
         parts["dn"] or row.get("nyaa_name"),
         name if nn else None,
         company if new_company else None,
         gid_val, date, name),
    )
    if nyaa_synced:
        plan["nyaa_name_synced"] = plan.get("nyaa_name_synced", 0) + 1
    if (nd and nd != date) or (nn and nn != name):
        _cascade_move(conn, date, name, nd or date, nn or name)
    change["date_new"] = nd
    change["name_new"] = nn
    plan["applied"] += 1
    return change


def relabel_month(year, month, conn=None, config=None, force=False, dry_run=True):
    """对指定 getchu 登记月份有磁链的行做 dn 重标注。返回计划/结果。"""
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
        "with_link": 0, "already": 0, "migrated": 0,
        "no_dn": 0, "no_dn_date": 0, "dn_mismatch": 0,
        "applied": 0, "nyaa_name_synced": 0,
        "changes": [], "errors": [],
    }

    try:
        rows = conn.execute(
            """
            SELECT date, name, company, link, release_ts, name_orig,
                   company_orig, getchu_id, getchu_date, getchu_name,
                   getchu_company, nyaa_name, release_date
            FROM getchu_games
            WHERE getchu_date=? AND link IS NOT NULL AND link != ''
            ORDER BY name
            """,
            (month_key,),
        ).fetchall()
        cols = ["date", "name", "company", "link", "release_ts", "name_orig",
                "company_orig", "getchu_id", "getchu_date", "getchu_name",
                "getchu_company", "nyaa_name", "release_date"]
        plan["with_link"] = len(rows)

        for r in rows:
            row = dict(zip(cols, r))
            if row["release_ts"] and not force:
                # 已重标注 -> 迁移补做 date 搬月（旧版本重标注只写 release_ts 未搬月）
                target = row["release_ts"][:7]
                if target == row["date"]:
                    plan["already"] += 1
                    continue
                if dry_run:
                    plan["migrated"] += 1
                    if len(plan["changes"]) < 400:
                        plan["changes"].append({
                            "name": row["name"], "date": row["date"],
                            "release_ts": row["release_ts"], "date_new": target,
                            "name_new": None, "company_new": None,
                            "status": "migrate_preview", "related": None,
                        })
                    continue
                if not conn.execute(
                    "SELECT 1 FROM getchu_games WHERE date=? AND name=?",
                    (target, row["name"]),
                ).fetchone():
                    parts = {"release_ts": row["release_ts"], "company": None,
                             "name": None, "getchu_id": None, "dn": None,
                             "date_code": None}
                    ch = _apply_row(conn, row, parts, False, plan)
                    ch["status"] = "migrated"
                    plan["migrated"] += 1
                    if len(plan["changes"]) < 400:
                        plan["changes"].append(ch)
                else:
                    plan["already"] += 1
                continue

            parts = extract_dn_parts(
                row["link"], delete_list, release_date=row.get("release_date")
            )
            if not parts["dn"]:
                plan["no_dn"] += 1
                continue
            if not parts["release_ts"]:
                # 时间降级链：dn日期码 → 文件名裸日期 → getchu预定时间(release_date)
                bare = _bare_date_in_text(parts["dn"], row.get("release_date"))
                if bare:
                    parts["release_ts"] = bare
                    parts["date_code"] = bare.replace("-", "")[2:]
                elif row.get("release_date"):
                    parts["release_ts"] = row["release_date"]
                    parts["ts_fallback"] = "getchu_scheduled"
                else:
                    plan["no_dn_date"] += 1
                    continue

            # 关联性校验（防历史错配磁链搬错月/改名）
            related, pts, _detail = _name_related(
                row["getchu_name"], row["getchu_company"], parts["dn"]
            )
            if not related:
                plan["dn_mismatch"] += 1
                if len(plan["changes"]) < 400:
                    plan["changes"].append({
                        "name": row["name"], "date": row["date"],
                        "status": "dn_mismatch", "related": pts,
                        "release_ts": None, "name_new": None,
                        "company_new": None, "date_new": None,
                    })
                continue

            change = _apply_row(conn, row, parts, dry_run, plan)
            change["related"] = pts
            if (change.get("name_new") or change.get("company_new")
                    or change.get("date_new") or not row["release_ts"]):
                if len(plan["changes"]) < 400:
                    plan["changes"].append(change)

        if not dry_run:
            conn.commit()
        return plan
    finally:
        if own_conn:
            conn.close()


def relabel_year(year, config=None, force=False, dry_run=True):
    """全年 12 个月依序重标注（按 getchu 登记月桶）。"""
    plans = []
    for month in range(1, 13):
        plans.append(relabel_month(year, month, config=config, force=force, dry_run=dry_run))
    return plans


def relabel_all(config=None, force=False, dry_run=True):
    """全库历史数据重标注（所有 getchu 登记年份）。"""
    conn = open_db()
    ensure_getchu_schema(conn)
    try:
        years = [
            int(r[0]) for r in conn.execute(
                "SELECT DISTINCT substr(getchu_date,1,4) FROM getchu_games ORDER BY 1"
            )
        ]
    finally:
        conn.close()
    plans = []
    for y in years:
        plans.extend(relabel_year(y, config=config, force=force, dry_run=dry_run))
    return plans, years


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
        name_changed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM getchu_games WHERE company_orig IS NOT NULL")
        company_changed = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM getchu_games WHERE release_ts IS NOT NULL"
            " AND substr(date,1,7) != substr(release_ts,1,7)"
        )
        release_month_diff = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM getchu_games WHERE getchu_date IS NOT NULL AND date != getchu_date"
        )
        display_moved = cur.fetchone()[0]
        return {
            "with_link": with_link,
            "release_ts_set": relabeled,
            "pending": pending,
            "name_changed": name_changed,
            "company_changed": company_changed,
            "release_month_diff": release_month_diff,
            "display_moved": display_moved,
        }
    finally:
        if own_conn:
            conn.close()
