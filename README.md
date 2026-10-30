# PyGalLinkByAI

EGS / Nyaa / 115 资源流水线的**机械执行内核** + **只读 Web 面板**。

- 抓 EGS 清单 → 关联 Nyaa 磁链 → 提交 115 → 校对下载 → 按 `[YYYYMMDD][公司]游戏名` 归入 `/GAL/GAL-{年}`
- **所有需要判断的环节（低分磁链审核、记录修正、整理 issue、`/GAL.old` 清理）由 AI agent（如 DSH）通过 CLI 在会话中完成**，网页不承担判定
- 网页只做两件事：展示状态、启动可以盲跑的机械任务

## 页面（两页）

| 页面 | 内容 |
|---|---|
| `index.php` 总控 | 四段流水线状态、控制器（同步 / 提交并校对两个按钮）、当前任务进度、年历、维护（清理记录与 db 快照） |
| `tool/egs.php` 数据 | 只读卡片网格：封面、名称、公司、日期、阶段（已下载 / 已提交 / 有磁链 / 无资源 / 下载失败 / 重复）、备注 |

## 流水线（四段）

1. **同步（sync）**：EGS 清单增量 + 磁链搜索。磁链阶段带分层重扫——无结果的记录按作品年龄 30 / 90 / 180 / 365 天重新搜索，兼顾同人社团多年后才发种的情况。
2. **DSH 审核**：低分磁链由 AI agent 用 `egs review_detail / review_decide` 逐条确认；未清零时网页锁定提交按钮。
3. **提交并校对**：提交未提交的磁链到 115，完成后等 10 分钟自动校对下载结果（`check` 只校对提交满 10 分钟的记录）。
4. **最终核查**：整理 issue 与 `/GAL.old` 历史残留由 AI agent 处理，网页只显示积压数量。

## CLI（机械动作，AI agent 与运维共用）

```bash
# 同步（清单 + 磁链）
python -m tool.cli egs crawl --start-year 2026 [--month 9]
python -m tool.cli egs magnet --year 2026 [--rescan] [--limit N]

# 提交 / 校对
python -m tool.cli 115 submit --egs-id 12345 --magnet 'magnet:?xt=...' --dir /GAL/GAL-2026
python -m tool.cli 115 check  --magnet 'magnet:?xt=...' --dir /GAL/GAL-2026

# 审核（DSH 会话内使用）
python -m tool.cli egs review_detail --egs-id 12345
python -m tool.cli egs review_decide --egs-id 12345 --decision approve --candidate-id 7
python -m tool.cli egs review_blacklist list

# 记录修正 / 整理
python -m tool.cli egs update --egs-id 12345 --new-name '...'
python -m tool.cli egs organize_issues
python -m tool.cli egs organize_issue_resolve --id 42

# 统计 / 查询
python -m tool.cli egs overview
python -m tool.cli egs games --year 2026 --stage linked
```

流水线任务由 `tool/pipeline.py` 串行执行（状态文件 `status/pipeline.json`，支持停止与断点续跑）；网页接口在 `tool/api.php`，只暴露只读查询与机械任务开关。

## 安装

1. 克隆项目
2. 初回部署（Ubuntu 24.04）：`bash scripts/first_deploy_ubuntu24.sh`
3. 配置 `tool/config.json`
4. Web 服务器站点根目录指向仓库根目录（nginx + php-fpm），访问 `/index.php`

## 配置（`tool/config.json`）

| 键 | 说明 |
|---|---|
| `skip` / `delete` | 游戏名过滤与后缀剔除关键词 |
| `per_page` | 数据页每页卡片数（默认 24） |
| `magnet_*` | 磁链搜索的速率、提前结束阈值、reader 代理 |
| `magnet_rescan_limit` | 每轮分层重扫的条数上限（默认 50） |
| `115_save_path` / `115_cookies_path` | 115 保存根与 Cookie 路径 |
| `organize_name_format` | 归位命名模板 |

## 目录

```
index.php              总控
tool/egs.php           数据（只读）
tool/api.php           只读查询 + 机械任务开关的接口
tool/cli.py            CLI 统一入口（输出 JSON）
tool/pipeline.py       流水线（sync / submit / check / organize）
tool/egs_core.py       EGS 抓取与 schema
tool/egs_magnet.py     磁链搜索 / 分层重扫 / 候选
tool/egs_organize.py   命名、归位、整理 issue
tool/p115_client.py    115 API 封装
tool/agent_jobs.py     文件信箱任务总线（old_audit / prune 等）
tool/gal_old_audit.py  旧库审计
assets/app.css         设计系统（两页共用，深色模式跟随系统）
assets/app.js          前端逻辑
```

## Nginx 安全建议

站点根目录指向仓库时，建议拒绝访问：

- `/.venv/`
- `/getchu.db`、`/egs.db`（含 `-wal` / `-shm`）
- `/status/`、`/logs/`、`/db_backups/`
- `/tool/*.py`、`/tool/config.json`、`/tool/115-cookies.txt`
