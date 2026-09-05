# HANDOFF：getchu 流程收尾 → 批判空间（ErogameScape）新情报源调研

> 交接时间：2026-09-01
> 分支状态：`develop` @ b823785（工作区干净）；全部 getchu 重构成果在 `feature/nyaa-release-date-reconciliation` @ d81104c（已推 GitHub）
> 任务书：`docs/REFACTOR_PLAN.md`（已从 feature 分支恢复到工作区，**未提交**——新工程建新 feature 分支时再一并处理）

---

## 一、上一工程（getchu 重构）最终状态速览

### 已完成（feature/nyaa-release-date-reconciliation）
| 阶段 | 状态 | 关键成果 |
|---|---|---|
| Phase1 爬取+AI去重 | ✅ | `crawl` 命令 + `dedup_service.py`（规则分组+AI+缓存） |
| Phase2 磁链获取 | ✅ | `nyaa` 命令（sukebei 匹配，2026 链接 81） |
| Phase3 dn重标注 | ✅ | `magnet relabel`（身份/展示分层 getchu_* vs date/name/company + release_ts 多码规则） |
| Phase4 115整理 | ✅ | `115 organize`（[YYYY-MM-DD][公司]名 + GAL-年份目录校验 + review清单） |
| 审核面板 | ✅ | `tool/review_web.php` + `review_api.php`（去重建议/115待办/无日期码行 三标签） |

### 2026 逐月 dedup 修复进度（换情报源前中断）
- 12月 ✅（進撃の巨人3 5行→1行）、11月 ✅（27→13行）
- 01–10月 ⏸ 未跑（平台恢复行51 + 假名行18 清理未完成）
- **这些遗留不影响新工程**：新情报源将是全新的表/流程，getchu 数据保留作历史

### 核心经验教训（新工程必须吸取）
1. **getchu 是商品视角**：每个特典版/平台版/店铺限定版都是独立行——这是去重困境的根源。AI 判不干净、规则剥字出残片（`Nintendo Switch2版`→`2版`）、补丁式 delete 表越打越补
2. **批判空间（ErogameScape）是作品视角**：一个游戏一条记录，版本/特典是附属信息——**从源头消除去重问题**（用户裁定方向）
3. 惯例（继续遵守）：dry-run 优先 + DB 备份；幂等可重跑；AI 有缓存与兜底；feature 分支作业不碰 develop/main；用户中文沟通、表格报告
4. 环境：`.venv/bin/python`；`HOME=/var/www/html/pyGal`（p115client 缓存）；sukebei 限流 2.5s；115 API sleep 0.5s；bash 单次调用 ≤300s（长任务拆分）；`GIT_CONFIG_GLOBAL=/tmp/gitconfig`（含 safe.directory）

### 基础设施（可直接复用）
- `tool/p115_client.py`（115 API 封装，含 fs_move web 端点 payload `{fid,pid}`、搜索索引滞后年份目录直读兜底）
- `tool/organize_115.py`（Phase4 整理逻辑，dn_date 语义可直接对接新情报源）
- `tool/review_web.php`/`review_api.php`（审核面板，CLI 桥接模式）
- `tool/runtime.py`（状态文件/决定记录/原子写）
- nginx `/pyGal/` + php8.3-fpm；**php-sqlite3 扩展不可用**（apt 被锁），PHP 侧一律走 CLI 桥接

---

## 二、新工程：批判空间（ErogameScape）对接

### 目标
游戏情报源从 getchu（商品视角）切换到 批判空间（作品视角），重写"爬取入库"阶段，消除特典混杂与去重困境；后续阶段（磁链/重标注/115整理）复用现有机制。

### 建议新分支
`feature/egs-integration`（从 develop 切出；REFACTOR_PLAN.md 未提交文件一并纳入或改写）

### 需要新对话调研的关键点
1. **EGS 数据获取方式**：
   - ErogameScape 无官方 API；有非官方 JSON（`https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/sql_for_erogame_formerly.php?mode=...`）与 HTML 表格页
   - 需实测：月度发售一览（`seolists`）、公司名（brand）、批評空間ID（game domain id）与日亚/DMM/getchu ID 的对应表（`game_infobox`?）
2. **字段映射**：EGS `gamename`（正式名/假名/TL名三种）vs getchu 登记名；brand vs 会社名；发售日 `sellday`
3. **磁链匹配适配**：nyaa 磁链 dn 是商品名（可能带特典后缀）——Phase3 的 dn 重标注逻辑（relabel.py）恰好可以按 EGS 本体名归一化，特典后缀只影响匹配不影响入库名
4. **表设计**：新表（如 `egs_games`）还是复用 `getchu_games` 加 source 列——建议新表 + source 字段，避免旧数据污染；getchu_115_folders 的 (date,name) 键语义要延续
5. **与 115 整理的衔接**：`organize_115.py` 依赖 `release_ts/date/name/company` 字段语义，新表需提供等价列或适配层

### 验收思路（延续 2026 测试口径）
- EGS 拉取 2026 年发售一览 → 与 getchu 2026 清单（8924行/4628链）对比覆盖率
- 抽查：進撃の巨人3 类商品在 EGS 是否单行（验证作品视角成立）
- 磁链匹配率不显著低于 getchu 路径

### 立即行动清单（新 session 开场）
1. 读本文档 + `docs/REFACTOR_PLAN.md`
2. `git checkout -b feature/egs-integration`（基于 develop）
3. 实测 EGS 数据端点（月度一览/作品详情/brand），确定抓取器形态
4. 出对接方案（表结构/字段映射/与 Phase2-4 复用边界）→ 用户确认后动手
