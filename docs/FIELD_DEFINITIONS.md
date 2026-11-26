# 字段定义（枚举与含义）

> 2026-11-25 定稿。凡写入以下字段，只能使用本表枚举；新增取值需先改这里。

## egs_games

| 字段 | 取值 | 含义 | 写 | 读 |
|---|---|---|---|---|
| `link` | 磁链或 NULL | 这条记录当前选中的磁链 | magnet 选链 / decide_review / 人工 | 提交、校对、整理 |
| `infohash_hex` | 40 位 hex 或 NULL | 上一条磁链的 infohash（失败历史也保留作排除键） | 同上 | 去重、排除重搜 |
| `downloaded` | 0/1 | 115 产物已确认 | check / adopt | 提交与整理过滤 |
| `submitted_115` | 0/1 | 已提交过 115 离线 | submit / adopt | 提交与校对过滤 |
| `download_failed` | 0/1 | 判死种或明确失败，等重搜 | check 回滚 / 人工 | magnet 重搜队列 |
| `magnet_duplicate` | 0/1 | 非主记录（同链成员或共享目录 DLC） | `refresh_magnet_duplicates` / 人工 | 提交与整理过滤 |
| `duplicate_of_egs_id` | egs_id 或 NULL | 指向主记录 | 同上 | 归组、整理 |
| `duplicate_reason` | `NULL` / `infohash` / `shared_cid_manual` | 同链重复 / 共享 115 目录（DLC、合集） | 同上 | 归组、整理 |
| `resource_kind` | `''` / `collection_dlc` | 普通条目 / 合集内 DLC | 人工 / 抓取 | 校对跨年查找 |
| `is_eroge` | 0/1 | 全年龄标记（EGS 口径，不可靠，需核包名前缀） | 抓取 | 磁链与提交队列 |
| `brand_kind` | `CIRCLE` / `CORPORATION` | 同人社团 / 商业公司 | 抓取 | 展示 |
| `model` | `PC` | 平台 | 抓取 | — |
| `submission_excluded` | 0/1 | 排除出提交队列 | 人工 | 提交过滤 |
| `submission_excluded_reason` | 文本或 NULL | **deprecated**：字段保留但当前未使用 | — | — |
| `source` | `egs` | 情报源 | 抓取 | — |

## egs_nyaa_search_log

| 字段 | 取值 | 含义 |
|---|---|---|
| `review_status` | `none` / `review` / `approved` / `rejected` | 搜过无结果或已自动选中 / 有候选待复核 / 人工采用 / 已放弃。**不再是提交通道门禁**（gate 已于 2026-11-26 移除），只表示这条记录的匹配处理状态 |

## egs_nyaa_candidates

| 字段 | 取值 | 含义 |
|---|---|---|
| `tier` | `auto` / `review` / `reject` | 三档分流（2026-11-26）：`auto` 证据齐备可直接采用（≥40 分，或 20–40 分且公司与发售日双命中）；`review` 进待复核队列，由 `egs review_queue` 列出；`reject` 明确指向别的作品（CG 集、分卷、尾号不同）或分数 <20，不占用复核队列 |

## egs_115_folders

| 字段 | 取值 | 含义 |
|---|---|---|
| `status` | `ok` / `renamed` / `moved` / `renamed_moved` / `needs_manual` | 产物最终状态：已就位 / 已改名 / 已移动 / 已改名并移动 / 待人工 |
| `source` | `auto` / `orphan_match` / `collection_match` / `magnet_match` / `manual_move` / `adopt` | 这条映射怎么来的 |

旧值迁移对应（2026-11-25 一次性）：

| 旧 status | 新 status | 新 source |
|---|---|---|
| `renamed` | `renamed` | `auto` |
| `already_ok` | `ok` | `auto` |
| `renamed_moved` | `renamed_moved` | `auto` |
| `moved` | `moved` | `auto` |
| `ready` | `ok` | `auto` |
| `downloaded_unorganized` | `needs_manual` | `auto` |
| `manual_orphan_match` | `renamed` | `orphan_match` |
| `manual_collection_match` | `renamed` | `collection_match` |
| `manual_magnet_match` | `renamed` | `magnet_match` |
| `manually_moved_from_gal_old` | `moved` | `manual_move` |
| `found_set_downloaded` | `ok` | `adopt` |

## egs_artifacts

| 字段 | 取值 | 含义 |
|---|---|---|
| `kind` | `torrent` / `folder` / `both` | 只有磁链 / 只有 115 目录 / 两者都有 |
| `infohash_hex` | 40 位 hex 或 NULL，UNIQUE | 产物身份（磁链） |
| `folder_cid` | cid 或 NULL | 产物身份（115 目录） |

## egs_artifact_links

| 字段 | 取值 | 含义 |
|---|---|---|
| `relation` | `primary` / `member` / `dlc` | 本体（主记录）/ 同链成员或合集成员 / 共享目录 DLC |
| `source` | `auto` / `manual` | 自动推导 / 人工确认 |
| `note` | `folder:save` / `folder:date+name` / `magnet:selected` / `refresh:duplicates` / `manual:*` | 固定前缀，冒号后是动作或来源 |

## egs_organize_issues

| 字段 | 取值 | 含义 |
|---|---|---|
| `status` | `month_shift_confirm` / `missing_in_115` / `error` / `shared_cid` / `in_offline` | 搬月待确认 / 115 中找不到 / 出错 / 目录映射冲突 / 离线中 |

## 写入约束

- 代码常量见 `tool/egs_core.py` 的 `*_VALUES` 常量；新增枚举先改本文件与常量。
- `egs_artifact_links.source='manual'` 的行，自动双写不会覆盖其 `relation`/`note`。

## egs_external_ids（2026-11-26）

站内记录 ↔ 各来源编号。一条记录可挂多个来源、多个候选；`egs_games` 的 `getchu_id` / `dlsite_id` / `dmm` 仍是事实来源并保持双写，读路径用 `COALESCE(映射表 confirmed, 旧列)` 取首选值。

| 字段 | 取值 | 含义 |
|---|---|---|
| `source` | `getchu` / `dlsite` / `dmm` / `vndb` | 编号来源。不存 `egs` 自映射行——`egs_id` 列本身就是站内坐标；`vndb` 没有对应旧列，只存映射 |
| `status` | `confirmed` / `candidate` / `rejected` | 已核实可用 / 按名搜索出的待确认候选 / 已排除 |
| `origin` | `egs_crawl` / `getchu_search` / `dlsite_search` / `dmm_probe` / `dir_scan` / `vndb_search` / `manual` | 这一行怎么来的；`dir_scan` = 从 115 目录名或种子名里提取到的编号，`vndb_search` = 按名匹配 VNDB |
| `confidence` | 0–1 或 NULL | 匹配分数，只有搜索来源才有 |

写入与读取约定：

- `external_id_columns()` 生成读列表达式（映射表 confirmed 优先、旧列兜底），列表页与封面脚本共用同一口径。
- `attach_external_id()` 写映射行并按需回写主表空列，**只填空、不覆盖**已有值。
- `sync_row()` 由 EGS 抓取 upsert 调用，只补写不删除，人工判断（candidate/rejected）不会被抓取覆盖。

## 巡检

- 残留 link 只读检查：`python tool/cli.py egs artifact_audit`（加 `--strict` 时四类残留非零则退出码 1，供定时任务使用）。
- 口径实现见 `tool/egs_core.py` 的 `audit_artifact_links()`：失败态残留、记录/产物 hash 不一致、一个记录多条 link、有 hash 只剩 folder-only link，另附孤儿 artifact 数量。
- 失败态（`download_failed=1` 且 `link` 为空）的 hash link 由人工或清理脚本删除，`download_failed` 标记本身足够；`relation='dlc'` 与 `source='manual'` 的 link 不得自动清理。
