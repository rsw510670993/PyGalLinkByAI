import json
import logging
import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

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
            date = f"{year}-{month:02d}"
            name = columns[1].text.strip()
            company = columns[2].text.strip()
            if company and name and not any(skip_str in name for skip_str in skip_list):
                raw_games.append(GetchuGame(date, name, company))

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
        stripped_key = key.rsplit(" ", 1)[0] if " " in key else key
        if key in processed_keys or stripped_key in processed_keys:
            continue
        processed_games.append(game)
        processed_keys.add(key)

    processed_games.sort(key=lambda x: (x.date, x.name))
    return processed_games


def get_getchu_games(year, month):
    raw_games = get_raw_getchu_games(year, month)
    return deduplicate_games(raw_games)


def get_db_path(default=None):
    paths = runtime_paths()
    return paths["db_path"] if paths.get("db_path") else default


def ensure_getchu_schema(conn):
    cursor = conn.cursor()
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
    if "downloaded" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN downloaded INTEGER DEFAULT 0")
    if "infohash_hex" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN infohash_hex TEXT")
    if "submitted_115" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN submitted_115 INTEGER DEFAULT 0")
    if "submitted_pick_code" not in cols:
        cursor.execute("ALTER TABLE getchu_games ADD COLUMN submitted_pick_code TEXT")
    cursor.execute("UPDATE getchu_games SET submitted_115 = 1 WHERE COALESCE(downloaded, 0) = 1 AND COALESCE(submitted_115, 0) = 0")
    conn.commit()


def set_downloaded_status(date, name, downloaded=1, infohash_hex=None, db_path=None):
    conn = sqlite3.connect(db_path or get_db_path())
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    if infohash_hex:
        cursor.execute(
            "UPDATE getchu_games SET downloaded = ?, infohash_hex = ? WHERE date = ? AND name = ?",
            (downloaded, infohash_hex, date, name),
        )
    else:
        cursor.execute(
            "UPDATE getchu_games SET downloaded = ? WHERE date = ? AND name = ?",
            (downloaded, date, name),
        )
    conn.commit()
    conn.close()


def set_submitted_status(date, name, submitted_115=1, submitted_pick_code=None, db_path=None):
    conn = sqlite3.connect(db_path or get_db_path())
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
    conn = sqlite3.connect(db_path or get_db_path())
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
    conn = sqlite3.connect(db_path or get_db_path())
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM getchu_games WHERE date = ? AND name = ?", (date, name))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_all_getchu_games(start_year, end_year, start_month, end_month, db_path=None):
    logger.info("开始获取%s年%s月至%s年%s月的数据", start_year, start_month, end_year, end_month)
    try:
        conn = sqlite3.connect(db_path or get_db_path())
        ensure_getchu_schema(conn)
        cursor = conn.cursor()
        success_count = 0
        for year in range(start_year, end_year + 1):
            for month in range(start_month, end_month + 1):
                logger.info("正在处理%s年%s月的数据", year, month)
                games = get_getchu_games(year, month)
                if not games:
                    logger.warning("%s年%s月没有获取到数据", year, month)
                    continue
                for game in games:
                    cursor.execute(
                        "INSERT OR IGNORE INTO getchu_games (date, name, company) VALUES (?,?,?)",
                        (game.date, game.name, game.company),
                    )
                success_count += len(games)
                logger.info("完成%s年%s月的数据处理，共处理%s个游戏", year, month, len(games))
        conn.commit()
        conn.close()
        return success_count > 0
    except Exception as e:
        logger.error("get_all_getchu_games,数据库操作失败: %s", str(e))
        return False


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


def download_games_by_month(year, month):
    try:
        logger.info("开始获取%s年%s月的游戏下载链接", year, month)

        conn = sqlite3.connect(get_db_path())
        ensure_getchu_schema(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM getchu_games WHERE date LIKE ?", (f"{year}-{month:02d}",))
        total_count = int(cursor.fetchone()[0] or 0)
        if total_count <= 0:
            logger.info("%s年%s月没有游戏数据，跳过", year, month)
            conn.close()
            return True

        cursor.execute(
            "SELECT * FROM getchu_games WHERE date LIKE ? AND (link IS NULL OR link = '')",
            (f"{year}-{month:02d}",),
        )
        games = cursor.fetchall()

        if not games:
            logger.info("%s年%s月所有游戏都已有下载链接，跳过", year, month)
            conn.close()
            return True

        success_count = 0
        for game in games:
            game_date = game[0]
            game_name = game[1]
            company = game[2]
            nyaa_data_list = get_nyaa_data(game_name, company)

            if nyaa_data_list:
                current_month = f"{str(year)[-2:]}{month:02d}"
                selected_data = next(
                    (d for d in nyaa_data_list if "girlcelly" in d.name and current_month in d.name),
                    next(
                        (d for d in nyaa_data_list if "2D.G.F." in d.name and current_month in d.name),
                        next((d for d in nyaa_data_list if current_month in d.name), None),
                    ),
                )

                if selected_data is None and nyaa_data_list:
                    selected_data = clear_link(nyaa_data_list[0])
                    logger.warning(
                        "未找到包含%s的下载链接，记录第一条数据: %s", current_month, selected_data.name
                    )

                if selected_data:
                    cursor.execute(
                        "UPDATE getchu_games SET size = ?, link = ?, nyaa_name = ? WHERE date = ? AND name = ?",
                        (selected_data.size, selected_data.link, selected_data.name, game_date, game_name),
                    )
                    success_count += 1
                else:
                    cursor.execute(
                        """
                        UPDATE getchu_games
                        SET size = NULL, link = NULL, nyaa_name = NULL, comment = NULL
                        WHERE date = ? AND name = ?
                        """,
                        (game_date, game_name),
                    )
            time.sleep(2)

        conn.commit()
        conn.close()

        logger.info("成功更新%s年%s月%s个游戏的下载链接", year, month, success_count)
        return True
    except Exception as e:
        logger.error("获取%s年%s月游戏下载链接时出错: %s", year, month, str(e))
        return False


def get_years_list():
    conn = sqlite3.connect(get_db_path())
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT substr(date, 1, 4) FROM getchu_games ORDER BY date DESC")
    years = [int(row[0]) for row in cursor.fetchall()]
    conn.close()
    return years


def get_download_link(year=None, month=None):
    logger.info("开始获取下载链接")
    conn = sqlite3.connect(get_db_path())
    ensure_getchu_schema(conn)
    cursor = conn.cursor()

    if year and month:
        cursor.execute("SELECT * FROM getchu_games WHERE date = ?", f"{year}-{month:02d}")
    else:
        cursor.execute("SELECT * FROM getchu_games")

    games = cursor.fetchall()
    for index, game in enumerate(games):
        game_date = game[0]
        game_name = game[1]
        nyaa_data_list = get_nyaa_data(game_name)
        yymm_format = game_date.replace("-", "")[2:]
        current_list = [data for data in nyaa_data_list if yymm_format in data.name]
        current_list.sort(key=lambda x: yymm_format in x.name, reverse=True)
        if current_list:
            nyaa_data_list = current_list
        selected_data = None
        if nyaa_data_list:
            for data in nyaa_data_list:
                if "girlcelly" in data.name:
                    selected_data = data
                    break
            else:
                selected_data = nyaa_data_list[0]
            if selected_data:
                try:
                    selected_data_date = datetime.strptime(selected_data.date, "%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    selected_data_date = None
                try:
                    game_date_dt = datetime.strptime(game_date, "%Y-%m")
                except ValueError:
                    logger.warning("无效的日期格式: %s", game_date)
                    continue
                if selected_data_date and selected_data_date < game_date_dt:
                    cursor.execute(
                        "UPDATE getchu_games SET comment = ? WHERE date = ? AND name = ?",
                        (str(selected_data.date), game[0], game[1]),
                    )
                cursor.execute(
                    "UPDATE getchu_games SET size = ?, link = ? WHERE date = ? AND name = ?",
                    (str(selected_data.size), str(selected_data.link), game[0], game[1]),
                )
                conn.commit()
                logger.info("已更新游戏 %s 的下载链接和大小信息，当前进度: %s/%s", game_name, index + 1, len(games))
        time.sleep(2)
    conn.close()

    if year and month:
        logger.info("已完成%s年%s月的下载链接获取", year, month)
    else:
        logger.info("已完成所有下载链接获取")


def get_games_data():
    conn = sqlite3.connect(get_db_path())
    ensure_getchu_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            substr(date, 1, 4) as year,
            substr(date, 6, 2) as month,
            name,
            company,
            link as download_url,
            nyaa_name,
            comment,
            COALESCE(downloaded, 0) as downloaded,
            infohash_hex,
            COALESCE(submitted_115, 0) as submitted_115,
            submitted_pick_code
        FROM getchu_games
        ORDER BY year DESC, month DESC
        """
    )
    games = [
        GetchuGame(
            f"{row[0]}-{row[1]}",
            row[2],
            row[3],
            None,
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
        )
        for row in cursor.fetchall()
    ]
    conn.close()
    return games
