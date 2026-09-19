# EGS 与 115 历史数据整理手册

> 更新时间：2026-09-19
> 适用分支：`feature/egs-integration`
> 用途：供后续 agent 接手 EGS 下载、校对、整理及 `/GAL.old` 历史数据清理。

## 1. 当前数据方针

### 1.1 数据源职责

- `egs.db` 是当前游戏记录、磁链状态和 115 整理映射的唯一写入目标。
- ErogameScape（EGS）按“作品”建档，当前流程只围绕 `egs_games` 工作。
- `getchu.db` 只用于历史对比：
  - 反查旧磁链和 InfoHash；
  - 判断旧目录来源；
  - 查看旧版本是否包含豪华版、特典、存档等附加内容。
- 不要把 getchu 旧记录重新导回 EGS，也不要修改 getchu 数据来“修正”当前状态。

### 1.2 115 目录职责

| 路径 | 用途 |
|---|---|
| `/GAL/GAL-YYYY` | EGS 正式管理的年度目录 |
| `/GAL/` | 有明确年份、但没有直接 EGS 记录的手动资源或全年龄资源 |
| `/GAL.old/GAL-YYYY` | 尚未完成审计的历史年度资源 |
| `/GAL.old/todo` | 年份或来源暂时无法确认的手动资源 |

EGS 自动校对和整理必须限制在 `/GAL/` 内，不得搜索 `/GAL.old` 或其它 115 目录。

## 2. 最重要的安全原则

1. **先只读审计，再移动或删除。**
2. 删除前至少确认名称、版本、InfoHash 和目录内容；名称相似不代表同一资源。
3. 同名但 InfoHash 不同，必须视为不同种子：
   - 可能是 DL 版与实体版；
   - 可能是豪华版、通常版或合集；
   - 可能仅包含特典；
   - 可能是后续更新版。
4. 只在 InfoHash 完全相同，或内容已经安全合并后，才清理旧目录。
5. 115 删除进入回收站，但仍应当作破坏性操作处理。
6. 修改 `egs.db` 前先复制到 `db_backups/`，文件名写明目的和日期。
7. 不要批量猜测续作关系。尤其要防止：
   - 原作被匹配到“2”“3”等续作；
   - `SPIN!` 被匹配到 `SPIN!2`；
   - 英文短标题命中另一部游戏。

## 3. 标准审计流程

### 3.1 列出历史目录

使用项目已有接口，不要自行拼 115 HTTP 请求：

```python
from tool.egs_organize import resolve_cid, list_dir_children

cid = resolve_cid("/GAL.old/GAL-2025")
items = list_dir_children(cid) or []
for item in items:
    print(item["cid"], item["n"], item["is_dir"])
```

保存并使用条目 CID。后续移动、删除和中断恢复均以 CID 为准，不要只依赖名称。

### 3.2 匹配 EGS

优先级从高到低：

1. 发行日期、公司和完整标题一致；
2. 标题仅有全半角符号、波浪线、感叹号或副标题差异；
3. getchu ID、DLsite ID、RJ 号等稳定标识一致；
4. 目录内容能够确认是同一游戏；
5. 最后才考虑模糊相似度。

可复用：

- `tool.egs_match._norm`
- `tool.p115_client._normalize_for_comparison`
- `tool.p115_client._names_match`

模糊匹配只用于生成候选，不能直接授权移动或删除。

### 3.3 核对磁链

同时读取：

- 当前 `egs_games.link / infohash_hex / nyaa_name`；
- 历史 `getchu_games.link / infohash_hex / nyaa_name`；
- `getchu_115_folders` 中旧目录 CID；
- `egs_115_folders` 中当前 EGS 目录 CID。

判断规则：

| 情况 | 处理 |
|---|---|
| 旧、新 InfoHash 相同 | 可判定为同一磁链；确认目标目录存在后，旧副本可清理 |
| InfoHash 不同、内容相同 | 视为不同发行种子，先比较目录内容 |
| 旧资源只有特典/存档 | 将附加文件搬入现有 EGS 目录，再清理空目录 |
| 旧资源是更完整的豪华版/合集 | 保留或合并；必要时将 EGS 主磁链切换到更完整资源 |
| EGS 无磁链但旧资源能明确对应 | 移入 EGS 年度目录，登记为手动下载 |
| 没有直接 EGS 记录 | 有明确年份移到 `/GAL/`；无年份移到 `/GAL.old/todo` |

### 3.4 比较实际目录内容

不要只看磁链标题。使用 `list_dir_children(cid)` 比较文件名：

- 主游戏压缩包；
- 实体版和 DL 版；
- OST、Drama CD、Voice Drama；
- 店铺特典；
- 更新包、补丁；
- 存档文件。

如果旧目录只有当前目录缺少的附加文件，应移动文件而不是删除整个旧目录。

## 4. 常见操作模式

### 4.1 安全重复清理

适用条件：

- EGS 和旧库 InfoHash 完全一致；
- 当前 EGS 目录已经存在且内容正常；
- 旧目录只是同一离线任务的历史副本。

操作：

1. 再次确认源 CID 和目标映射；
2. 调用 `tool.p115_client.delete_item(old_cid)`；
3. 重新列出父目录，确认源 CID 消失；
4. 在结果中明确说明目录进入了 115 回收站。

### 4.2 附加内容合并

适用条件：

- 两个种子属于同一游戏；
- 当前 EGS 主资源正常；
- 旧目录含额外特典、实体版、存档或其它值得保留的文件。

操作：

1. 预检目标目录中是否存在同名文件；
2. 使用 `tool.egs_organize.move_item(file_id, target_cid)` 逐项移动；
3. 每次移动后确认文件已离开源目录；
4. 全部移动完后确认源目录为空；
5. 删除空源目录；
6. 再次列出目标目录，核对所有预期文件。

不要先删源目录再验证目标。

### 4.3 手动下载记录

旧资源可以明确对应 EGS、但 EGS 没有可用磁链时：

- 将资源移动到 `/GAL/GAL-YYYY`；
- `egs_games.downloaded=1`；
- `submitted_115=0`；
- 不伪造 `link` 或 `infohash_hex`；
- `comment` 写明来源，例如：

```text
手动下载（从 /GAL.old/GAL-2025 搬入）
```

- `egs_115_folders.status` 可使用 `manually_moved_from_gal_old`。

EGS 页面会为包含“手动下载”的备注显示绿色标签，点击标签可查看完整备注。

### 4.4 1＋2 合集归属

当前约定：合集归给较新的续作，前作显示为重复磁链并指向续作。

主记录（例如 2 代）：

- 使用合集 `link / infohash_hex / nyaa_name`；
- `resource_kind='collection_dlc'`；
- `downloaded=1`；
- `submitted_115=1`；
- `magnet_duplicate=0`；
- `egs_115_folders.status='manual_collection_match'`。

前作：

- 共用合集磁链和 InfoHash；
- `downloaded=0`；
- `submitted_115=0`；
- `magnet_duplicate=1`；
- `duplicate_of_egs_id=<续作 EGS ID>`；
- `duplicate_reason='shared_cid_manual'`。

人工重复必须使用非 `infohash` 的原因，否则调用
`refresh_magnet_duplicates()` 时可能被自动重建逻辑覆盖。

已验证案例：

- `姉妹X催眠 MOTION EDITION 1＆2セット`：归给 2 代，1 代指向 2 代；
- `猫忍えくすはーとSPIN！1＋2 コンプリートパック`：归给 2 代，1 代指向 2 代。

合集内部还包含番外篇或 `LOVE+PLUS` 时，不要未经用户确认自动把所有相关作品都设为重复。

## 5. EGS 字段语义

### 5.1 `egs_games`

| 字段 | 语义 |
|---|---|
| `link` | 当前采用的磁链 |
| `infohash_hex` | 磁链身份；重复判断的首要依据 |
| `nyaa_name` | 磁链展示名 |
| `torrent_name/files/size` | 种子元数据；切换磁链时不能保留上一种子的陈旧内容 |
| `downloaded` | 已在允许范围内确认资源存在 |
| `submitted_115` | 当前磁链已提交到 115 |
| `submitted_pick_code` | 当前提交或目录对应的 Pick Code |
| `download_failed` | 当前磁链已确认失败 |
| `resource_kind` | `collection_dlc` 表示合集或 DLC 替代资源 |
| `magnet_duplicate` | 当前记录不应再次提交下载 |
| `duplicate_of_egs_id` | 指向实际拥有资源的主记录 |
| `duplicate_reason` | `infohash` 为自动重复；`shared_cid_manual` 为人工确认共用资源 |
| `comment` | 人工处理说明，页面可用于显示“手动下载”标签 |

### 5.2 `egs_115_folders`

该表以 `(date, name)` 为主键，记录 EGS 游戏与 115 实际目录的映射。

重要字段：

- `cid / pid / pick_code`
- `folder_name / folder_path / target_name`
- `status`

切换主资源时必须同步更新映射。不要让新磁链仍指向旧种子的目录。

## 6. 115 API 的实际陷阱

### 6.1 操作异步完成

连续移动或删除时常见：

```text
errno: 990009
操作尚未执行完成，请稍后再试
```

正确处理：

1. 不要立刻重复提交同一操作；
2. 重新列出源目录；
3. 如果 CID 已消失，说明前一次实际上成功；
4. 如果仍存在，再单独重试；
5. 批量操作时逐项执行，并在操作间留出短暂间隔。

### 6.2 TLS 或目录列表卡住

115 偶尔会卡在 TLS 握手或 `fs_files` 读取：

- 中止卡住的只读轮询不会回滚已经成功的移动；
- 中断后必须使用新连接重新列出源目录；
- 以实际 CID 是否仍存在为准，从剩余项继续；
- 不要因为客户端没有返回输出就盲目重放整批任务。

### 6.3 使用已确认 CID

路径解析本身也可能卡住。已经只读确认过的父目录 CID 可以在同一任务内复用：

- 移动 payload 语义为 `{fid, pid}`；
- 目录和文件都使用条目自身 CID/FID 作为 `fid`；
- `pid` 是目标目录 CID。

不得跨任务长期硬编码 CID；新任务开始仍应重新解析并验证一次。

## 7. 本轮 2025 清理结果

截至 2026-09-19：

- `/GAL.old/GAL-2025` 已清空，空目录本身仍保留；
- 11 个无磁链但可对应 EGS 的资源已移入 `/GAL/GAL-2025`，并标记手动下载；
- 猫忍 SPIN 1＋2 合集已归给 2 代，1 代指向 2 代；
- 2 个同 InfoHash 的安全重复目录已进入 115 回收站；
- 10 个同游戏不同种子的旧目录已将 24 个文件合并进现有 EGS 目录，空目录随后进入回收站；
- 最后 9 个有明确 2025 日期但无直接 EGS 记录的目录已移到 `/GAL/`；
- 1 个无日期的手动资源已移到 `/GAL.old/todo`。

猫忍合集操作前曾创建以下数据库备份，但已于 2026-09-19 按用户要求随
`db_backups/` 其余历史备份一并清理，当前不可用于恢复：

```text
db_backups/egs.before_neko_spin_collection_20260919.db
```

## 8. 后续 agent 执行清单

开始处理一个年份前：

- [ ] 确认当前 Git 分支和工作区，不覆盖用户未提交改动；
- [ ] 备份 `egs.db`；
- [ ] 只读列出 `/GAL.old/GAL-YYYY`；
- [ ] 导出 EGS 候选、当前磁链、下载状态和文件夹映射；
- [ ] 用 getchu 旧库反查历史磁链和目录 CID；
- [ ] 将项目分为：同 Hash、不同 Hash、无磁链、无 EGS、合集/特典；
- [ ] 对不同 Hash 的项目逐目录查看内容；
- [ ] 得到用户确认后再执行删除或复杂合集归属；
- [ ] 移动后按 CID 验证源和目标；
- [ ] 删除前确认源目录为空或确属完整重复；
- [ ] 最终报告剩余数量、移动数量、删除数量和可恢复性。

## 9. 禁止事项

- 不要让校对搜索 `/GAL.old`、`/我的下载` 或整个 115。
- 不要仅凭相似标题把前作和续作判为同一游戏。
- 不要把 `downloaded=1` 同时误当成“当前磁链一定正确”。
- 不要因为当前 EGS 已有磁链就删除不同 InfoHash 的旧资源。
- 不要删除仅包含特典或存档的目录，应先合并。
- 不要修改 getchu 历史库来配合 EGS。
- 不要提交 Cookie、完整个人目录信息或本地会话数据库。
- 不要提交 `.dsh-meow/`、`.p115client.cache.d/` 等运行缓存。
