import json
import logging
import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .ai_matcher import PROTECTED_PUBLISHERS, judge_nyaa_match
from .models import GetchuGame, NyaaData
from .runtime import read_config, runtime_paths


logger = logging.getLogger(__name__)


def clear_link(nyaa_data):
    nyaa_data.link = None
    return nyaa_data


def normalize_name(name, delete_list):
    if not name:
        return ""
    for del_str in delete_list:
        if del_str and del_str in name:
            name = name.replace(del_str, " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def get_raw_getchu_games(year, month):
    config = read_config()
    skip_list = config.get("skip", [])

    cookies = {"getchu_adalt_flag": "getchu.com"}
    url = f"https://www.getchu.com/all/price.html?genre=pc_soft&year={year}&month={month}"
    try:
        response = requests.get(url, cookies=cookies)
        response.encoding = 'euc-jp'  # Getchu页面使用EUC-JP编码
        response.raise_for_status()
    except Exception as e:
        logger.error("获取%s年%s月数据时出错: %s", year, month, str(e))
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    if not soup:
        logger.error("解析%s年%s月HTML内容失败", year, month)
        return []

    game_rows = soup.find_all("tr", bgcolor="#ffffff")
    if not game_rows:
        logger.warning("%s年%s月没有找到游戏数据", year, month)
        return []

    raw_games = []
    for row in game_rows:
        columns = row.find_all("td")
        if len(columns) >= 3:
            # 解析精确发售日（col0锚点MM/DD）
            day_text = columns[0].get_text(strip=True)
            if not day_text or day_text == "/":
                continue
            
            # 处理日期格式：col0通常是 "08/28" 格式
            month_key = f"{year}-{month:02d}"  # 主键日期统一用 YYYY-MM（与历史数据兼容）
            if "/" in day_text:
                # 提取天数部分，避免混合格式
                day_part = day_text.split("/")[-1]  # 取最后的部分
                if len(day_part) == 2 and day_part.isdigit():
                    release_date = f"{month_key}-{day_part}"
                else:
                    # 如果格式不正确，使用原始格式（降级）
                    release_date = f"{month_key}-{day_text}"
            else:
                release_date = f"{month_key}-{day_text}"
            
            # 解析游戏标题和getchu_id（col1链接）
            a_tag = columns[1].find('a', href=re.compile(r'soft\.phtml\?id=(\d+)'))
            if not a_tag:
                continue
            name = a_tag.text.strip()
            match = re.search(r'id=(\d+)', a_tag['href'])
            getchu_id = match.group(1) if match else None
            
            # 制作公司（col2）
            company = columns[2].text.strip()
            
            # 媒体类型（col3）
            media = columns[3].text.strip()
            
            # 价格（col4）
            price = columns[4].get_text(" ", strip=True)
            
            # 详情页URL
            detail_url = f"https://www.getchu.com/soft.phtml?id={getchu_id}" if getchu_id else None
            
            # 构建缩略图URL（Getchu缩略图格式固定：/brandnew/{gid}/rc{gid}package.jpg）
            thumb_url = f"https://www.getchu.com/brandnew/{getchu_id}/rc{getchu_id}package.jpg" if getchu_id else None
            
            if company and name and getchu_id and not any(skip_str in name for skip_str in skip_list):
                raw_games.append(GetchuGame(
                    date=month_key,
                    name=name,
                    company=company,
                    size=media,  # 复用size字段存储媒体类型
                    extra={
                        "getchu_id": getchu_id, 
                        "price": price, 
                        "detail_url": detail_url,
                        "thumb_url": thumb_url,  # 缩略图URL
                        "release_date": release_date  # 精确发售日
                    }
                ))

    return raw_games


def deduplicate_games(raw_games):
    config = read_config()
    combined_list = sorted(config.get("delete", []), key=len, reverse=True)

    for game in raw_games:
        for del_str in combined_list:
            if del_str in game.name:
                game.name = game.name.replace(del_str, "").strip()

    raw_games.sort(key=lambda x: (x.company, len(x.name)))

    processed_games = []
    processed_keys = set()

    for game in raw_games:
        key = normalize_name(game.name, combined_list)
        # 补丁对应版（if エロパッチ対応...）视为同一游戏，剥离后缀生成去重键
        dedup_key = re.sub(r"\s*if\s+エロパッチ対応.*$", "", key).strip()
        stripped_key = dedup_key.rsplit(" ", 1)[0] if " " in dedup_key else dedup_key
        
        # 新增：如果有getchu_id，优先用getchu_id去重（防止不同月同名版本冲突）
        if hasattr(game, 'extra') and game.extra and game.extra.get('getchu_id'):
            gid_key = f"gid_{game.extra['getchu_id']}"
            if gid_key in processed_keys:
                continue
            processed_games.append(game)
            processed_keys.add(gid_key)
        else:
            # 旧逻辑：公司名+名称去重（补丁后缀剥离后匹配）
            if key in processed_keys or dedup_key in processed_keys or stripped_key in processed_keys:
                continue
            processed_games.append(game)
            processed_keys.add(key)
            processed_keys.add(dedup_key)

    processed_games.sort(key=lambda x: (x.date, x.name))
    return processed_games


def get_getchu_games(year, month):
    raw_games = get_raw_getchu_games(year, month)
    return deduplicate_games(raw_games)


def get_db_path(default=None):
    paths = runtime_paths()
    return paths["db_path"] if paths.get("db_path") else default


def open_db(db_path=None, timeout_s=30):
    conn = sqlite3.connect(db_path or get_db_path(), timeout=timeout_s)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_getchu_schema(conn):
    cursor = conn.cursor()
    changed = False
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS getchu_games (
            date TEXT,
            name TEXT,
            company TEXT,
            size TEXT,
            link TEXT,
            nyaa_name TEXT,
            comment TEXT,
            downloaded INTEGER DEFAULT 0,
            infohash_hex TEXT,
            submitted_115 INTEGER DEFAULT 0,
            submitted_pick_code TEXT,
            PRIMARY KEY (date, name)
        )
        """
    )
    cursor.execute("PRAGMA table_info(getchu_games)")
    cols = {row[1] for row in cursor.fetchall()}
    if "nyaa_name" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN nyaa_name TEXT")
        changed = True
    if "downloaded" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN downloaded INTEGER DEFAULT 0")
        changed = True
    if "infohash_hex" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN infohash_hex TEXT")
        changed = True
    if "submitted_115" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN submitted_115 INTEGER DEFAULT 0")
        changed = True
    if "submitted_pick_code" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN submitted_pick_code TEXT")
        changed = True

    cursor.execute("PRAGMA user_version")
    user_version = int(cursor.fetchone()[0] or 0)
    if user_version < 1:
        cursor.execute(
            "UPDATE getchu_games SET submitted_115 = 1 WHERE COALESCE(downloaded, 0) = 1 AND COALESCE(submitted_115, 0) = 0"
        )
        cursor.execute("PRAGMA user_version = 1")
        changed = True
    if user_version < 2:
        cursor.execute(
            "UPDATE getchu_games SET submitted_115 = 1 WHERE COALESCE(downloaded, 0) = 1 AND COALESCE(submitted_115, 0) = 0"
        )
        cursor.execute("PRAGMA user_version = 2")
        changed = True

    if changed:
        conn.commit()

    ensure_match_schema(conn)


def ensure_match_schema(conn):
    """Create AI matching judgement and keyword-memory tables."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS match_judgements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            game_name TEXT NOT NULL,
            company TEXT,
            verdict TEXT NOT NULL,
            confidence REAL DEFAULT 0,
            source TEXT DEFAULT 'ai',
            need_retry INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(date, game_name)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS match_keyword_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            source TEXT DEFAULT 'ai',
            confidence REAL DEFAULT 0.5,
            hit_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(keyword, rule_type)
        )
        """
    )
    conn.commit()


def save_match_judgement(conn, result):
    """Insert or update a single match judgement row."""
    ensure_match_schema(conn)
    cursor = conn.cursor()
    verdict = getattr(result, "verdict", None) or "review"
    confidence = float(getattr(result, "confidence", 0.0) or 0.0)
    source = getattr(result, "source", None) or "ai"
    need_retry = 1 if verdict == "review" or confidence < 0.4 else 0
    cursor.execute(
        """
        INSERT INTO match_judgements (date, game_name, company, verdict, confidence, source, need_retry, retry_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(date, game_name) DO UPDATE SET
            verdict = excluded.verdict,
            confidence = excluded.confidence,
            source = excluded.source,
            need_retry = excluded.need_retry,
            retry_count = match_judgements.retry_count + 1
        """,
        (
            getattr(result, "date", None),
            getattr(result, "name", None),
            getattr(result, "company", None),
            verdict,
            confidence,
            source,
            need_retry,
        ),
    )
    conn.commit()


def save_match_keyword_rules(conn, keywords):
    """Insert or update AI-extracted keyword memory rules."""
    ensure_match_schema(conn)
    cursor = conn.cursor()
    for item in keywords or []:
        keyword = str(item.get("keyword") or "").strip()
        rule_type = str(item.get("rule_type") or "review").strip().lower()
        if not keyword or rule_type not in {"include", "discard", "duplicate", "review"}:
            continue
        # girlcelly / 2D.G.F. 是发布来源标记，不能作为排除要素
        if keyword.lower() in PROTECTED_PUBLISHERS and rule_type != "include":
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        cursor.execute(
            """
            INSERT INTO match_keyword_rules (keyword, rule_type, source, confidence, hit_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(keyword, rule_type) DO UPDATE SET
                source = excluded.source,
                confidence = excluded.confidence,
                hit_count = match_keyword_rules.hit_count + 1,
                updated_at = datetime('now', 'localtime')
            """,
            (keyword, rule_type, "ai", confidence),
        )
    conn.commit()


def load_match_keyword_rules(conn=None):
    """Return all memory keyword rules as dicts."""
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        ensure_match_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT keyword, rule_type, source, confidence, hit_count
            FROM match_keyword_rules
            ORDER BY hit_count DESC, id ASC
            """
        )
        rows = [
            {
                "keyword": row[0],
                "rule_type": row[1],
                "source": row[2],
                "confidence": row[3],
                "hit_count": row[4],
            }
            for row in cursor.fetchall()
        ]
        return rows
    finally:
        if own_conn:
            conn.close()


def load_match_judgement(conn, date, game_name):
    """Return one judgement row or None."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT verdict, confidence, source, need_retry, retry_count
        FROM match_judgements
        WHERE date = ? AND game_name = ?
        """,
        (date, game_name),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "verdict": row[0],
        "confidence": row[1],
        "source": row[2],
        "need_retry": row[3],
        "retry_count": row[4],
    }


def set_downloaded_status(date, name, downloaded=1, infohash_hex=None, db_path=None):
    conn = open_db(db_path=db_path)
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    if infohash_hex:
        if downloaded:
            cursor.execute(
                "UPDATE getchu_games SET downloaded = ?, infohash_hex = ?, submitted_115 = 1 WHERE date = ? AND name = ?",
                (downloaded, infohash_hex, date, name),
            )
        else:
            cursor.execute(
                "UPDATE getchu_games SET downloaded = ?, infohash_hex = ? WHERE date = ? AND name = ?",
                (downloaded, infohash_hex, date, name),
            )
    else:
        if downloaded:
            cursor.execute(
                "UPDATE getchu_games SET downloaded = ?, submitted_115 = 1 WHERE date = ? AND name = ?",
                (downloaded, date, name),
            )
        else:
            cursor.execute(
                "UPDATE getchu_games SET downloaded = ? WHERE date = ? AND name = ?",
                (downloaded, date, name),
            )
    conn.commit()
    conn.close()


def set_submitted_status(date, name, submitted_115=1, submitted_pick_code=None, db_path=None):
    conn = open_db(db_path=db_path)
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    if submitted_pick_code is not None:
        cursor.execute(
            "UPDATE getchu_games SET submitted_115 = ?, submitted_pick_code = ? WHERE date = ? AND name = ?",
            (1 if submitted_115 else 0, submitted_pick_code, date, name),
        )
    else:
        cursor.execute(
            "UPDATE getchu_games SET submitted_115 = ? WHERE date = ? AND name = ?",
            (1 if submitted_115 else 0, date, name),
        )
    conn.commit()
    conn.close()


def update_game_record(date, name, new_date=None, new_name=None, new_company=None, new_link=None, new_downloaded=None, new_nyaa_name=None, new_submitted_115=None, new_submitted_pick_code=None, db_path=None):
    conn = open_db(db_path=db_path)
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    fields = {}
    if new_date is not None and new_date != date:
        fields["date"] = new_date
    if new_name is not None:
        fields["name"] = new_name
    if new_company is not None:
        fields["company"] = new_company
    if new_link is not None:
        fields["link"] = new_link
    if new_downloaded is not None:
        fields["downloaded"] = 1 if new_downloaded else 0
        if new_downloaded and new_submitted_115 is None:
            fields["submitted_115"] = 1
    if new_nyaa_name is not None:
        fields["nyaa_name"] = new_nyaa_name
    if new_submitted_115 is not None:
        fields["submitted_115"] = 1 if new_submitted_115 else 0
    if new_submitted_pick_code is not None:
        fields["submitted_pick_code"] = new_submitted_pick_code
    if not fields:
        conn.close()
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [date, name]
    cursor.execute(f"UPDATE getchu_games SET {sets} WHERE date = ? AND name = ?", values)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_game_record(date, name, db_path=None):
    conn = open_db(db_path=db_path)
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM getchu_games WHERE date = ? AND name = ?", (date, name))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def _build_update_sql(extra):
    """根据 extra 构建 UPDATE 的 set 子句和参数"""
    set_clause = []
    params = []
    if extra.get("getchu_id"):
        set_clause.append("getchu_id = ?")
        params.append(extra["getchu_id"])
    if extra.get("price"):
        set_clause.append("price = ?")
        params.append(extra["price"])
    if extra.get("detail_url"):
        set_clause.append("detail_url = ?")
        params.append(extra["detail_url"])
    if extra.get("thumb_url"):
        set_clause.append("thumb_url = ?")
        params.append(extra["thumb_url"])
    if extra.get("release_date"):
        set_clause.append("release_date = ?")
        params.append(extra["release_date"])
    return set_clause, params


def upsert_getchu_game(cursor, game):
    """
    插入或更新单个游戏记录（保留115下载相关数据）

    - 优先按 getchu_id 匹配：名称不一致（历史去重规则改写）也能正确合并到已有记录
    - INSERT OR IGNORE 基础字段（date+name 为主键）
    - 再更新扩展字段：getchu_id / price / detail_url / thumb_url / release_date
    - 匹配策略：gid > date+name+company > date+name
    - 永不触碰 link / nyaa_name / downloaded / submitted_115 等下载字段

    Returns:
        bool: 是否新插入了记录
    """
    extra = getattr(game, "extra", None) or {}
    gid = extra.get("getchu_id")

    # 1) 已有同 gid 记录：直接按 rowid 更新，避免名称不一致产生重复
    if gid:
        cursor.execute("SELECT rowid FROM getchu_games WHERE getchu_id = ? LIMIT 1", (gid,))
        r = cursor.fetchone()
        if r:
            set_clause, params = _build_update_sql(extra)
            if set_clause:
                cursor.execute(
                    f"UPDATE getchu_games SET {', '.join(set_clause)} WHERE rowid = ?",
                    tuple(params) + (r[0],),
                )
            return False

    # 2) INSERT OR IGNORE 基础字段（date+name 为主键）
    cursor.execute(
        "INSERT OR IGNORE INTO getchu_games (date, name, company, size) VALUES (?,?,?,?)",
        (game.date, game.name, game.company, game.size),
    )
    inserted = cursor.rowcount == 1

    # 3) 更新扩展字段（无 gid 或新插入时走名称匹配）
    set_clause, params = _build_update_sql(extra)
    if not set_clause:
        return inserted

    # 优先精确匹配，失败则忽略公司名差异降级匹配
    cursor.execute(
        f"UPDATE getchu_games SET {', '.join(set_clause)} WHERE date = ? AND name = ? AND company = ?",
        tuple(params) + (game.date, game.name, game.company),
    )
    if cursor.rowcount == 0:
        cursor.execute(
            f"UPDATE getchu_games SET {', '.join(set_clause)} WHERE date = ? AND name = ?",
            tuple(params) + (game.date, game.name),
        )
    return inserted


def get_all_getchu_games(start_year, end_year, start_month, end_month, db_path=None):
    logger.info("开始获取%s年%s月至%s年%s月的数据", start_year, start_month, end_year, end_month)
    conn = None
    try:
        conn = open_db(db_path=db_path)
        ensure_getchu_schema(conn)
        cursor = conn.cursor()
        success_count = 0
        error_months = 0
        for year in range(start_year, end_year + 1):
            for month in range(start_month, end_month + 1):
                logger.info("正在处理%s年%s月的数据", year, month)
                try:
                    games = get_getchu_games(year, month)
                except Exception as e:
                    # 单月网络/解析失败不中断整体
                    logger.error("%s年%s月获取失败: %s", year, month, str(e))
                    error_months += 1
                    continue
                if not games:
                    logger.warning("%s年%s月没有获取到数据", year, month)
                    continue
                for game in games:
                    # 统一入库：INSERT OR IGNORE + 扩展字段更新（含公司名降级匹配）
                    upsert_getchu_game(cursor, game)
                success_count += len(games)
                conn.commit()  # 每月提交，避免单点异常丢失全部进度
                logger.info("完成%s年%s月的数据处理，共处理%s个游戏", year, month, len(games))
        if error_months:
            logger.warning("共%d个月获取失败（已跳过）", error_months)
        return success_count > 0
    except Exception as e:
        logger.error("get_all_getchu_games,数据库操作失败: %s", str(e))
        return False
    finally:
        if conn is not None:
            conn.close()


def get_nyaa_data(game_name, company):
    game_name = re.sub(r"[-]", "", game_name)
    try:
        response = requests.get(f"https://sukebei.nyaa.si/?f=0&c=1_3&q={game_name}+{company}")
        response.raise_for_status()
    except (requests.exceptions.ConnectTimeout, requests.exceptions.RetryError) as e:
        logger.error("获取游戏 %s 数据时连接超时或重试次数过多: %s", game_name, str(e))
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.find_all("tr")
    if not rows:
        keyword = re.sub(r"[^\w\s]", "", game_name)
        keyword2 = re.sub(r"[-]", " ", company)
        try:
            response = requests.get(f"https://sukebei.nyaa.si/?f=0&c=1_3&q={keyword}+{keyword2}")
            response.raise_for_status()
        except (requests.exceptions.ConnectTimeout, requests.exceptions.RetryError) as e:
            logger.error("使用关键词 %s 获取游戏 %s 数据时连接超时或重试次数过多: %s", keyword, game_name, str(e))
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.find_all("tr")

    nyaa_data_list = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 5:
            link_views = cells[1].select('a[href*="view"]')
            name_element = link_views[-1] if len(link_views) > 0 else None
            name = name_element.attrs["title"] if name_element else cells[1].get_text(strip=True)

            link_element = next(
                (a for a in cells[2].select("a[href]") if "magnet:?xt=urn:btih:" in a.attrs["href"]),
                None,
            )
            link = link_element.attrs["href"] if link_element else ""

            size = cells[3].get_text(strip=True)
            date_str = cells[4].get_text(strip=True)
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                date_str_formatted = date_obj.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date_str_formatted = None

            nyaa_data_list.append(NyaaData(date_str_formatted, size, name, link))

    nyaa_data_list.sort(key=lambda x: x.date if x.date else datetime.min, reverse=True)
    return nyaa_data_list


def match_games_by_month(year, month, dry_run=False, force=False, limit=None, only_missing=False):
    """Run the AI matching pipeline for one month.

    dry_run=True only reports results without changing getchu_games.
    Normal mode also writes match_judgements and learned keyword rules.
    """
    conn = open_db()
    ensure_getchu_schema(conn)
    cursor = conn.cursor()

    sql = "SELECT date, name, company, link FROM getchu_games WHERE date = ?"
    params = [f"{year}-{month:02d}"]
    if only_missing:
        sql += " AND (link IS NULL OR link = '')"
    sql += " ORDER BY name"
    cursor.execute(sql, params)
    rows = cursor.fetchall()

    keyword_rules = load_match_keyword_rules(conn)
    summary = {
        "year": year,
        "month": month,
        "total": len(rows),
        "processed": 0,
        "skipped_existing": 0,
        "ai_calls": 0,
        "rule_hits": 0,
        "matched": 0,
        "unmatched": 0,
        "duplicate": 0,
        "discarded": 0,
        "review": 0,
        "errors": [],
        "results": [] if dry_run else None,
    }

    for index, row in enumerate(rows):
        if limit and index >= limit:
            break
        game_date, game_name, company, old_link = row
        game = GetchuGame(game_date, game_name, company or "")

        if not force:
            previous = load_match_judgement(conn, game_date, game_name)
            if previous and not previous.get("need_retry"):
                summary["skipped_existing"] += 1
                continue

        try:
            nyaa_data_list = get_nyaa_data(game_name, company or "")
            result = judge_nyaa_match(
                game,
                nyaa_data_list,
                keyword_rules=keyword_rules,
            )
        except Exception as e:
            logger.exception("匹配失败: %s / %s", game_date, game_name)
            summary["errors"].append(f"{game_date} {game_name}: {e}")
            continue

        if result.source == "ai":
            summary["ai_calls"] += 1
        elif result.source == "rule":
            summary["rule_hits"] += 1

        verdict = result.verdict
        summary[verdict if verdict in summary else "review"] += 1
        summary["processed"] += 1

        if dry_run:
            summary["results"].append({
                "date": game_date,
                "name": game_name,
                "company": company,
                "verdict": verdict,
                "confidence": result.confidence,
                "source": result.source,
                "matched_name": result.matched_name,
                "reason": result.reason,
            })
            continue

        save_match_judgement(conn, result)
        if result.keywords:
            save_match_keyword_rules(conn, result.keywords)

        # Update getchu_games only when a real link is available.
        if result.has_match() and result.link and verdict in ("matched", "duplicate", "review"):
            cursor.execute(
                "UPDATE getchu_games SET size = ?, link = ?, nyaa_name = ? WHERE date = ? AND name = ?",
                (result.size, result.link, result.matched_name, game_date, game_name),
            )
            conn.commit()

        time.sleep(1)

    conn.close()
    return summary


def download_games_by_month(year, month):
    try:
        logger.info("开始获取%s年%s月的游戏下载链接", year, month)
        result = match_games_by_month(year, month, dry_run=False, only_missing=True)
        logger.info(
            "完成%s年%s月，处理%s条，匹配%s条，待复核%s条",
            year, month, result["processed"], result["matched"], result["review"],
        )
        return True
    except Exception as e:
        logger.error("获取%s年%s月游戏下载链接时出错: %s", year, month, str(e))
        return False


def get_years_list():
    conn = open_db()
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT substr(date, 1, 4) FROM getchu_games ORDER BY date DESC")
    years = [int(row[0]) for row in cursor.fetchall()]
    conn.close()
    return years


def get_download_link(year=None, month=None):
    """Unified download-link matching entry (fixes old missing-company bug)."""
    if year and month:
        logger.info("开始获取%s年%s月的下载链接", year, month)
        result = match_games_by_month(year, month, dry_run=False, only_missing=False)
        logger.info("已完成%s年%s月，处理%s条", year, month, result.get("processed", 0))
        return result

    logger.info("开始获取所有月份的下载链接")
    conn = open_db()
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM getchu_games ORDER BY date")
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()

    results = []
    for date in dates:
        year, month = map(int, date.split("-"))
        result = match_games_by_month(year, month, dry_run=False, only_missing=False)
        results.append(result)
    logger.info("已完成所有月份的下载链接获取")
    return {"success": True, "months": results}


def get_games_data():
    conn = open_db()
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            date,
            name,
            company,
            link as download_url,
            nyaa_name,
            comment,
            COALESCE(downloaded, 0) as downloaded,
            infohash_hex,
            COALESCE(submitted_115, 0) as submitted_115,
            submitted_pick_code,
            getchu_id,
            thumb_url,
            thumb_path
        FROM getchu_games
        ORDER BY date DESC
        """
    )
    games = [
        GetchuGame(
            row[0],
            row[1],
            row[2],
            None,
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            extra={
                "getchu_id": row[10],
                "thumb_url": row[11],
                "thumb_path": row[12]
            }
        )
        for row in cursor.fetchall()
    ]
    conn.close()
    return games
