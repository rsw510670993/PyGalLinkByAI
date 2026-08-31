"""AI 去重服务：getchu 原始清单 → 规则预分组 → 第三方 AI 归组 → 入库。

设计要点
--------
- getchu_games 保持"一作品一行"语义（下游 nyaa/organize/calendar 均依赖），
  重复版次条目不落 getchu_games，只记录到 dedup_log / dedup_cache。
- 规则预分组：config delete 列表剥离版次后缀得到 base_name，
  (company, base_name) 相同的条目直接归组，无需 AI。
- AI 归组：一个月内未能被规则/缓存解析的组 + 已入库锚点行，
  交由 aigc2d（OpenAI 兼容接口）判定 unique / dup。
- 缓存：cache_key = sha1(company|base_name)，月度重爬零 AI 调用。
- AI 失败兜底：全部按 unique + base_name 入库（source=rule_fallback）。
"""
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime

import requests

from .core import get_raw_getchu_games, open_db, ensure_getchu_schema
from .runtime import read_config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://next.aigc2d.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_TIMEOUT = 90
DEFAULT_MAX_TOKENS = 4096
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "key", "aigc2d.key")


class DedupAIError(Exception):
    pass


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def ensure_identity_schema(conn):
    """爬虫身份列：getchu_date/getchu_name/getchu_company。

    date/name/company 是展示字段（Phase3 dn重标注会改动），
    爬虫/去重/对账永远以 getchu 登记值为身份，防止重复爬取与误合并。
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(getchu_games)")
    cols = {row[1] for row in cursor.fetchall()}
    for col, ddl in [
        ("getchu_date", "TEXT"),
        ("getchu_name", "TEXT"),
        ("getchu_company", "TEXT"),
    ]:
        if col not in cols:
            cursor.execute(f"ALTER TABLE getchu_games ADD COLUMN {col} {ddl}")
    # 一次性回填：身份 = 建列时的登记值（重标注历史值经 *_orig 恢复）
    cursor.execute(
        "UPDATE getchu_games SET getchu_date=date"
        " WHERE getchu_date IS NULL OR getchu_date=''"
    )
    cursor.execute(
        "UPDATE getchu_games SET getchu_name=COALESCE(name_orig, name)"
        " WHERE getchu_name IS NULL OR getchu_name=''"
    )
    cursor.execute(
        "UPDATE getchu_games SET getchu_company=COALESCE(company_orig, company)"
        " WHERE getchu_company IS NULL OR getchu_company=''"
    )
    conn.commit()


def ensure_dedup_schema(conn):
    ensure_identity_schema(conn)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(getchu_games)")
    cols = {row[1] for row in cursor.fetchall()}
    for col, ddl in [
        ("raw_name", "TEXT"),
        ("dedup_source", "TEXT"),
        ("dedup_confidence", "REAL"),
        ("dedup_reason", "TEXT"),
        ("dedup_updated_at", "TEXT"),
    ]:
        if col not in cols:
            cursor.execute(f"ALTER TABLE getchu_games ADD COLUMN {col} {ddl}")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS dedup_cache (
            cache_key TEXT PRIMARY KEY,
            company TEXT,
            base_name TEXT,
            verdict TEXT,
            canonical_name TEXT,
            target_company TEXT,
            target_name TEXT,
            rep_raw TEXT,
            confidence REAL,
            reason TEXT,
            model TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS dedup_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            company TEXT,
            raw_name TEXT,
            base_name TEXT,
            verdict TEXT,
            canonical_name TEXT,
            confidence REAL,
            source TEXT,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reconcile_state (
            date TEXT PRIMARY KEY,
            rows_hash TEXT,
            merged_count INTEGER DEFAULT 0,
            edition_count INTEGER DEFAULT 0,
            done_at TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reconcile_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            name TEXT,
            row_json TEXT,
            merged_into TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 规则分组
# ---------------------------------------------------------------------------

def load_delete_list(config=None):
    config = config or read_config()
    return sorted(config.get("delete", []), key=len, reverse=True)


def rule_base_name(name, delete_list=None):
    """剥除版次后缀 → base_name（词边界感知，不破坏相邻字符）。

    修复旧缺陷：'Nintendo Switch2版' 被剥成 '2版'（任意位置子串替换）。
    现在 token 前后必须是"非字母数字"边界才删除：
      - '進撃の巨人3 Nintendo Switch2版' → token 'Nintendo Switch' 后紧跟 '2'（字母数字）→ 不剥，保留原文
      - '小金井荘と金色の揚羽蝶 Nintendo Switch版' → 'Nintendo Switch' 后是 '版'（非字母数字）→ 剥除
    """
    if not name:
        return ""
    delete_list = delete_list if delete_list is not None else load_delete_list()
    base = _PLATFORM_EDITION_RE.sub(" ", name)
    for token in delete_list:
        if not token:
            continue
        pat = re.compile(
            r"(?<![0-9A-Za-zぁ-んァ-ヶ一-龠々ー])"
            + re.escape(token)
            + r"(?![0-9A-Za-z])"
        )
        base = pat.sub(" ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or name


# 平台版次整体剥除：平台词 + 可选空格 + 附带数字 + 版 → 一并移除（不留 2版/5版 残片）
#   '進撃の巨人3 Nintendo Switch2版'  → '進撃の巨人3'
#   '進撃の巨人3 PlayStation 5版'     → '進撃の巨人3'
#   '小金井荘と金色の揚羽蝶 Nintendo Switch版' → '小金井荘と金色の揚羽蝶'
_PLATFORM_EDITION_RE = re.compile(
    r"(?<![0-9A-Za-z])("
    r"ニンテンドースイッチ2|ニンテンドースイッチ|Nintendo\s*Switch|"
    r"プレイステーション[1-9]|PlayStation\s*[1-9]|PlayStation|"
    r"SWITCH|Switch|PS[1-9]|NSW"
    r")\s*[0-9]?\s*版?(?![0-9A-Za-z])"
)


def _strip_platform_edition(name):
    if not name:
        return ""
    return re.sub(r"\s+", " ", _PLATFORM_EDITION_RE.sub(" ", name)).strip()


# 平台词（词边界化后仍能识别）；用于识别平台版行
PLATFORM_TOKENS = (
    "Nintendo Switch2", "Nintendo Switch", "ニンテンドースイッチ2", "ニンテンドースイッチ",
    "Switch2", "SWITCH", "Switch", "PlayStation5", "PlayStation 5", "PlayStation4",
    "PlayStation 4", "PS5", "PS4", "プレイステーション5", "プレイステーション4",
)


def has_platform_suffix(name):
    """名字含平台版次词（Nintendo Switch2版 / PlayStation 5版 等）→ 独立行，禁 dup"""
    n = name or ""
    return any(t in n for t in PLATFORM_TOKENS)


def rule_rep_rank(raw_name):
    """组内代表优先级：越小越优先（通常版/パッケージ版 > DL版）。"""
    n = raw_name or ""
    if any(k in n for k in ("ダウンロード版", "DL版", "DL版", "download版")):
        return 5
    if "特典" in n or "同梱" in n:
        return 3
    if any(k in n for k in ("初回", "限定", "豪華", "限定版")):
        return 2
    return 1


def _cache_key(company, base_name):
    raw = f"{company or ''}\x00{base_name or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# aigc2d 通信
# ---------------------------------------------------------------------------

def _load_api_key():
    env = os.environ.get("AIGC2D_API_KEY", "").strip()
    if env:
        return env
    try:
        with open(KEY_PATH, "r", encoding="utf-8") as f:
            return f.read().strip(" \t\n\r\0\x0B\"'")
    except Exception:
        return ""


def ai_chat(messages, model=None, max_tokens=None, timeout=None, config=None):
    config = config or read_config()
    section = config.get("aigc2d") or {}
    base_url = str(section.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = model or section.get("model") or DEFAULT_MODEL
    max_tokens = int(max_tokens or section.get("max_tokens") or DEFAULT_MAX_TOKENS)
    timeout = int(timeout or section.get("timeout") or DEFAULT_TIMEOUT)
    api_key = _load_api_key()
    if not api_key:
        raise DedupAIError("aigc2d key 未配置: tool/config/key/aigc2d.key")

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    last_err = None
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content, model
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("aigc2d 调用失败(第%s次): %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
    raise DedupAIError(f"aigc2d 调用失败: {last_err}")


def _extract_json_array(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    raise DedupAIError("AI 返回内容无法解析为 JSON 数组")


# ---------------------------------------------------------------------------
# 月度去重主流程
# ---------------------------------------------------------------------------

def _build_ai_prompt(eid_groups, anchors):
    entries_desc = []
    for g in eid_groups:
        members = " / ".join(g["members"][:6])
        entries_desc.append(
            f'- {g["eid"]}: company={g["company"]!r} base={g["base_name"]!r} raw=[{members}]'
        )
    anchors_desc = [f'- {a["aid"]}: company={a["company"]!r} name={a["name"]!r}' for a in anchors]
    prompt = f"""你是 galgame 数据库的数据清洗助手。以下条目来自 Getchu 同一个月的上架清单，已经按版次关键词做了初步分组。

【待判定条目（E开头）】
{chr(10).join(entries_desc)}

【本月已入库锚点作品（A开头，已存在数据库，不要输出它们本身）】
{chr(10).join(anchors_desc) if anchors_desc else '-（无）'}

判定规则：
1. "unique" = 独立作品，与其它条目/锚点都不是同一作品。
2. "dup" = 与某个锚点或另一个待判定条目是同一作品的不同版次（初回版/通常版/DL版/限定版/特典付き等）。
   - 廉価版・再販・合算合集（○○1+2 等）与本体视为不同作品（unique）。
   - 平台版（Nintendo Switch2版/Switch版/PlayStation 5版/PS4版/PS5版等）与本体是同一作品的不同平台版本：判 dup 到本体或组内代表；整组只有平台版时，代表判 unique 且 canonical_name 必须剥离平台词与版次（例：進撃の巨人3 Nintendo Switch2版 → 進撃の巨人3）。
   - 判 dup 时 target 必须是锚点 A* 或另一个条目 E* 的编号。
3. canonical_name：unique 时给出作品本体名（剥离版次后缀，保留原文写法与用字，默认用 base）。
4. confidence 0~1，reason 用不超过 20 字的日文/中文简述。

只输出 JSON 数组，格式：
[{{"eid":"E1","verdict":"unique","target":null,"canonical_name":"...","confidence":0.9,"reason":"..."}}]"""
    return prompt


# ---------------------------------------------------------------------------
# 存量再去重（reconcile）：合并已入库行中"同一作品不同表记"的分裂行
# ---------------------------------------------------------------------------

def _norm_s(s):
    return re.sub(r"[\s　]+", "", (s or "")).lower()


_RECONCILE_MERGE_COLS = [
    "company", "size", "link", "nyaa_name", "comment", "infohash_hex",
    "submitted_pick_code", "getchu_id", "release_date", "thumb_url",
    "thumb_path", "price", "detail_url", "raw_name",
]
_RECONCILE_MAX_COLS = ["downloaded", "submitted_115", "detail_fetched", "detail_retry"]


def _sim_ratio(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _reconcile_candidates(rows):
    """同公司内规范化名相似度高的行索引集合（候选池，减少AI token）。

    平台版行（Nintendo Switch2版/PlayStation 5版 等）是独立商品，不进候选池。
    """
    norm = [(i, _norm_s(r["name"])) for i, r in enumerate(rows)]
    cand = set()
    for (i, ni), (j, nj) in [(a, b) for ai, a in enumerate(norm) for b in norm[ai + 1:]]:
        if rows[i]["company"] != rows[j]["company"] or not ni or not nj:
            continue
        if has_platform_suffix(rows[i]["name"]) or has_platform_suffix(rows[j]["name"]):
            continue
        if ni == nj or _sim_ratio(ni, nj) >= 0.72:
            cand.add(i)
            cand.add(j)
    return sorted(cand)


_RECONCILE_PROMPT = """你是galgame数据库的数据清洗助手。以下是数据库中同一个月已入库的游戏行，其中一些可能是「同一作品」因表记差异被拆成多行。

【候选行】
%s

【任务】
1. merges：判定为【同一作品、仅表记差异】的行组。只允许以下情况：
   - 别名/表记差异（例：『～ with ○○』与『～ ○○』指向同一商品）
   - 全半角/假名/英字表记差异、多余空格
   - 误录的孤立后缀字（如末尾多出的一个"版"字）
   - 同一作品的特典BOX版与普通版（TREASURE BOX等）→ merges（canonical 取普通版行）
   - 注意：タペストリー絵柄のR18/全年齢違い、抱き枕カバー、SET内容違い等是【不同商品】，绝对不要放进 merges，归入 editions。
   - members 必须是2个以上 R 编号，canonical 必须是 members 之一（优先信息更全、命名更正式完整的行）。
   - 标题本体不同的（续作、别的作品、独立资料片）绝对不要合并。
2. editions：某行是另一行的版次/特典違い（TREASURE BOX/限定版/DL版/特典内容違い等）时单独输出，不合并，仅供人工复核。
3. 不确定就不要输出。

只输出JSON：
{"merges":[{"members":["R1","R2"],"canonical":"R1","confidence":0.95,"reason":"..."}],
 "editions":[{"row":"R3","of":"R1","confidence":0.6,"reason":"..."}]}"""


def reconcile_month(year, month, conn=None, config=None, use_ai=True, dry_run=True):
    """对已入库行做跨组再去重，返回合并计划；dry_run=False 时执行合并。"""
    config = config or read_config()
    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_getchu_schema(conn)
    ensure_dedup_schema(conn)
    delete_list = load_delete_list(config)

    month_key = f"{year}-{month:02d}"
    plan = {
        "year": year, "month": month, "dry_run": dry_run,
        "rows_total": 0, "candidates": 0, "ai_calls": 0,
        "merges": [], "editions": [], "errors": [],
        "executed": None,
    }

    try:
        rows = []
        for r in conn.execute(
            """
            SELECT getchu_name, getchu_company, date, name, link, downloaded,
                   submitted_115, getchu_id
            FROM getchu_games WHERE getchu_date=?
            """,
            (month_key,),
        ).fetchall():
            rows.append({
                # 匹配/展示用 getchu 登记身份（重标注不影响对账）
                "name": r[0], "company": r[1] or "",
                "cur_date": r[2], "cur_name": r[3],
                "has_link": bool(r[4]), "downloaded": int(r[5] or 0),
                "submitted_115": int(r[6] or 0), "getchu_id": r[7],
            })
        plan["rows_total"] = len(rows)
        if len(rows) < 2:
            if not dry_run:
                _record_reconcile_state(conn, month_key, 0, 0)
                conn.commit()
            return plan

        cand_idx = _reconcile_candidates(rows)
        plan["candidates"] = len(cand_idx)
        if len(cand_idx) < 2:
            if not dry_run:
                _record_reconcile_state(conn, month_key, 0, 0)
                conn.commit()
            return plan

        # 规则预合并：规范化名完全一致的行直接成组（不走AI）
        by_norm = {}
        for i in cand_idx:
            by_norm.setdefault((_norm_s(rows[i]["name"]), rows[i]["company"]), []).append(i)
        rule_groups = [v for v in by_norm.values() if len(v) > 1]
        assigned = {i for g in rule_groups for i in g}
        rest = [i for i in cand_idx if i not in assigned]

        merges = []
        for g in rule_groups:
            names = [rows[i]["name"] for i in g]
            canonical = _pick_reconcile_canonical(rows, g)
            merges.append({
                "members": names, "canonical": canonical,
                "confidence": 1.0, "reason": "规范化同名", "source": "rule",
            })

        if rest and use_ai:
            lines = []
            for k, i in enumerate(rest):
                r = rows[i]
                lines.append(
                    f"- R{k+1}: name={r['name']!r} company={r['company']!r}"
                    f" has_link={r['has_link']} downloaded={r['downloaded']}"
                    f" getchu_id={r['getchu_id'] or 'NULL'}"
                )
            prompt = _RECONCILE_PROMPT % "\n".join(lines)
            try:
                content, model = ai_chat([{"role": "user", "content": prompt}], config=config)
                plan["ai_calls"] += 1
                data = json.loads(_extract_json_array(content)[0]) if False else None
                # _extract_json_array 返回数组；这里期望对象，做兼容解析
                text = content.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                try:
                    obj = json.loads(text)
                except Exception:
                    m = re.search(r"\{.*\}", text, re.S)
                    obj = json.loads(m.group(0)) if m else None
                if not isinstance(obj, dict):
                    raise DedupAIError("reconcile AI 返回非对象")
                rmap = {f"R{k+1}": rows[i]["name"] for k, i in enumerate(rest)}
                for mg in obj.get("merges") or []:
                    members = [rmap.get(str(m).strip()) for m in (mg.get("members") or [])]
                    members = [m for m in members if m]
                    if len(members) < 2:
                        continue
                    canonical = rmap.get(str(mg.get("canonical") or "").strip())
                    if canonical not in members:
                        canonical = _pick_reconcile_canonical(
                            rows, [next(idx for idx in rest if rows[idx]["name"] == m) for m in members]
                        )
                    merges.append({
                        "members": members, "canonical": canonical,
                        "confidence": float(mg.get("confidence") or 0.5),
                        "reason": mg.get("reason") or "",
                        "source": "ai",
                    })
                for ed in obj.get("editions") or []:
                    row_name = rmap.get(str(ed.get("row") or "").strip())
                    of_name = rmap.get(str(ed.get("of") or "").strip())
                    if row_name and of_name and row_name != of_name:
                        plan["editions"].append({
                            "row": row_name, "of": of_name,
                            "confidence": float(ed.get("confidence") or 0.5),
                            "reason": ed.get("reason") or "",
                        })
            except Exception as e:  # noqa: BLE001
                plan["errors"].append(f"ai_failed: {e}")

        plan["merges"] = merges
        if dry_run:
            return plan
        _record_edition_suggestions(conn, month_key, plan["editions"])
        if not merges:
            _record_reconcile_state(conn, month_key, 0, len(plan["editions"]))
            conn.commit()
            return plan
        plan["executed"] = _execute_reconcile(conn, month_key, merges)
        merged_count = sum(
            len(e.get("merged") or []) for e in plan["executed"] if not e.get("error")
        )
        _record_reconcile_state(conn, month_key, merged_count, len(plan["editions"]))
        conn.commit()
        return plan
    finally:
        if own_conn:
            conn.close()


def _pick_reconcile_canonical(rows, idxs):
    """选规范行：有link > downloaded > getchu_id > 名字更长（表记更完整）。"""
    def rank(i):
        r = rows[i]
        return (
            1 if r["has_link"] else 0,
            r["downloaded"],
            1 if r["getchu_id"] else 0,
            len(r["name"]),
        )
    return rows[max(idxs, key=rank)]["name"]


def _month_rows_hash(conn, month_key):
    """行集合哈希（按 getchu 登记身份，重标注不改变哈希 → 幂等不被打破）"""
    rows = conn.execute(
        "SELECT getchu_company, getchu_name FROM getchu_games"
        " WHERE getchu_date=? ORDER BY getchu_name",
        (month_key,),
    ).fetchall()
    raw = "\n".join(f"{c or ''}|{n}" for c, n in rows)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _record_reconcile_state(conn, month_key, merged_count, edition_count):
    conn.execute(
        """
        INSERT INTO reconcile_state (date, rows_hash, merged_count, edition_count, done_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            rows_hash=excluded.rows_hash,
            merged_count=excluded.merged_count,
            edition_count=excluded.edition_count,
            done_at=excluded.done_at
        """,
        (month_key, _month_rows_hash(conn, month_key),
         int(merged_count or 0), int(edition_count or 0),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def _record_edition_suggestions(conn, month_key, editions):
    """版次建议落库（verdict=edition_of），按月替换避免重复膨胀。"""
    conn.execute(
        "DELETE FROM dedup_log WHERE date=? AND verdict='edition_of'",
        (month_key,),
    )
    for ed in editions or []:
        row_name = ed.get("row")
        of_name = ed.get("of")
        if not row_name or not of_name:
            continue
        comp = conn.execute(
            "SELECT getchu_company FROM getchu_games WHERE getchu_date=? AND getchu_name=?",
            (month_key, row_name),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO dedup_log
                (date, company, raw_name, base_name, verdict,
                 canonical_name, confidence, source, reason)
            VALUES (?, ?, ?, ?, 'edition_of', ?, ?, 'reconcile_ai', ?)
            """,
            (month_key, comp[0] if comp else None, row_name, row_name,
             of_name, float(ed.get("confidence") or 0.5), ed.get("reason") or ""),
        )


def _execute_reconcile(conn, month_key, merges):
    import sqlite3

    from .runtime import repo_root

    # ① 备份
    db_path = conn.execute("PRAGMA database_list").fetchall()
    db_file = next((r[2] for r in db_path if r[1] == "main"), None)
    backup_path = None
    if db_file and os.path.isfile(db_file):
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(repo_root(), "db_backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"getchu.db.before_reconcile.{ts}")
        src = sqlite3.connect(db_file)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()

    executed = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for mg in merges:
        canonical = mg["canonical"]
        merged = [m for m in mg["members"] if m != canonical]
        if not merged:
            continue
        try:
            # ② 读取规范行当前展示键（重标注后 date/name 可能 ≠ getchu 身份）
            can_row = conn.execute(
                "SELECT date, name FROM getchu_games WHERE getchu_date=? AND getchu_name=?",
                (month_key, canonical),
            ).fetchone()
            if not can_row:
                executed.append({"canonical": canonical, "merged": merged,
                                 "error": "canonical行不存在"})
                continue
            cdate, cname = can_row

            # ③ 读取被并行的完整数据（按getchu身份定位）
            placeholders = ",".join("?" for _ in merged)
            merged_rows = conn.execute(
                f"SELECT * FROM getchu_games WHERE getchu_date=? AND getchu_name IN ({placeholders})",
                (month_key, *merged),
            ).fetchall()
            cur = conn.execute("PRAGMA table_info(getchu_games)")
            col_names = [r[1] for r in cur.fetchall()]
            name_i = col_names.index("name")
            gname_i = col_names.index("getchu_name")
            merged_full = [dict(zip(col_names, r)) for r in merged_rows]
            # 实际命中的 getchu 名（防 record 缺失时 members 幽灵项）
            hit_names = {r[gname_i] for r in merged_rows}

            # ④ 合并数据到规范行（fill-if-null / MAX），用规范行当前展示键定位
            for col in _RECONCILE_MERGE_COLS:
                if col == "company":
                    continue
                ci = col_names.index(col) if col in col_names else None
                if ci is None:
                    continue
                for mr in merged_full:
                    if mr.get(col):
                        conn.execute(
                            f"UPDATE getchu_games SET {col}=? WHERE date=? AND name=?"
                            f" AND ({col} IS NULL OR {col}='')",
                            (mr[col], cdate, cname),
                        )
            for col in _RECONCILE_MAX_COLS:
                if col not in col_names:
                    continue
                for mr in merged_full:
                    conn.execute(
                        f"UPDATE getchu_games SET {col}=MAX(COALESCE({col},0), ?)"
                        f" WHERE date=? AND name=?",
                        (int(mr.get(col) or 0), cdate, cname),
                    )
            conn.execute(
                "UPDATE getchu_games SET dedup_source=?, dedup_reason=?, dedup_updated_at=?"
                " WHERE date=? AND name=?",
                ("reconcile_" + mg.get("source", "rule"),
                 mg.get("reason") or "", now_str, cdate, cname),
            )

            # ⑤ getchu_115_folders 引用迁移（成员当前展示键 → 规范行当前展示键）
            for mr in merged_full:
                m_date, m_name = mr.get("date"), mr.get("name")
                fr = conn.execute(
                    "SELECT cid, pid, pick_code, folder_name, folder_path, target_name,"
                    " date_code, company, status FROM getchu_115_folders WHERE date=? AND name=?",
                    (m_date, m_name),
                ).fetchone()
                if not fr:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM getchu_115_folders WHERE date=? AND name=?",
                    (cdate, cname),
                ).fetchone()
                if exists:
                    conn.execute(
                        """UPDATE getchu_115_folders SET
                            cid=COALESCE(cid,?), pid=COALESCE(pid,?),
                            pick_code=COALESCE(pick_code,?),
                            folder_name=COALESCE(folder_name,?),
                            folder_path=COALESCE(folder_path,?),
                            target_name=COALESCE(target_name,?),
                            date_code=COALESCE(date_code,?),
                            company=COALESCE(company,?),
                            status=CASE WHEN status IN ('already_ok','renamed') THEN status ELSE ? END
                           WHERE date=? AND name=?""",
                        (*fr, cdate, cname),
                    )
                    conn.execute(
                        "DELETE FROM getchu_115_folders WHERE date=? AND name=?",
                        (m_date, m_name),
                    )
                else:
                    conn.execute(
                        "UPDATE getchu_115_folders SET name=? WHERE date=? AND name=?",
                        (cname, m_date, m_name),
                    )

            # ⑥ dedup_cache 指向修正（target 语义 = getchu 登记名，不变）
            for m in merged:
                if m not in hit_names:
                    continue
                conn.execute(
                    "UPDATE dedup_cache SET target_name=?, updated_at=? WHERE target_name=?",
                    (canonical, now_str, m),
                )
                conn.execute(
                    """UPDATE dedup_cache SET verdict='dup', canonical_name=NULL,
                        target_name=?, updated_at=?
                       WHERE company=(SELECT getchu_company FROM getchu_games
                                      WHERE getchu_date=? AND getchu_name=?)
                         AND base_name=?""",
                    (canonical, now_str, month_key, canonical, m),
                )

            # ⑦ 归档被并行完整数据（可回溯）→ 删除（按当前展示键）→ 审计
            for mr in merged_full:
                conn.execute(
                    """
                    INSERT INTO reconcile_archive (date, name, row_json, merged_into)
                    VALUES (?, ?, ?, ?)
                    """,
                    (mr.get("date"), mr.get("name"),
                     json.dumps(mr, ensure_ascii=False), canonical),
                )
            for mr in merged_full:
                conn.execute(
                    "DELETE FROM getchu_games WHERE date=? AND name=?",
                    (mr.get("date"), mr.get("name")),
                )
                conn.execute(
                    """
                    INSERT INTO dedup_log
                        (date, company, raw_name, base_name, verdict,
                         canonical_name, confidence, source, reason)
                    VALUES (?, ?, ?, ?, 'merged', ?, ?, ?, ?)
                    """,
                    (month_key, mg.get("source"), mr.get("getchu_name"),
                     mr.get("getchu_name"), canonical,
                     mg.get("confidence"), "reconcile_" + mg.get("source", "rule"),
                     mg.get("reason") or ""),
                )
            executed.append({
                "canonical": canonical, "merged": merged,
                "backup": backup_path,
            })
        except Exception as e:  # noqa: BLE001
            executed.append({"canonical": canonical, "merged": merged, "error": str(e)})
    conn.commit()
    return executed



def dedup_month(year, month, conn=None, config=None, use_ai=True, reconcile=True):
    """对指定月份执行 爬取→规则分组→AI去重→入库，返回统计。

    reconcile=True 时在入库后对存量行做跨组再去重（幂等：按月行集合哈希，
    行集合未变化则跳过）。
    """
    config = config or read_config()
    own_conn = conn is None
    if own_conn:
        conn = open_db()
        ensure_getchu_schema(conn)
    ensure_dedup_schema(conn)
    delete_list = load_delete_list(config)

    stats = {
        "year": year,
        "month": month,
        "raw_fetched": 0,
        "groups": 0,
        "anchor_groups": 0,
        "ai_calls": 0,
        "cache_hits": 0,
        "inserted": 0,
        "dup_logged": 0,
        "errors": [],
        "inserted_names": [],
        "dup_examples": [],
    }

    try:
        # 1. 抓原始清单（skip 列表仍生效）
        try:
            raw_games = get_raw_getchu_games(year, month)
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"crawl_failed: {e}")
            return stats
        stats["raw_fetched"] = len(raw_games)
        if not raw_games:
            return stats

        month_key = f"{year}-{month:02d}"

        # 2. 规则分组（同月同公司才归组；公司为空的行绝不与有公司的合并）
        groups = {}
        for g in raw_games:
            base = rule_base_name(g.name, delete_list)
            comp = g.company or ""
            key = (comp if comp else "\x00独立(无公司)", base)
            if key not in groups:
                groups[key] = {"company": comp, "base_name": base, "members": []}
            groups[key]["members"].append(g.name)
        stats["groups"] = len(groups)

        # 3. 已入库锚点（按 getchu 登记身份；重标注行 date/name 已变，不影响锚定）
        rows = conn.execute(
            "SELECT getchu_name, getchu_company FROM getchu_games WHERE getchu_date LIKE ?",
            (f"{month_key}%",),
        ).fetchall()
        anchor_names = {(r[0] or "", r[1] or "") for r in rows}
        # 锚点索引（规范化匹配用）
        anchor_by_norm = {_norm_s(n): (n, c) for n, c in anchor_names}

        # 4. 缓存解析 + 收集待 AI 组
        resolved = []   # (group_key, cache_row_like_dict, source)
        pending = []
        for (company, base), g in sorted(groups.items()):
            if all(has_platform_suffix(m) for m in g["members"]):
                # 全平台版组（Switch2版/PS5版等）→ 合并为本体：canonical=剥平台后的base
                # 跳过AI（确定性规则），如進撃の巨人3 Switch2版/PS5版 → 進撃の巨人3
                resolved.append({
                    "company": company, "base_name": base, "members": g["members"],
                    "verdict": "unique", "canonical_name": base,
                    "target_company": None, "target_name": None,
                    "rep_raw": min(g["members"], key=rule_rep_rank),
                    "confidence": 1.0,
                    "reason": "平台版组合并为本体", "model": "rule_platform",
                    "source": "rule_platform",
                })
                stats["platform_groups"] = stats.get("platform_groups", 0) + 1
                continue
            ck = _cache_key(company, base)
            cached = conn.execute(
                "SELECT verdict, canonical_name, target_company, target_name, rep_raw,"
                " confidence, reason, model FROM dedup_cache WHERE cache_key=?",
                (ck,),
            ).fetchone()
            if cached:
                stats["cache_hits"] += 1
                resolved.append({
                    "company": company, "base_name": base, "members": g["members"],
                    "verdict": cached[0], "canonical_name": cached[1],
                    "target_company": cached[2], "target_name": cached[3],
                    "rep_raw": cached[4], "confidence": cached[5],
                    "reason": cached[6], "model": cached[7], "source": "cache",
                })
            else:
                pending.append({"company": company, "base_name": base, "members": g["members"]})

        # 5. 规则锚点直配：base 与锚点完全一致的组直接判 dup（免 AI）
        still_pending = []
        for p in pending:
            norm_base = _norm_s(p["base_name"])
            if norm_base in anchor_by_norm:
                an, ac = anchor_by_norm[norm_base]
                resolved.append({
                    "company": p["company"], "base_name": p["base_name"], "members": p["members"],
                    "verdict": "dup", "canonical_name": None,
                    "target_company": ac, "target_name": an,
                    "rep_raw": None, "confidence": 1.0,
                    "reason": "与已入库作品同名", "model": None, "source": "rule",
                })
                stats["anchor_groups"] += 1
            else:
                still_pending.append(p)
        pending = still_pending

        # 6. AI 归组
        ai_results = {}
        eid_groups = []
        anchors = [
            {"aid": f"A{i+1}", "company": c, "name": n}
            for i, (n, c) in enumerate(sorted(anchor_names))
        ]
        if pending and use_ai:
            for i, p in enumerate(pending):
                eid_groups.append({
                    "eid": f"E{i+1}", "company": p["company"],
                    "base_name": p["base_name"], "members": p["members"],
                })
            prompt = _build_ai_prompt(eid_groups, anchors)
            try:
                content, model = ai_chat(
                    [{"role": "user", "content": prompt}], config=config
                )
                stats["ai_calls"] += 1
                arr = _extract_json_array(content)
                by_eid = {}
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    eid = item.get("eid")
                    if eid in {g["eid"] for g in eid_groups}:
                        by_eid[eid] = item
                for g in eid_groups:
                    item = by_eid.get(g["eid"]) or {}
                    verdict = item.get("verdict") or "unique"
                    if verdict not in ("unique", "dup"):
                        verdict = "unique"
                    ai_results[g["eid"]] = {
                        "company": g["company"], "base_name": g["base_name"],
                        "members": g["members"], "verdict": verdict,
                        "target": item.get("target"),
                        "canonical_name": item.get("canonical_name") or g["base_name"],
                        "confidence": float(item.get("confidence") or 0.5),
                        "reason": item.get("reason") or "",
                        "model": model, "source": "ai",
                    }
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"ai_failed: {e}")
                # 兜底：全部 unique
                for g in eid_groups:
                    ai_results[g["eid"]] = {
                        "company": g["company"], "base_name": g["base_name"],
                        "members": g["members"], "verdict": "unique",
                        "target": None, "canonical_name": g["base_name"],
                        "confidence": 0.3, "reason": "AI失败按规则兜底",
                        "model": None, "source": "rule_fallback",
                    }
        elif pending:
            # --no-ai 模式：全部 unique
            for i, p in enumerate(pending):
                ai_results[f"E{i+1}"] = {
                    "company": p["company"], "base_name": p["base_name"],
                    "members": p["members"], "verdict": "unique",
                    "target": None, "canonical_name": p["base_name"],
                    "confidence": 0.5, "reason": "no_ai_mode",
                    "model": None, "source": "rule",
                }

        # 7. 解析 E 间 dup 引用：E→E 指向的代表组 canonical
        eid_canonical = {}
        for eid, r in ai_results.items():
            if r["verdict"] == "unique":
                eid_canonical[eid] = r["canonical_name"]

        def _resolve_target(r):
            if r["verdict"] != "dup" or not r.get("target"):
                return None
            t = str(r["target"]).strip()
            m = re.fullmatch(r"[Ee]\s*(\d+)", t)
            if m:
                teid = f"E{m.group(1)}"
                if teid in eid_canonical:
                    return ai_results[teid]["company"], eid_canonical[teid]
                return None
            m = re.fullmatch(r"[Aa]\s*(\d+)", t)
            if m:
                aid = f"A{m.group(1)}"
                for a in anchors:
                    if a["aid"] == aid:
                        return a["company"], a["name"]
                return None
            # AI 直接给了名字
            norm_t = _norm_s(t)
            if norm_t in anchor_by_norm:
                return anchor_by_norm[norm_t]
            return None

        # 8. 应用结果：入库 unique / 记 dup / 写缓存 / 写日志
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in list(resolved) + list(ai_results.values()):
            company = r["company"]
            base = r["base_name"]
            members = r["members"]
            rep_raw = r.get("rep_raw") or min(members, key=rule_rep_rank)
            verdict = r["verdict"]
            conf = float(r.get("confidence") or 0)
            reason = r.get("reason") or ""
            source = r.get("source")

            if verdict == "unique":
                canonical = (r.get("canonical_name") or base).strip() or base
                # 防御：canonical 已存在（AI 与锚点撞名）→ 视为 dup（按getchu身份查）
                exist = conn.execute(
                    "SELECT getchu_name FROM getchu_games WHERE getchu_date=? AND getchu_name=?",
                    (month_key, canonical),
                ).fetchone()
                if exist:
                    verdict, canonical = "dup", None
                    r["target_company"], r["target_name"] = company, exist[0]
                    r["reason"] = (reason + " | canonical与已入库同名").strip(" |")
                    reason = r["reason"]

            # 先解析 dup 目标名（AI结果用 target 字段，需经 _resolve_target）
            tc2 = r.get("target_company") if verdict == "dup" else None
            tn2 = r.get("target_name") if verdict == "dup" else None
            if verdict == "dup" and not tn2:
                t = _resolve_target(r)
                if t:
                    tc2, tn2 = t

            if verdict == "dup" and tn2:
                # dup 目标存在性校验（cache重放目标可能已被删/合并）→ 不存在则降级 unique
                tgt_exist = conn.execute(
                    "SELECT 1 FROM getchu_games WHERE getchu_date=? AND getchu_name=?",
                    (month_key, tn2),
                ).fetchone()
                if not tgt_exist:
                    verdict = "unique"
                    canonical = base
                    tn2 = tc2 = None
                    reason = (reason + " | dup目标已不存在,降级unique").strip(" |")
            if verdict == "dup" and tc2 is not None and (tc2 or "") != (company or ""):
                # 同月同公司约束: dup 目标公司与成员不一致 → 拒绝合并，降级 unique
                verdict = "unique"
                tn2 = tc2 = None
                r["target"] = None
                reason = (reason + " | dup被拒:目标公司不同").strip(" |")
            if verdict == "dup":
                if tn2:
                    stats["dup_logged"] += 1
                    if len(stats["dup_examples"]) < 10:
                        stats["dup_examples"].append({
                            "raw": members[0] if members else base,
                            "target": tn2,
                        })
                    # cache 重放不重复记日志，只记首次判定
                    if source != "cache":
                        for m_name in members:
                            conn.execute(
                                """
                                INSERT INTO dedup_log
                                    (date, company, raw_name, base_name, verdict,
                                     canonical_name, confidence, source, reason)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (month_key, company, m_name, base, "dup",
                                 tn2, conf, source, reason),
                            )
                else:
                    # 目标不可解析 → 降级为 unique 入库
                    canonical = base
                    exist = conn.execute(
                        "SELECT 1 FROM getchu_games WHERE getchu_date=? AND getchu_name=?",
                        (month_key, canonical),
                    ).fetchone()
                    if not exist:
                        conn.execute(
                            """
                            INSERT INTO getchu_games
                                (date, name, company, getchu_date, getchu_name,
                                 getchu_company, raw_name, dedup_source,
                                 dedup_confidence, dedup_reason, dedup_updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (month_key, canonical, company, month_key, canonical,
                             company, rep_raw, source or "rule",
                             conf, reason or "dup目标不可解析", now_str),
                        )
                        stats["inserted"] += 1
                        stats["inserted_names"].append(canonical)
                    verdict = "unique"
                    tn2, tc2 = None, None

            if verdict == "unique":
                canonical = (r.get("canonical_name") or base).strip() or base
                # 防御：canonical 已存在（AI 与锚点撞名）→ 视为 dup（按getchu身份查）
                exist = conn.execute(
                    "SELECT 1 FROM getchu_games WHERE getchu_date=? AND getchu_name=?",
                    (month_key, canonical),
                ).fetchone()
                if not exist:
                    conn.execute(
                        """
                        INSERT INTO getchu_games
                            (date, name, company, getchu_date, getchu_name,
                             getchu_company, raw_name, dedup_source,
                             dedup_confidence, dedup_reason, dedup_updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (month_key, canonical, company, month_key, canonical,
                         company, rep_raw, source,
                         conf, reason, now_str),
                    )
                    stats["inserted"] += 1
                    stats["inserted_names"].append(canonical)
                    if source != "cache":
                        for m_name in members:
                            conn.execute(
                                """
                                INSERT INTO dedup_log
                                    (date, company, raw_name, base_name, verdict,
                                     canonical_name, confidence, source, reason)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (month_key, company, m_name, base, "unique",
                                 canonical, conf, source, reason),
                            )

            # 写缓存（幂等：值未变化则跳过，避免重跑产生写入）
            ck = _cache_key(company, base)
            new_vals = (
                verdict,
                canonical if verdict == "unique" else None,
                tc2, tn2, rep_raw, conf, reason, r.get("model"),
            )
            existing = conn.execute(
                """
                SELECT verdict, canonical_name, target_company, target_name,
                       rep_raw, confidence, reason, model
                FROM dedup_cache WHERE cache_key=?
                """,
                (ck,),
            ).fetchone()
            unchanged = False
            if existing:
                unchanged = (
                    existing[0] == new_vals[0]
                    and existing[1] == new_vals[1]
                    and (existing[2] or None) == (new_vals[2] or None)
                    and (existing[3] or None) == (new_vals[3] or None)
                    and (existing[4] or None) == (new_vals[4] or None)
                    and abs(float(existing[5] or 0) - float(new_vals[5] or 0)) < 1e-9
                    and (existing[6] or None) == (new_vals[6] or None)
                    and (existing[7] or None) == (new_vals[7] or None)
                )
            if not unchanged:
                conn.execute(
                """
                INSERT INTO dedup_cache
                    (cache_key, company, base_name, verdict, canonical_name,
                     target_company, target_name, rep_raw, confidence, reason,
                     model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    verdict=excluded.verdict,
                    canonical_name=excluded.canonical_name,
                    target_company=excluded.target_company,
                    target_name=excluded.target_name,
                    rep_raw=excluded.rep_raw,
                    confidence=excluded.confidence,
                    reason=excluded.reason,
                    model=excluded.model,
                    updated_at=excluded.updated_at
                """,
                (ck, company, base, verdict,
                 canonical if verdict == "unique" else None,
                 tc2, tn2, rep_raw, conf, reason, r.get("model"), now_str),
            )

        conn.commit()

        # 9. 存量行再去重（幂等：行集合哈希未变化则跳过）
        if reconcile:
            try:
                h = _month_rows_hash(conn, month_key)
                st = conn.execute(
                    "SELECT rows_hash FROM reconcile_state WHERE date=?",
                    (month_key,),
                ).fetchone()
                if st and st[0] == h:
                    stats["reconcile"] = {"skipped": True, "reason": "rows_unchanged"}
                else:
                    plan = reconcile_month(
                        year, month, conn=conn, config=config,
                        use_ai=use_ai, dry_run=False,
                    )
                    stats["reconcile"] = {
                        "skipped": False,
                        "rows_total": plan.get("rows_total"),
                        "candidates": plan.get("candidates"),
                        "ai_calls": int(plan.get("ai_calls") or 0),
                        "merges": plan.get("merges") or [],
                        "executed": plan.get("executed") or [],
                        "editions": plan.get("editions") or [],
                        "errors": plan.get("errors") or [],
                    }
            except Exception as e:  # noqa: BLE001
                stats["reconcile"] = {"error": str(e)}

        return stats
    finally:
        if own_conn:
            conn.close()
