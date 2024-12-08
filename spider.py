from tool import get_all_getchu_games, get_download_link


# 定义要获取数据的年份和月份范围
start_year = 2024
end_year = 2024
start_month = 1
end_month = 12
file_path='xlsx/2024.xlsx'
# 获取Getchu游戏数据
get_all_getchu_games(start_year, end_year, start_month, end_month, file_path)
get_download_link(file_path)