from tool import get_all_getchu_games, get_download_link
import logging

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 定义要获取数据的年份和月份范围
for i in range(2018, 2021):
    start_year, end_year = i, i
    start_month, end_month = 1, 12  # 默认处理1-12月
    file_path=f'xlsx/{i}.xlsx'
    logging.info(f'开始处理{start_year}年{start_month}月至{end_month}月的数据')
    # 获取Getchu游戏数据
    get_all_getchu_games(start_year, end_year, start_month, end_month, db_path='getchu.db')
    logging.info(f'开始获取{start_year}年的下载链接')
    get_download_link()
    logging.info(f'完成{start_year}年的数据处理')