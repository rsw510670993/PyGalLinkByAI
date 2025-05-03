from tool import get_nyaa_data
import logging

# 配置日志记录器
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# 测试游戏名称
test_game = "美少女万華鏡 -罪と罰の少女-"
test_company = "ωstar"
#test_game = "楓ふうあ"

# 调用函数并打印结果
print(f"测试游戏: [{test_company}]{test_game}")

year = 2018
month = 1
nyaa_data_list = get_nyaa_data(test_game, test_company)
# 输出nyaa_data_list中所有的name
for data in nyaa_data_list:
    print(data.name)
# 使用next函数优化查找逻辑
selected_data = next(
    (d for d in nyaa_data_list 
        if 'girlcelly' in d.name and f"{str(year)[-2:]}{month:02d}" in d.name),
    next(
        (d for d in nyaa_data_list 
            if f"{str(year)[-2:]}{month:02d}" in d.name),
        nyaa_data_list[0] if nyaa_data_list else None
    )
)

print(f"返回结果: {selected_data}")