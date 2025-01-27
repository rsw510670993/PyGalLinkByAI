import logging
import os, requests, time
import pandas as pd
from bs4 import BeautifulSoup
from models import GetchuGame, NyaaData

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
    last_name = ''
    for row in game_rows:
        columns = row.find_all('td')
        if len(columns) >= 3:
            date = f"{year}-{month:02d}"
            name = columns[1].text.strip()
            company = columns[2].text.strip()
            if company:
                # 重命名变量以提高可读性
                is_name_similar = is_similar_name(name, last_name)
                if not is_name_similar:
                    game = GetchuGame(date, name, company)
                    games.append(game)
                    last_name = name
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
    rows = soup.find_all('tr', class_='success')
    
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
            date = cells[4].get_text(strip=True)
            
            nyaa_data = NyaaData(date, size, name, link)
            nyaa_data_list.append(nyaa_data)
    
    return nyaa_data_list

def get_download_link(file_path):
    # 读取Excel文件
    df = pd.read_excel(file_path, sheet_name=None)
    
    logging.info("开始处理Excel文件: %s", file_path)
    
    # 遍历每个sheet
    for sheet_name, sheet_df in df.items():
        logging.info("开始处理sheet: %s", sheet_name)
        
        # 新增size和link列
        sheet_df['size'] = ''
        sheet_df['link'] = ''
        
        # 遍历每一行，以name列为关键字调用get_nyaa_data
        for index, row in sheet_df.iterrows():
            game_name = row['name']
            nyaa_data_list = get_nyaa_data(game_name)
            
            # 优先选择包含girlcelly的结果
            selected_data = next((data for data in nyaa_data_list if 'girlcelly' in data.name), None)
            if not selected_data and nyaa_data_list:
                selected_data = nyaa_data_list[0]
            
            if selected_data:
                sheet_df.at[index, 'size'] = selected_data.size
                sheet_df.at[index, 'link'] = selected_data.link
                logging.info("已更新行 %d 的size和link", index + 1)  # +1是因为iterrows的行索引从0开始，而用户通常期望从1开始计数
            
            # 每次调用get_nyaa_data后休眠2秒
            time.sleep(2)
        
        # 将更新后的DataFrame写回Excel文件
        with pd.ExcelWriter(file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
            sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        logging.info("已完成处理sheet: %s", sheet_name)
    
    logging.info("已完成处理Excel文件: %s", file_path)