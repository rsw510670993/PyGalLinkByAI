# pyGal 游戏获取流程重构任务书

> 目标：重构游戏获取流水线为 4 个解耦阶段，先在 CLI 层面跑通（以 2026 年数据为测试），再改造 Web 页面。
> 测试范围：仅 2026 年数据，不做全量。

## 背景

现有流程：
- `tool/core.py::get_getchu_games(year, month)` 直接爬 getchu 并写入 `getchu_games` 表
- `tool/download_worker.py` 从 sukebei.nyaa 搜磁链直接回填 `link` 字段
- `tool/organize_115.py` 负责 115 目录整理
- `tool/cli.py` 提供 spider/download/115/auto 等子命令

新流程要求：
1. **爬取入库**：爬 getchu 游戏清单 → 交由第三方 AI 去重 → 入库
2. **磁链获取**：根据去重后的清单在 sukebei.nyaa 获取游戏下载地址
3. **磁链重标注**：解析磁链 dn 文件名，按 dn 时间戳 + 游戏文件名重新标注发布时间、公司名、游戏名
4. **115 校验整理**：检查 115 是否存在该文件，存在则确认是否在 `GAL/GAL-年份` 正确位置，并重命名游戏文件夹为 `[dn时间戳-年月日][公司名]游戏名`

## 阶段任务

### Phase 0：分析现有数据结构（已完成 2026-08-30）

- [x] P0-1 分析 `getchu.db` 现有 schema 与索引
- [x] P0-2 查看 2026 年现有测试数据的数量与字段质量
- [x] P0-3 理解现有 nyaa 爬取逻辑（`tool/core.py::get_nyaa_data`、`download_worker.py`）
- [x] P0-4 分析 115 目录命名规则（`organize_115.py`、`config.json::organize_name_format`）
- [x] P0-5 输出分析结论与新旧流程差异清单（见下方「Phase 0 结论」）

#### Phase 0 结论

**数据库现状（getchu.db）**

| 表 | 说明 |
|----|------|
| `getchu_games` | PK(date,name)，19列：date/name/company/size/link/nyaa_name/comment/downloaded/infohash_hex/submitted_115/submitted_pick_code/getchu_id/release_date/thumb_*/price/detail_*。唯一索引 `idx_getchu_games_gcid(getchu_id)` |
| `getchu_115_folders` | PK(date,name)：cid/pid/pick_code/folder_name/folder_path/target_name/date_code/company/status |
| `match_judgements` | 384条。verdict: matched 147 / unmatched 226 / review 10 / duplicate 1。2026年：matched 81(有link 80) / review 7 / unmatched 195 / 无记录 21 |
| `match_keyword_rules` | AI 提炼的关键字规则（include/discard/duplicate/review） |

**2026 数据现状**：267 条游戏，80 条有磁链，55 条 downloaded=1。1-8月已有爬取+磁链数据，9-12月仅爬取（未发售预登记）。注意：服务器系统时间为 2026-08-30，2026 为当前年份。

**现有 nyaa 匹配逻辑（develop 分支）**
- `get_nyaa_data(game_name, company)`：抓 sukebei.nyaa.si 搜索页 → NyaaData(date,size,name,link) 按日期倒序
- 优先级：`girlcelly+YYMMDD` > `2D.G.F.+YYMMDD` > `YYMMDD` > 第一条（清空link）
- 特性分支 `feature/aigc2d-nyaa-match-refactor` 已有完整 AI 匹配管道（aigc2d API + soul.md + keyword rules 学习 + match_games_by_month），未合并进 develop；key 已在 `tool/config/key/aigc2d.key`（base_url=next.aigc2d.com/v1, model=gpt-5.6-luna）

**现有 115 整理逻辑（organize_115.py v2，edf99bb）**
- 定位链：DB记录cid精确寻址 → dn多级搜索 → missing/not_downloaded
- 目标名：`[YYMMDD][公司]游戏名`（6位dn日期码，无空格）
- 状态：already_ok/would_rename/renamed/missing_in_115/not_downloaded/conflict/ambiguous/not_dir/no_dn_date/no_link/error
- 115 提交路径：calendar.php 走 `/GAL/GAL-${year}`；config.json `115_save_path=/我的下载/Getchu`（两处不一致，Phase 4 统一）

**dn时间戳 vs getchu日期差异实测**：79 条可解析 dn 日期码中 5 条与 getchu 月份不一致（LOOPERS PLUS getchu=2026-01 dn=251024、極煌戦姫2 dn=251219 等），证实"以 dn 时间戳为时间权威"的必要性。

**新旧流程差异清单（重构要点）**

| # | 现状 | 新流程要求 | 差距 |
|---|------|-----------|------|
| 1 | 规则去重（skip/delete列表+归一化），无原始名保留，无去重状态 | 第三方 AI 去重后入库 | 新增 raw_name/dedup_status/dedup_confidence/dedup_of 列 + dedup_cache 表 + AI 去重服务（复用 aigc2d client） |
| 2 | nyaa 匹配针对全部游戏（含未去重） | 仅对去重确认后的清单获取磁链 | nyaa_worker 按 dedup_status='confirmed' 过滤 + 候选集评分 |
| 3 | link/nyaa_name/date/company 保持 getchu 原值，dn 只用于命名 | 解析 dn → 重标注发布时间/公司名/游戏名 | *_orig 保留列 + relabel 模块 + release_ts 字段 |
| 4 | 目标名 `[YYMMDD][公司]名`；只重命名不校验父目录 | `[dn时间戳-年月日][公司名]游戏名`；校验 `GAL/GAL-年份` 位置 | compute_target_name 日期改 YYYY-MM-DD；新增父目录校验/搬移 + 存在性检查回写 submitted_115 |
| 5 | CLI 分散（spider/download/115 organize） | 4 阶段可独立执行 + pipeline 串接 | 新增 crawl/nyaa/magnet 子命令组 + pipeline 命令 |

**实施策略**：从 `feature/aigc2d-nyaa-match-refactor` 提取 `ai_matcher.py` 的 aigc2d 通信部分改造为去重服务；115 整理在 organize_115.py v2 上迭代；DB 迁移并入 `ensure_getchu_schema`（幂等 ALTER）。

### Phase 1：爬取 + AI 去重入库（已完成 2026-08-30）

- [x] P1-1 新建 `tool/dedup_service.py`：getchu 原始清单 → 规则预分组 → aigc2d AI 归组 → 入库
  - 规则预分组：config delete 列表剥后缀得 base_name，(company, base_name) 相同直接归组
  - 锚点直配：base 与已入库行同名 → 直接判 dup（免 AI）
  - AI 归组：月度单次调用，输出 unique/dup + canonical_name + 置信度，JSON 解析 + 失败兜底（rule_fallback）
  - 缓存：cache_key=sha1(company|base_name)，跨月复用（再版月零 AI 调用）
- [x] P1-2 新建 `tool/crawl_worker.py`：逐月后台执行 dedup_month，SIGTERM 优雅停机，状态落 `status/crawl_status.json`
- [x] P1-3 DB schema 增量迁移（`ensure_dedup_schema`，幂等）：
  - `getchu_games` 新增列：`raw_name / dedup_source / dedup_confidence / dedup_reason / dedup_updated_at`
  - 新表 `dedup_cache`（组级判定缓存）、`dedup_log`（逐条审计日志）
- [x] P1-4 `cli.py` 新增 `crawl` 子命令组：`crawl start --start-year --end-year [--month] [--no-ai]`、`crawl status`、`crawl stop`
- [x] P1-5 2026 年验证通过：
  - 343 条原始条目 → 312 组（规则合并 31 条）→ 267 锚点直配 + AI 判定 dup → **0 新增**（2026 已被旧流程爬全，符合预期）
  - AI 调用 9 次（首月各 1 次，缓存复用后 0），缓存命中 47，错误 0
  - 游戏行数 267 无变化；AI 正确归并平台版（PS4/PS5/Switch）、豪華版、予約版到本体
  - 抽样确认：`三國志8 REMAKE with パワーアップキット` 的 5 个平台/版本变体全部归并到本体

- [x] P1-6 存量行再去重（reconcile，已并入 Phase 1 流水线，幂等）：
  - 背景：新流程原设计只做"新爬条目→已有锚点"去重，锚点行之间不可合并，旧流程遗留的行级分裂（如 `三國志8 REMAKE with パワーアップキット` 与 `三國志8 REMAKE パワーアップキット`）原样保留
  - 实现：`dedup_service.reconcile_month()`——同公司内规范化名相似度≥0.72 的行为候选池 → 规则同名直并 + AI 判定（merges 仅纯表记差异必并 / editions 特典版次差异仅建议）→ 合并执行（备份DB → 归档被并行至 reconcile_archive → fill-if-null 并数据 → getchu_115_folders 引用迁移 → dedup_cache 指向修正 → 删被并行 → dedup_log 审计）
  - **幂等机制**：`reconcile_state` 表按月存行集合哈希（sha1(company|name 排序拼接)）——爬取流水线每月入库后自检：哈希未变化→跳过 reconcile（0 AI 调用零写入）；getchu 数据变化→新条目入库→哈希变化→自动重跑 reconcile。缓存写入带 skip-if-unchanged 保护，重复执行对 DB 零写入
  - CLI：`crawl start` 默认含 reconcile 阶段（`--no-reconcile` 可跳过）；`crawl reconcile --year [--month] [--execute] [--no-ai]` 保留为手动入口（默认预览）
  - 2026 验证：首次全量执行 5 组合并（三國志8 with/非with×2、カスタムオーダーメイド with/＋、BLUE REFLECTION 残留"版"、小金井荘 残留"版"）+ 34 条版次建议（TREASURE BOX/抱き枕/タペストリー等，仅记录不自动并）；三國志8 4行→2行
  - 幂等复验：连续第二次全量爬取 → inserted=0、reconcile AI 调用=0（12/12月 skipped）、6 张核心表内容哈希完全一致，零写入 ✅

**设计决策（与原计划的偏差）**：
- 重复版次**不落 getchu_games 行**（保持"一作品一行"语义，下游 nyaa/organize/calendar 无需改造），改记 `dedup_log` 审计 + `dedup_cache` 缓存
- `dedup_status` 列改为 `dedup_source`（legacy/rule/ai/rule_fallback/cache），canonical 行入库即 confirmed
- 缓存重放（source=cache）不重复写 dedup_log，审计只记首次判定
- skip 列表（再販/普及版/廉価版/ヌキコレ）仍在抓取层生效；delete 列表仅用于规则分组的 base_name 剥离，raw_name 保留原文

### Phase 2：sukebei.nyaa 磁链获取（已完成 2026-08-30）

- [x] P2-1 新建 `tool/nyaa_worker.py`：只处理 link 为空的已发售条目（Phase 1 去重后一行一作品）
  - 多轮关键词放宽：`游戏名+公司名` → `游戏名` → `去符号游戏名`，每轮限10条合并去重
  - 未发售过滤：`release_date <= today` 才搜索（不浪费请求、不污染搜索记录）
  - 幂等：`nyaa_search_log` 记录搜索史（UNIQUE date+name），重跑自动跳过已搜索行（零网络请求）；`--force` 强制重搜
  - 回填：link/nyaa_name/size/infohash_hex；候选全量落 `nyaa_candidates`（UNIQUE date+name+infohash）
- [x] P2-2 匹配策略模块 `tool/nyaa_match.py`（阈值40）
  - 评分：游戏名完整包含30 / 最长公共块≥85%给25 / 主标题拆分匹配25（「主标题 -副标题-」vs nyaa版次名不同场景）/ ≥60%给12；公司名10；发布日距发售日±7天15/±30天10/±60天5（无发布日退化为[YYMMDD]月份匹配10）；girlcelly 10 / 2D.G.F. 8
  - 规范化：波浪线/全半角统一、连字符与审查填充符（getchu○ vs nyaa●）双端删除
  - **离线校准**：84条已验证磁链 min=40 median=60，0条误拒
- [x] P2-3 `cli.py` 新增 `nyaa` 子命令组：`nyaa start --year [--month] [--force] [--limit]`、`nyaa status`、`nyaa stop`
- [x] P2-4 2026 年验证（含人工抽查）：
  - 已发售月份（1-8月）115个无磁链条目全部搜索完毕：**新增回填 5 条**（正确率抽查5/5，score 40~65），113条确认 sukebei 无资源（主机版/ASMR音声/小众作品，girlcelly不发布属正常）
  - 抽查样例：`ユニオリズム・カルテット B2-STYLE`（score65，±0天）、`as:9-nine- ARTEISIA`（score65，全角：归一后精确匹配）、`リビドー・アバンちゅ〜る`（score60，主标题拆分匹配命中——其nyaa副标题"プレミアムエディション"与getchu副标题不同）
  - 幂等复验：第二次运行 total=0（无任务），零请求零写入
  - 2026磁链覆盖：85/267（9-12月未发售83条除外，1-8月覆盖率 85/184≈46%，剩余为sukebei确无资源的小众作品）

### Phase 3：磁链解析与重标注（已完成 2026-08-30）

- [x] P3-1 dn 解析内置于 `tool/relabel.py`（`parse_magnet_dn` / `extract_dn_parts`）
  - 结构：`[发布者标签] [YYMMDD日期码]([getchu id]) [公司段] 游戏名段 [+ 特典] [尾部标记]`
  - 日期码=首个6位数字段（兼容多日期码如 Rance35th `[250406][250131]`）；7+位数字段取 getchu_id（仅空缺时回填）
  - 公司段=日期码后第一个「后面还有自由文本」的非数字括号段（噪音词过滤 serial/crack 等）
- [x] P3-2 重标注 `tool/relabel.py`：**dn 为唯一时间权威**（延续 organize v2 哲学）
  - `release_ts`（新列）← dn [YYMMDD] → YYYY-MM-DD；`release_date`（getchu发售日）保留仅展示；`date`（日历月桶）不变
  - `company` / `name` ← dn 公司段/游戏名段（清洗：去尾部[标记]、「 + 」特典段、版次词、连续重复词压缩），原值保留 `name_orig` / `company_orig`（仅真实变更时记录）
  - 名字清洗与 Phase1 规则分组同源（delete 列表）+ dn 特有版次词（DL版/DVD Version 等）
  - 守卫：同月 PK 冲突 + **跨月重名**（目标名已被其他行占用→不改名，防错配磁链产生影子行）
  - 级联：改名同步 getchu_115_folders / nyaa_candidates / dedup_cache 引用 + reconcile_state 哈希刷新（保持 Phase 1 幂等）
- [x] P3-3 `cli.py` 新增 `magnet` 子命令组：`magnet relabel --year [--month] [--execute] [--force]`（默认预览）、`magnet status`、`magnet parse --magnet`
- [x] P3-4 2026 年执行（备份 `db_backups/getchu.db.before_relabel.20260830_175907`）：
  - 85 条有磁链：84 条重标注（1 条英文版资源无日期码，正确跳过）；49 条仅补 release_ts、31 条改名（大部分为 `〜→～` 规范化）、6 条公司修正（POISON 品牌纠正、だーくニャー！品牌、WAFFLE 大小写、わるきゅ～れ 半角波线）
  - 7 条 dn 发布月与 getchu 发售月不一致（预售/早期发布场景，如 LOOPERS PLUS getchu=2026-01 dn=2025-10-24）——release_ts 如实记录
  - 2 条旧流程错配被重标注揭示：`にょにんじま1＋2`→dn 实为 2 代资源（已按 dn 改名）；`流星ワールドアクター Gaslight Bullet`(2026-03 本体行) 挂的是パック磁链→跨月重名守卫拦截改名（release_ts 仍记录），列入遗留数据质量问题
  - 幂等复验：第二次 execute already=84 / applied=0 / 零写入

### Phase 4：115 存在性检查与目录整理

- [ ] P4-1 存在性检查：`tool/organize_115.py` 增加按 infohash/文件名查 115 是否已存在
  - 存在 → 校验其目录是否为 `GAL/GAL-年份`，错误则移动
  - 不存在 → 标记 `submitted_115=0` 供后续提交
- [ ] P4-2 重命名规则：`[{dn时间戳:%Y-%m-%d}][{company}]{name}`
  - 与 `config.json::organize_name_format` 对齐，新增 `{dn_date}` 占位符
- [ ] P4-3 `cli.py` 扩展 `115 organize`：支持 `--year --month --name --dry-run`
  - dry-run 输出：旧路径 → 新路径 映射表
- [ ] P4-4 2026 年 dry-run 验证重命名映射无误后实际执行

### Phase 5：集成验证（2026 全链路）

- [ ] P5-1 新增 `pipeline` 子命令：按顺序串 Phase 1-4（支持 `--from-step` 断点续跑）
- [ ] P5-2 2026 年端到端跑一遍，核对每阶段统计数字与最终目录结构
- [ ] P5-3 回归旧命令兼容性（`spider`/`download` 保留为旧流程入口，标注 deprecated）

## 执行顺序与验收

| 阶段 | 产出 | 验收标准 |
|------|------|----------|
| P0 | 分析结论 | 差异清单确认 |
| P1 | crawl 命令 + 去重库 | 2026 年爬取+去重跑通，重复条目可识别 |
| P2 | nyaa 命令 | 2026 年磁链回填率 ≥ 目标，抽查正确 |
| P3 | magnet 命令 | dry-run 输出重标注映射且抽查正确 |
| P4 | organize 增强 | dry-run 映射正确，实际执行后目录符合 `GAL/GAL-年份/[dn日期][公司]名` |
| P5 | pipeline 命令 | 2026 年一键全链路成功 |

## 约束

- 仅 2026 年数据做测试，不触碰其他年份
- 所有写库操作先 dry-run / 先备份 `getchu.db`
- AI 去重必须有缓存与失败兜底（AI 不可用时退化为规则去重）
- Web 页面（`index.php`/`calendar.php`）在 CLI 全链路验证通过前不改动
