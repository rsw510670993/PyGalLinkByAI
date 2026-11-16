# gshelf 全CG存档补档 · 实施说明

更新：2026-11-12｜脚本：`scripts/backfill_savedata.py`

## 用途

给已下载的游戏补「全CG存档」：按游戏名从存档站匹配 → 下载 → 直传 115 对应游戏目录。
新抓到游戏、且 `egs_115_folders` 已有映射后，重跑一遍即可自动补上缺的。

## 数据来源

| 站点 | 抓取方式 | 条目 |
| --- | --- | --- |
| seiya-saiga.com | 单页 `save.html` | 4,322 条（优先） |
| sagaoz.net/savedata | 首页 58 个五十音索引页 | 7,834 条（zip 4,544 + LZH 3,294） |

- 索引缓存在 `status/savedata_index.json`，24 小时内复用；`--refresh` 强制重抓。
- 编码都是 Shift_JIS/CP932，脚本内已处理；sagaoz 的 href 是相对页面目录的，必须拼 `{页面目录}/{href}`。

## 匹配口径

1. `egs_games.downloaded=1` 且 `egs_115_folders` 有 `(date, name)` 映射 → 得到游戏目录 cid（约 93.8% 可定位）。
2. 游戏名归一化：NFKC、去副标题/版本括号、去标点空格 → 与存档站条目标题比对；同一游戏 seiya 优先、sagaoz 补位。
3. 已成功（`egs_savedata.status='ok'`）的自动跳过。

## 运行环境

- **必须用 `status/venv115`**（p115client 0.0.9）。项目 `.venv` 里的 0.0.8 调 `upload_file` 会 405（uplb 接口被阿里云 CDN 拦）。
- 重建：`python3 -m venv status/venv115 && status/venv115/bin/pip install p115client==0.0.9`
- 115 cookies：`tool/config.json` 的 `115_cookies_path`，默认 `tool/115-cookies.txt`。

## 命令

```bash
cd /var/www/html/gshelf

# 预演：只看候选数与前 10 条
status/venv115/bin/python scripts/backfill_savedata.py --limit 100

# 执行：每次 100 个，1.2s/个限速
status/venv115/bin/python scripts/backfill_savedata.py --limit 100 --execute --delay 1.2

# 只跑某年
status/venv115/bin/python scripts/backfill_savedata.py --year 2015 --limit 100 --execute
```

## 流程与落库

下载 → zip 校验（必须有 readme 或存档文件）→ `upload_file` 直传到游戏目录，文件名 `全CG存档_<游戏名>.zip` → 写入 `egs_savedata` → 本地包立即删除。

`egs_savedata` 字段：egs_id / egs_name / source / url / sha1 / size / content_tag / folder_cid / pickcode / filename / status / uploaded_at / note。

## 限额与分批（重要）

115 直传**每日上传文件数约 3,000 个**，超过后 OSS 返回 403（p115client 报 `MultipartUploadAbort`），换 client/换目录都无效，**次日自动恢复**。建议每天控制在 2,500 以内、分批跑；断点续跑不会重复传。

## 常见失败

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| BAD-ZIP / not zip | 源文件本身是 LZH 或损坏 | 跳过，不重试 |
| DL-FAIL | 源 URL 404 | 跳过 |
| UPLOAD-FAIL / 403 | 日限额满或网络中断 | 次日重跑 / 重试 |

## 2026-11-12 基线

成功补档 **3,837** 个；剩余 10 个为坏源（5 非 zip、3 源 404、2 上传失败）。本地无残留。
