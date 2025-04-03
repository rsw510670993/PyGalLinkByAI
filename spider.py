from tool import get_all_getchu_games, get_download_link


# 定义要获取数据的年份和月份范围
for i in range(2018, 2022):
    start_year, end_year = i, i
    start_month, end_month = 1, 12
    file_path=f'xlsx/{i}.xlsx'
    # 获取Getchu游戏数据
    get_all_getchu_games(start_year, end_year, start_month, end_month, file_path)
    get_download_link(file_path)