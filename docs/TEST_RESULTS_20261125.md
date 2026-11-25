# 测试与验收结果（2026-11-25）

## 测试

- 命令：`.venv/bin/python -m pytest tests -q`
- 结果：**141 passed, 0 failed**（基线 134 + 新增 7），耗时 49.64s
- 新增：`tests/test_egs_artifact_audit.py`，7 个用例
  - 干净库 ok
  - 四类残留各计 1（a/b/c/d）
  - 失败态挂 manual/dlc link 不报为残留
  - dlc link 不误报 hash 不一致
  - 双表缺失时 tables_present=false
  - CLI 子命令注册与 `--strict`

## 巡检与清理验收

- 四类残留（删除后）：**a=0 / b=0 / c=0 / d=0**
- 备份：`db_backups/egs_20261125_before_link_cleanup.db`（110,653,440 B，integrity_check=ok）
- links：11,021 → 10,998（删 23 条 `source='auto' AND relation!='dlc'` 的失败态 hash link）
- egs_artifacts 10,801 不变；egs_115_folders 7,385 不变；孤儿 artifact 7 → 27
- 巡检命令：`python tool/cli.py egs artifact_audit`（`--strict` 时四类残留非零退出码 1）

## 改动文件

- `tool/egs_core.py`：新增只读函数 `audit_artifact_links()`
- `tool/cli.py`：新增 `egs artifact_audit [--db] [--strict]`
- `tests/test_egs_artifact_audit.py`：新增测试
- `docs/FIELD_DEFINITIONS.md`：补「巡检」入口说明
