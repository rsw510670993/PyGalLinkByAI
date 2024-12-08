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

def get_all_getchu_games(start_year, end_year, start_month, end_month, file_path='getchu.xlsx'):

    # 检查文件是否存在
    mode = 'w' if not os.path.exists(file_path) else 'a'

    # 根据模式设置 if_sheet_exists 参数
    if_sheet_exists = 'replace' if mode == 'a' else None

    # 创建一个Excel writer对象
    with pd.ExcelWriter(file_path, engine='openpyxl', mode=mode, if_sheet_exists=if_sheet_exists) as writer:
        for year in range(start_year, end_year + 1):
            for month in range(start_month, end_month + 1):
                # 获取当前月份的数据
                games = get_getchu_games(year, month)
                
                if not games:
                    continue  # 如果没有数据，则跳过当前月份
                
                # 将 GetchuGame 对象转换为字典列表
                games_data = [
                    {'date': game.date, 'name': game.name, 'company': game.company}
                    for game in games
                ]
                
                # 创建一个DataFrame
                df = pd.DataFrame(games_data, columns=['date', 'name', 'company'])
                
                # 获取月份作为sheet名（格式化为YYYY-MM）
                sheet_name = f"{year}-{month:02d}"
                
                # 将DataFrame写入Excel的不同sheet中
                df.to_excel(writer, sheet_name=sheet_name, index=False)

def get_nyaa_data(game_name):
    response = requests.get(f'https://sukebei.nyaa.si/?f=0&c=1_3&q={game_name}')
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    rows = soup.find_all('tr')
    if not rows:
        keyword = re.sub(r'[^\w\s]', '', game_name)
        response = requests.get(f'https://sukebei.nyaa.si/?f=0&c=1_3&q={keyword}')
        response.raise_for_status()
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

def get_download_link(file_path):
    # 读取Excel文件
    df = pd.read_excel(file_path, sheet_name=None)
    
    logging.info("开始处理Excel文件: %s", file_path)
    
    # 遍历每个sheet
    for sheet_name, sheet_df in df.items():
        logging.info("开始处理sheet: %s", sheet_name)
        
        # 获取当前sheet名的时间
        try:
            sheet_time_obj = datetime.strptime(sheet_name, '%Y-%m')
        except ValueError:
            logging.warning("Sheet名 %s 格式不正确，跳过处理", sheet_name)
            continue
        
        # 确保所有需要的列都存在，并且数据类型为字符串
        for col in ['size', 'link', 'comment']:
            if col not in sheet_df.columns:
                sheet_df[col] = ''
            sheet_df[col] = sheet_df[col].astype(str)
        
        rows_to_fill = []
        # 遍历每一行，以name列为关键字调用get_nyaa_data
        for index, row in sheet_df.iterrows():
            # 如果 link 已有数据，则跳过当前行
            if row['link'] != '' and pd.notna(row['link']):
                continue
            game_name = row['name']
            nyaa_data_list = get_nyaa_data(game_name)
            
            selected_data = None
            if nyaa_data_list:
                for data in nyaa_data_list:
                    if 'girlcelly' in data.name:
                        # 若数据名称中包含"girlcelly"，则立即选定此数据并中断循环
                        selected_data = data
                        break
                else:
                    # 若循环正常结束而未通过break中断，说明未找到包含"girlcelly"的数据
                    selected_data = nyaa_data_list[0]
            
            if selected_data:
                try:
                    selected_data_date = datetime.strptime(selected_data.date, '%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    selected_data_date = None
                
                if selected_data_date and selected_data_date < sheet_time_obj:
                    # 如果date小于sheet名时间，则将date计入comment列
                    sheet_df.at[index, 'comment'] = str(selected_data.date)
                    # 记录需要设置背景色的行索引
                    rows_to_fill.append(index)
                
                # 否则，将size和link计入Excel
                sheet_df.at[index, 'size'] = str(selected_data.size)
                sheet_df.at[index, 'link'] = str(selected_data.link)
                logging.debug("已更新行 %d 的size和link", index + 1)  # 行索引通常从0开始，但用户可能期望从1开始计数
            
            # 每次调用get_nyaa_data后可能需要休眠（如果需要的话）
            time.sleep(2)
        
        # 将更新后的DataFrame写回Excel文件
        with pd.ExcelWriter(file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
            sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
            for row_idx in rows_to_fill:
                excel_row = row_idx + 2  # 加上标题行的偏移
                for cell in worksheet.iter_rows(min_row=excel_row, max_row=excel_row, values_only=False):
                    for c in cell:
                        c.fill = fill
        
        logging.info("已完成处理sheet: %s", sheet_name)
    
    logging.info("已完成处理Excel文件: %s", file_path)