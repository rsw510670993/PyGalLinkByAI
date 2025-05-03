import json
import logging
import re
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from models import GetchuGame, NyaaData
import sqlite3

import logging

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def clear_link(nyaa_data):
    # 清除链接信息，将 link 属性置为 None
    nyaa_data.link = None
    return nyaa_data

def get_raw_getchu_games(year, month):
    # 读取config.json文件，获取配置信息
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 从配置中获取需要跳过的关键词列表
    skip_list = config.get('skip', [])

    # 设置cookies跳过成年确认
    cookies = {'getchu_adalt_flag': 'getchu.com'}
    # 构造目标URL
    url = f'https://www.getchu.com/all/price.html?genre=pc_soft&year={year}&month={month}'
    # 发送GET请求并检查响应状态
    try:
        response = requests.get(url, cookies=cookies)
        response.raise_for_status()
    except Exception as e:
        logging.error(f'获取{year}年{month}月数据时出错: {str(e)}')
        return []
    
    # 使用BeautifulSoup解析HTML内容
    soup = BeautifulSoup(response.text, 'html.parser')
    if not soup:
        logging.error(f'解析{year}年{month}月HTML内容失败')
        return []
        
    # 查找所有背景色为白色的表格行
    game_rows = soup.find_all('tr', bgcolor='#ffffff')
    if not game_rows:
        logging.warning(f'{year}年{month}月没有找到游戏数据')
        return []
    
    raw_games = []
    for row in game_rows:
        columns = row.find_all('td')
        if len(columns) >= 3:
            # 提取日期、游戏名称和公司名称
            date = f"{year}-{month:02d}"
            name = columns[1].text.strip()
            company = columns[2].text.strip()
            
            # 确保公司和名称都不为空 并 跳过包含skip列表中字符串的记录
            if company and name and not any(skip_str in name for skip_str in skip_list):
                raw_games.append(GetchuGame(date, name, company))
    
    return raw_games

def deduplicate_games(raw_games):
    # 读取config.json文件，获取配置信息
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 从配置中获取delete列表
    delete_list, special_list = config.get('delete', []) , config.get('special', [])
    # 将delete_list按文字长度倒序排序
    combined_list = sorted(delete_list + special_list, key=len, reverse=True)
    
    # 处理game前，先删除delete和auto_del的文字
    for game in raw_games:
        for del_str in combined_list:
            if del_str in game.name:
                game.name = game.name.replace(del_str, '').strip()
    
    # 根据公司名的文字+游戏名长度升序排序
    raw_games.sort(key=lambda x: (x.company, len(x.name)))

    processed_games = []
    processed_names = set()

    for game in raw_games:
        stripped_name = game.name.rsplit(' ', 1)[0]  # 去除最后一个空格后的文本
        
        # 如果处理后的名称已经存在于processed_names中，则跳过
        if game.name in processed_names or stripped_name in processed_names:
            logging.debug("跳过重复游戏: %s", game.name)
            continue
        
        processed_games.append(game)
        processed_names.add(game.name)
    
    # 返回前根据日期和名称排序
    processed_games.sort(key=lambda x: (x.date, x.name))
    return processed_games

def get_getchu_games(year, month):
    raw_games = get_raw_getchu_games(year, month)
    processed_games = deduplicate_games(raw_games)
    return processed_games

def get_all_getchu_games(start_year, end_year, start_month, end_month, db_path='getchu.db'):
    logging.info(f'开始获取{start_year}年{start_month}月至{end_year}年{end_month}月的数据')
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS getchu_games (
                date TEXT,
                name TEXT,
                company TEXT,
                size TEXT,
                link TEXT,
                comment TEXT,
                PRIMARY KEY (date, name)
            )
        ''')
        success_count = 0
        for year in range(start_year, end_year + 1):
            for month in range(start_month, end_month + 1):
                logging.info(f'正在处理{year}年{month}月的数据')
                games = get_getchu_games(year, month)
                if not games:
                    logging.warning(f'{year}年{month}月没有获取到数据')
                    continue
                for game in games:
                    cursor.execute('INSERT OR IGNORE INTO getchu_games (date, name, company) VALUES (?,?,?)', (game.date, game.name, game.company))
                    logging.debug(f'已插入游戏: {game.name}')
                success_count += len(games)
                logging.info(f'完成{year}年{month}月的数据处理，共处理{len(games)}个游戏')
        conn.commit()
        conn.close()
        return success_count > 0
    except Exception as e:
        logging.error(f'get_all_getchu_games,数据库操作失败: {str(e)}')
        return False

def get_nyaa_data(game_name, company):
    # 去除干扰检索条件的特殊字符
    game_name = re.sub(r'[-]', '', game_name)
    try:
        response = requests.get(f'https://sukebei.nyaa.si/?f=0&c=1_3&q={game_name}+{company}')
        response.raise_for_status()
    except (requests.exceptions.ConnectTimeout, requests.exceptions.RetryError) as e:
        logging.error(f'获取游戏 {game_name} 数据时连接超时或重试次数过多: {e}')
        return []
    soup = BeautifulSoup(response.text, 'html.parser')
    rows = soup.find_all('tr')
    if not rows:
        keyword = re.sub(r'[^\w\s]', '', game_name)
        logging.warning(f'使用关键词 {keyword} 获取游戏 {game_name} 数据')
        try:
            response = requests.get(f'https://sukebei.nyaa.si/?f=0&c=1_3&q={keyword}+{company}')
            response.raise_for_status()
        except (requests.exceptions.ConnectTimeout, requests.exceptions.RetryError) as e:
            logging.error(f'使用关键词 {keyword} 获取游戏 {game_name} 数据时连接超时或重试次数过多: {e}')
            return []
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.find_all('tr')

    nyaa_data_list = []
    for row in rows:
        cells = row.find_all('td')
        if len(cells) >= 5:
            # 第2列 游戏名
            link_views = cells[1].select('a[href*="view"]')
            name_element = link_views[-1] if len(link_views) > 0 else None  # 如果有多个链接，取第一个作为游戏名，否则使用单元格内容作为游戏名
            name = name_element.attrs['title'] if name_element else cells[1].get_text(strip=True)
            
            # 第3列 下载链接
            link_element = next((a for a in cells[2].select('a[href]') if 'magnet:?xt=urn:btih:' in a.attrs['href']), None)
            link = link_element.attrs['href'] if link_element else ''
            
            # 第4列 大小
            size = cells[3].get_text(strip=True)
            
            # 第5列 日期
            date_str = cells[4].get_text(strip=True)
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                date_str_formatted = date_obj.strftime('%Y-%m-%d %H:%M')
            except ValueError as e:
                logging.warning("日期格式不匹配: %s, 错误信息: %s", date_str, e)
                date_str_formatted = None
            
            nyaa_data = NyaaData(date_str_formatted, size, name, link)
            nyaa_data_list.append(nyaa_data)
    
    # 按日期倒序排序
    nyaa_data_list.sort(key=lambda x: x.date if x.date else datetime.min, reverse=True)
    return nyaa_data_list

def download_games_by_month(year, month):
    """
    按月从nyaa获取游戏下载链接
    :param year: 要下载的年份
    :param month: 要下载的月份
    :return: 操作是否成功
    """
    try:
        logging.info(f'开始获取{year}年{month}月的游戏下载链接')
        
        # 从数据库获取该月份的游戏数据
        conn = sqlite3.connect('getchu.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM getchu_games WHERE date LIKE ?', (f"{year}-{month:02d}",))
        games = cursor.fetchall()
        
        if not games:
            logging.warning(f'{year}年{month}月没有找到游戏数据')
            return False
            
        # 为每个游戏获取nyaa下载链接
        success_count = 0
        for game in games:
            game_date = game[0]
            game_name = game[1]
            company = game[2]
            nyaa_data_list = get_nyaa_data(game_name, company)
            
            if nyaa_data_list:
                # 使用next函数优化查找逻辑
                selected_data = None
                # 优先查找包含girlcelly和年月的数据
                for data in nyaa_data_list:
                    if 'girlcelly' in data.name and f"{str(year)[-2:]}{month:02d}" in data.name:
                        selected_data = data
                        break

                if selected_data is None:
                    for data in nyaa_data_list:
                        if '2D.G.F.' in data.name and f"{str(year)[-2:]}{month:02d}" in data.name:
                            selected_data = data
                            break
                
                # 如果没有找到，则查找仅包含年月的数据
                if selected_data is None:
                    for data in nyaa_data_list:
                        if f"{str(year)[-2:]}{month:02d}" in data.name:
                            selected_data = data
                            break
                
                # 如果仍然没有找到，则使用第一条数据
                if selected_data is None and nyaa_data_list:
                    selected_data = clear_link(nyaa_data_list[0])
                    logging.warning(f'未找到包含{str(year)[-2:]}{month:02d}的下载链接，记录第一条数据: {selected_data.name}')
                
                if selected_data:
                    cursor.execute('UPDATE getchu_games SET size = ?, link = ?, nyaa_name = ? WHERE date = ? AND name = ?', 
                                 (selected_data.size, selected_data.link, selected_data.name, game_date, game_name))
                    success_count += 1
                    logging.debug(f'已更新游戏 {game_name} 的下载链接')
                else:
                    cursor.execute('''UPDATE getchu_games SET size = NULL, link = NULL, nyaa_name = NULL, comment = NULL
                                WHERE date = ? AND name = ?''', (game_date, game_name))
                    logging.debug(f'已清除游戏 {game_name} 的下载链接')        
            conn.commit()
            time.sleep(2)

        conn.close()
        
        logging.info(f'成功更新{year}年{month}月{success_count}个游戏的下载链接')
        return success_count > 0
        
    except Exception as e:
        logging.error(f'获取{year}年{month}月游戏下载链接时出错: {str(e)}')
        return False


def get_years_list():
    """直接从数据库查询所有不重复的年份列表"""
    conn = sqlite3.connect('getchu.db')
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT substr(date, 1, 4) FROM getchu_games ORDER BY date DESC')
    years = [int(row[0]) for row in cursor.fetchall()]
    conn.close()
    return years


def get_download_link(year=None, month=None):
    logging.info('开始获取下载链接')
    conn = sqlite3.connect('getchu.db')
    cursor = conn.cursor()
    
    if year and month:
        cursor.execute('SELECT * FROM getchu_games WHERE date = ?', f"{year}-{month:02d}")
    else:
        cursor.execute('SELECT * FROM getchu_games')
        
    games = cursor.fetchall()
    for index, game in enumerate(games):
        game_date = game[0]
        game_name = game[1]
        nyaa_data_list = get_nyaa_data(game_name)
        yymm_format = game_date.replace('-', '')[2:]
        current_list = [data for data in nyaa_data_list if yymm_format in data.name]
        current_list.sort(key=lambda x: yymm_format in x.name, reverse=True)
        if current_list:
            nyaa_data_list = current_list
        selected_data = None
        if nyaa_data_list:
            for data in nyaa_data_list:
                if 'girlcelly' in data.name:
                    selected_data = data
                    break
            else:
                selected_data = nyaa_data_list[0]
            if selected_data:
                try:
                    selected_data_date = datetime.strptime(selected_data.date, '%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    selected_data_date = None
                try:
                    game_date = datetime.strptime(game_date, '%Y-%m')
                except ValueError:
                    logging.warning(f"无效的日期格式: {game_date}")
                    continue
                if selected_data_date and selected_data_date < game_date:
                    cursor.execute('UPDATE getchu_games SET comment = ? WHERE date = ? AND name = ?', (str(selected_data.date), game[0], game[1]))
                cursor.execute('UPDATE getchu_games SET size = ?, link = ? WHERE date = ? AND name = ?', (str(selected_data.size), str(selected_data.link), game[0], game[1]))
                conn.commit()
                logging.info(f'已更新游戏 {game_name} 的下载链接和大小信息，当前进度: {index + 1}/{len(games)}')
        time.sleep(2)
    conn.close()
    
    if year and month:
        logging.info(f"已完成{year}年{month}月的下载链接获取")
    else:
        logging.info("已完成所有下载链接获取")

def get_games_data():
    conn = sqlite3.connect('getchu.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            substr(date, 1, 4) as year,
            substr(date, 6, 2) as month,
            name,
            company,
            link as download_url
        FROM getchu_games
        ORDER BY year DESC, month DESC
    ''')
    games = [GetchuGame(
            f"{row[0]}-{row[1]}",  # date
            row[2],  # name
            row[3],  # company
            None,     # size
            row[4],   # link
        ) for row in cursor.fetchall()]
    conn.close()
    return games

# 配置日志记录器
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 确保logger没有重复的handler
logger.handlers = []

# 创建一个文件处理器
file_handler = logging.FileHandler('app.log', encoding='utf-8')
file_handler.setLevel(logging.INFO)

# 创建一个流处理器，用于输出到控制台
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)

# 创建一个格式化器并添加到处理器
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# 将处理器添加到logger
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# 后续代码使用logger进行日志记录，例如：
# logger.info('This is an info message')