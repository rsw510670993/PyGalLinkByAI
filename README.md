# PyGalLinkByAI

## 项目概述
这是一个基于Python 3.10和Flask框架的爬虫项目，用于从Getchu网站获取游戏信息并关联Nyaa的下载链接。

## 功能特性
- 从Getchu网站爬取游戏数据
- 自动关联Nyaa的磁力链接
- 提供Web界面查看和搜索游戏数据
- 支持按年份和月份筛选游戏
- 重复下载链接会以红色标注，用户需手动取消勾选来避免复制重复链接

## 安装步骤
1. 克隆项目仓库
2. 安装依赖：`pip install -r requirements.txt`
3. 配置`config.json`文件
4. 运行项目：`python web_controller.py`

## 配置说明
编辑`config.json`文件：
- `skip`: 跳过包含这些关键词的游戏
- `delete`: 从游戏名中删除这些关键词
- `special`: 特殊处理的关键词
- `per_page`: 每页显示的游戏数量

## 使用示例
1. 访问`http://localhost:5000`打开Web界面
2. 选择年份和月份
3. 点击"获取下载链接"按钮
4. 查看游戏列表和对应的下载链接

## 项目结构
- `web_controller.py`: Flask应用入口
- `tool.py`: 核心爬虫和数据处理逻辑
- `templates/`: Web界面模板
- `config.json`: 配置文件