# PyGalLinkByAI

## 项目概述
这是一个基于 Python 3.10 的爬虫项目，用于从 Getchu 网站获取游戏信息并关联 Nyaa 的下载链接。网页部分使用 PHP 提供页面与接口，Python 作为任务执行器被 PHP 触发运行。

## 功能特性
- 从Getchu网站爬取游戏数据
- 自动关联Nyaa的磁力链接
- 提供Web界面查看和搜索游戏数据
- 支持按年份和月份筛选游戏
- 重复下载链接会以红色标注，用户需手动取消勾选来避免复制重复链接
- 游戏名称下方的小字显示的是不匹配但疑似有下载链接的游戏

## 安装步骤
1. 克隆项目仓库
2. 安装依赖：`pip install -r requirements.txt`
3. 配置`config.json`文件
4. 配置 Web 服务器站点根目录指向 `public/`（nginx+php-fpm）
5. 访问站点打开 Web 界面（首页：`/index.php`，数据页：`/data.php`）

## 配置说明
编辑`config.json`文件：
- `skip`: 跳过包含这些关键词的游戏
- `delete`: 从游戏名中删除这些关键词
- `special`: 特殊处理的关键词
- `per_page`: 每页显示的游戏数量
- `db_path`: SQLite 路径（可选，默认 `getchu.db`）
- `status_dir`: 状态文件目录（可选，默认 `status`）
- `log_path`: 日志文件路径（可选，默认 `logs/app.log`）

## 使用示例
1. 访问站点打开 Web 界面
2. 选择年份和月份
3. 点击"获取下载链接"按钮
4. 查看游戏列表和对应的下载链接

## 项目结构
- `tool.py`: 核心爬虫和数据处理逻辑
- `cli.py`: PHP 调用的 Python 统一入口（输出 JSON）
- `spider_worker.py`: 爬虫后台任务（写入状态文件）
- `download_worker.py`: 下载链接后台任务（写入状态文件）
- `runtime.py`: 运行时配置/状态文件工具
- `public/`: PHP 页面与接口（站点根目录）
- `config.json`: 配置文件
