import json
import logging
import os
import re
import time
from datetime import datetime
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openpyxl.styles import PatternFill
from models import GetchuGame, NyaaData
import sqlite3

import logging
from logging import StreamHandler

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
    response = requests.get(url, cookies=cookies)
    response.raise_for_status()
    
    # 使用BeautifulSoup解析HTML内容
    soup = BeautifulSoup(response.text, 'html.parser')
    # 查找所有背景色为白色的表格行
    game_rows = soup.find_all('tr', bgcolor='#ffffff')
    
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
                raw_games.append(GetchuGame(len(raw_games)+1, date, name, company))
    
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
    
    # 返回前根据index再次排序
    processed_games.sort(key=lambda x: x.index)
    return processed_games

def get_getchu_games(year, month):
    raw_games = get_raw_getchu_games(year, month)
    processed_games = deduplicate_games(raw_games)
    return processed_games

def get_all_getchu_games(start_year, end_year, start_month, end_month, db_path='getchu.db'):
    logging.info(f'开始获取{start_year}年{start_month}月至{end_year}年{end_month}月的数据')
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
            logging.info(f'完成{year}年{month}月的数据处理，共处理{len(games)}个游戏')
    conn.commit()
    conn.close()

def get_nyaa_data(game_name):
    try:
        response = requests.get(f'https://sukebei.nyaa.si/?f=0&c=1_3&q={game_name}')
        response.raise_for_status()
    except (requests.exceptions.ConnectTimeout, requests.exceptions.RetryError) as e:
        logging.error(f'获取游戏 {game_name} 数据时连接超时或重试次数过多: {e}')
        return []
    soup = BeautifulSoup(response.text, 'html.parser')
    rows = soup.find_all('tr')
    if not rows:
        keyword = re.sub(r'[^\w\s]', '', game_name)
        try:
            response = requests.get(f'https://sukebei.nyaa.si/?f=0&c=1_3&q={keyword}')
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
            name_element = cells[1].select_one('a[href*="view"]')
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

def get_download_link():
    logging.info('开始获取下载链接')
    conn = sqlite3.connect('getchu.db')
    cursor = conn.cursor()
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
    logging.info("已完成从数据库获取数据并更新")

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