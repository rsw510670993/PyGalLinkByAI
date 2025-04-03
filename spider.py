from tool import get_all_getchu_games, get_download_link


# 定义要获取数据的年份和月份范围
for i in range(2016, 2017):
    start_year, end_year = i, i
    start_month, end_month = 1, 1
    file_path=f'xlsx/{i}.xlsx'
    # 获取Getchu游戏数据
    get_all_getchu_games(start_year, end_year, start_month, end_month, db_path='getchu.db')
    get_download_link()