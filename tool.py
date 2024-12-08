from datetime import datetime
import json
import logging
import os, requests, time
import pandas as pd
from bs4 import BeautifulSoup
from models import GetchuGame, NyaaData
from openpyxl.styles import PatternFill

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def strip_after_last_space(text):
    if ' ' in text:
        return text.rsplit(' ', 1)[0]
    return text

def is_similar_name(name1, name2):
    name1_main = strip_after_last_space(name1.rstrip())
    name2_main = strip_after_last_space(name2.rstrip())
    return name1_main == name2_main or name1_main == name2 or name2_main == name1

def get_getchu_games(year, month):
    # 设置cookies跳过成年确认
    cookies = {'getchu_adalt_flag': 'getchu.com'}
    url = f'https://www.getchu.com/all/price.html?genre=pc_soft&year={year}&month={month}'
    response = requests.get(url, cookies=cookies)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    game_rows = soup.find_all('tr', bgcolor='#ffffff')
    
    # 如果游戏行为空，则直接返回空列表
    if not game_rows:
        return []
    
    games = []
    for row in game_rows:
        columns = row.find_all('td')
        if len(columns) >= 3:
            date = f"{year}-{month:02d}"
            name = columns[1].text.strip()
            company = columns[2].text.strip()
            games.append((date, name, company))
    
    return games

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
    url = f'https://sukebei.nyaa.si/?f=0&c=1_3&q={game_name}'
    response = requests.get(url)
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
        
        # 检查是否已存在size、link和comment列，如果不存在则添加
        if 'size' not in sheet_df.columns:
            sheet_df['size'] = ''
        if 'link' not in sheet_df.columns:
            sheet_df['link'] = ''
        if 'comment' not in sheet_df.columns:
            sheet_df['comment'] = ''
        
        rows_to_fill = []
        # 遍历每一行，以name列为关键字调用get_nyaa_data
        for index, row in sheet_df.iterrows():
            # 如果 link 已有数据，则跳过当前行
            if pd.notna(row['link']):
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
                    # 如果date小于sheet名时间，则跳过并将date计入comment列
                    sheet_df.at[index, 'comment'] = selected_data.date
                    # 记录需要设置背景色的行索引
                    rows_to_fill.append(index)
                else:
                    # 否则，将size和link计入Excel
                    sheet_df.at[index, 'size'] = selected_data.size
                    sheet_df.at[index, 'link'] = selected_data.link
                    logging.info("已更新行 %d 的size和link", index + 1)  # 行索引通常从0开始，但用户可能期望从1开始计数
            
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