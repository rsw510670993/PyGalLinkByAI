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
- 磁链校验测试页：从数据页选择磁链，尝试获取元数据并展示预计下载的文件列表（需要 aria2c）

## 安装步骤
1. 克隆项目仓库
2. 初回部署（Ubuntu 24.04）：`bash scripts/first_deploy_ubuntu24.sh`
3. 配置`tool/config.json`文件
4. 配置 Web 服务器站点根目录指向仓库根目录（nginx+php-fpm）
5. 访问站点打开 Web 界面（首页：`/index.php`，数据页：`/tool/data.php`）

## 配置说明
编辑`tool/config.json`文件：
- `skip`: 跳过包含这些关键词的游戏
- `delete`: 从游戏名中删除这些关键词
- `per_page`: 每页显示的游戏数量
- `db_path`: SQLite 路径（可选，默认 `getchu.db`）
- `status_dir`: 状态文件目录（可选，默认 `status`）
- `log_path`: 日志文件路径（可选，默认 `logs/app.log`）

## 使用示例
1. 访问站点打开 Web 界面
2. 选择年份和月份
3. 点击"获取下载链接"按钮
4. 查看游戏列表和对应的下载链接

## 闲时自动任务（Getchu入库 / 磁链入库 / 115已下载校验）

项目新增 CLI：`python tool/cli.py auto idle_run`，用于在“闲时”自动执行：
- 当年 Getchu 游戏入库（已存在的记录会跳过）
- 当年 Nyaa 磁链获取入库（已存在 link 的记录会跳过）
- 115 登录有效时：对“有磁链且未标记已下载”的记录进行 115 已下载校验（不会自动发起 115 云下载）

### 配置项
在 `tool/config.json` 可选增加：
- `idle_timezone`：闲时判断时区（默认 `Asia/Tokyo`）
- `idle_start_hour`：闲时开始小时（默认 `0`）
- `idle_end_hour`：闲时结束小时（默认 `9`，按 `[start, end)` 判断）

### cron 示例
由你在服务器上自行配置 cron。命令本身会判断是否在闲时，不在闲时会自动退出（输出 JSON 便于日志排查）。

例如每小时执行一次（00:00-09:00 期间会真正跑，其余时间会跳过）：
```cron
0 * * * * cd /path/to/repo && /path/to/repo/.venv/bin/python tool/cli.py auto idle_run >> logs/idle_run.log 2>&1
```

## 磁链校验测试页

- 入口：数据展示页每条含磁链记录的“校验”按钮，会打开 `tool/magnet_check.php`
- 依赖：为了展示“预计下载内容物（文件列表）”，服务器需安装 aria2c：`sudo apt-get update && sudo apt-get install -y aria2`

## Nginx安全建议

如果 nginx root 指向仓库根目录，建议拒绝访问以下路径：

- `/.venv/`
- `/getchu.db`
- `/status/`
- `/logs/`
- `/tool/*.py`
- `/tool/config.json`
- `/tool/requirements.txt`

## 项目结构
- `index.php`: 首页（根目录）
- `tool/`: PHP 页面与接口 + Python 代码（同时也是 Python 包）
- `tool/api.php`: PHP API 入口
- `tool/data.php`: 数据展示页
- `tool/cli.py`: PHP 调用的 Python 统一入口（输出 JSON）
- `tool/core.py`: 核心爬虫和数据处理逻辑
- `tool/spider_worker.py`: 爬虫后台任务（写入状态文件）
- `tool/download_worker.py`: 下载链接后台任务（写入状态文件）
- `tool/runtime.py`: 运行时配置/状态文件工具
- `tool/config.json`: 配置文件
- `tool/requirements.txt`: Python 顶层依赖
- `scripts/first_deploy_ubuntu24.sh`: 初回部署脚本（删除既存 db/.venv 并重建）
