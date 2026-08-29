# pyGAL 业务流程重规划：按年份抓取 Getchu（标题 / 制作公司 / 发售日 / 缩略图）

## 1. 背景与目标

### 1.1 背景
当前流程以「月份」为最小抓取单位，只从 `price.html` 月榜页拿到三个字段：
- `date`：仅精确到月（`YYYY-MM`）
- `name`：游戏标题
- `company`：制作公司

列表页上还有 **详情页链接（`soft.phtml?id=xxxxxxx`）**、**精确发售日（MM/DD）**、**媒体类型**、**价格** 等信息没有被利用；详情页上的 **包装缩略图（package 画像）** 与 **精确发售日（`YYYY/MM/DD`）** 更是完全没有入库。

### 1.2 目标
业务流程改为「**选年份 → 抓 Getchu → 标题 + 制作公司 + 发售日 + 缩略图**」：

1. 用户在 Web 界面只需选择一个**年份**，一次完成全年 12 个月的数据抓取。
2. 在现有 `标题 / 制作公司` 基础上，新增抓取：
   - **发售时间**（精确到日：`YYYY-MM-DD`）
   - **缩略图**（包装画像，本地缓存，前端直接展示）
3. 保留现有 Nyaa AI 匹配、115 校验链路，作为抓取后的下游环节。

## 2. 现状分析（实测）

### 2.1 列表页 `https://www.getchu.com/all/price.html?genre=pc_soft&year={Y}&month={M}`
（需 Cookie `getchu_adalt_flag=getchu.com`，编码 EUC-JP）

每行 9 列，实测结构：

| 列 | 内容 | 示例 |
|---|---|---|
| 0 | 锚点 + 日（MM/DD） | `08/28` |
| 1 | 标题 + 详情页链接 | `../soft.phtml?id=1331107` → 制服カノジョ 2.5 |
| 2 | 制作公司（品牌） | エンターグラム |
| 3 | 媒体 | DVD-ROM |
| 4 | 价格 | ￥3,000 (税込￥3,300) |
| 7 | 库存状态 | 在庫なし |

结论：**列表页可直接拿到 getchu_id、精确日、标题、公司、媒体、价格；无缩略图。**

### 2.2 详情页 `https://www.getchu.com/soft.phtml?id={id}`
（同样 Cookie + EUC-JP）

| 字段 | 示例 |
|---|---|
| ブランド（制作公司） | エンターグラム |
| 定価 | ￥3,000 (税込￥3,300) |
| **発売日** | **2025/08/28** |
| メディア | DVD-ROM |
| JANコード / 品番 | 4935066509722 / EGCS-00321 |
| **包装缩略图** | `/brandnew/1331107/rc1331107package.jpg`（约 27KB） |

### 2.3 关键技术约束（实测）
1. **缩略图有防盗链**：直接 GET 图片 URL 且不带 `Referer` 头会返回 **403**。
   → **不能在前端 `<img>` 热链**（浏览器 referer 不是 getchu.com）。
   → **必须由 Python 侧带 `Referer: https://www.getchu.com/soft.phtml?id={id}` 下载到本地缓存目录**，前端只访问本地文件。
2. 列表页日期锚点为 `MM/DD`，需与 `year/month` URL 参数合成完整 `YYYY-MM-DD`。
3. 详情页需要逐个请求：一年约 400~600 款游戏，必须限速 + 断点续跑。

## 3. 新业务流程

```
[Web] 选择年份
        │  api.php?action=spider_start (start_year=end_year=选中年)
        ▼
┌─ 阶段1：列表抓取（快，12 次请求）────────────────┐
│ price.html?year=Y&month=1..12                     │
│ 提取: getchu_id / 标题 / 公司 / YYYY-MM-DD /      │
│       媒体 / 价格 → UPSERT 入库 (date,name)       │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─ 阶段2：详情补全（慢，限速 1.5s/个，可中断续跑）─┐
│ 对 detail_fetched=0 的记录逐个:                   │
│  1. GET soft.phtml?id={id}                        │
│     → 校正品牌 / 精确発売日 / 定価 / メディア     │
│  2. 带 Referer 下载缩略图 → thumbnails/{id}.jpg   │
│  3. 置 detail_fetched=1；失败置 2 并记录重试次数  │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─ 阶段3：下游链路（保持现状，不变）──────────────┐
│ Nyaa 搜索 → AI 匹配 → 磁链入库 → 115 校验        │
└──────────────────────────────────────────────────┘
```

- 阶段 1 与阶段 2 在同一个 `spider_worker` 进程内顺序执行（一个任务链，前端一个进度条即可）。
- 阶段 2 支持**断点续跑**：靠 `detail_fetched` 标记，进程被杀后重跑自动跳过已完成项。
- 闲时任务 `idle_run` 同步适配：先列表、后详情、再匹配。

## 4. 数据模型变更（`getchu_games`）

新增列（`ensure_getchu_schema` 迁移，全部可空，兼容旧数据）：

```sql
ALTER TABLE getchu_games ADD COLUMN getchu_id       TEXT;  -- Getchu 详情页 ID（软唯一键）
ALTER TABLE getchu_games ADD COLUMN release_date    TEXT;  -- 精确发售日 YYYY-MM-DD
ALTER TABLE getchu_games ADD COLUMN thumb_url       TEXT;  -- 原始缩略图 URL
ALTER TABLE getchu_games ADD COLUMN thumb_path      TEXT;  -- 本地缓存相对路径 thumbnails/{id}.jpg
ALTER TABLE getchu_games ADD COLUMN price           TEXT;  -- 定価（含税价一并存储）
ALTER TABLE getchu_games ADD COLUMN detail_url      TEXT;  -- 详情页 URL
ALTER TABLE getchu_games ADD COLUMN detail_fetched  INTEGER DEFAULT 0; -- 0未抓 1成功 2失败
ALTER TABLE getchu_games ADD COLUMN detail_retry    INTEGER DEFAULT 0;

-- getchu_id 唯一性（部分索引，SQLite 支持）
CREATE UNIQUE INDEX IF NOT EXISTS idx_getchu_games_gcid
  ON getchu_games(getchu_id) WHERE getchu_id IS NOT NULL;
```

- 保留现有主键 `(date, name)` 与所有旧列（`size`/`link`/`downloaded`…），**下游 AI 匹配、115 校验零改动**。
- 列表页 `date` 直接升级为 `YYYY-MM-DD`（主键含 date，旧记录为 `YYYY-MM` 不冲突；同月同名的再版行可按 getchu_id 去重合并）。
- `media` 复用现有 `size` 列语义不清晰，新增数据写入 `size` 保持展示兼容（DVD-ROM 等）。

## 5. 抓取实现要点

### 5.1 列表抓取（重构 `get_raw_getchu_games`）
```python
def get_raw_getchu_games(year, month):
    # 现有 BeautifulSoup 逻辑保留，新增：
    a = columns[1].find('a', href=re.compile(r'soft\.phtml\?id=(\d+)'))
    getchu_id = m.group(1) if (m := re.search(r'id=(\d+)', a['href'])) else None
    day_text  = columns[0].get_text(strip=True)          # "08/28"
    release_date = f"{year}-{month:02d}-{day_text}"      # 合成精确日
    media     = columns[3].get_text(strip=True)
    price     = columns[4].get_text(" ", strip=True)
```

### 5.2 详情补全（新增 `tool/getchu_detail.py`）
```python
HEADERS = {"Referer": f"https://www.getchu.com/soft.phtml?id={gid}"}

def fetch_detail(gid):
    # 1. 解析信息表: ブランド / 定価 / 発売日 / メディア (按 td 文本定位 "発売日：" 取 next_sibling)
    # 2. 解析包装图: img[alt*="パッケージ画像"] → /brandnew/{gid}/rc{gid}package.jpg
    # 3. 下载缩略图: requests.get(img_url, headers=HEADERS) → thumbnails/{gid}.jpg
    # 4. 全程 time.sleep(1.5) 限速；失败 detail_fetched=2
```

### 5.3 状态上报（`status/spider_status.json` 扩展）
```json
{
  "phase": "listing | detail | done",
  "progress": 0.62,
  "current_year": 2025,
  "current_game": "制服カノジョ 2.5",
  "detail_done": 213, "detail_total": 402, "detail_failed": 3
}
```

## 6. CLI / API 变更

| 层 | 变更 |
|---|---|
| `cli.py` | `spider_start` 增加逻辑：抓完当年 12 月后自动进入 detail 阶段（无新参数，行为内聚）；新增 `getchu detail_retry` 手动重跑失败项 |
| `api.php` | `spider_start/status/stop` 不变；`spider_status` 透出 `phase/detail_done/detail_total` |
| `index.php` / `data.php` | 选择器从「年+月」改为「年份」一键抓取；列表卡片增加缩略图与精确发售日 |
| `calendar.php` | 日视图直接使用 `release_date`，按日聚合 |
| nginx | 放行 `/thumbnails/` 目录（不能加入现有拒绝列表） |

## 7. 存量数据回填（可选）

旧记录无 `getchu_id`、无缩略图：
- 提供 `python tool/cli.py getchu backfill --year 2024`：对指定年份按「公司+标题」在 Getchu 搜索页定位详情页回填（成功率受搜索接口限制，允许部分失败）。
- 或接受旧数据保持现状（无缩略图），仅新抓取年份具备完整信息。**推荐后者起步**，回填作为独立后续任务。

## 8. 实施步骤

1. **迁移**：`ensure_getchu_schema` 增加上述列与索引（幂等）。
2. **列表抓取**：改造 `get_raw_getchu_games` / `deduplicate_games`（getchu_id 优先去重），入库 `release_date/media/price/detail_url`。
3. **详情补全**：新建 `tool/getchu_detail.py`（解析 + 缩略图下载 + 限速 + 断点续跑），接入 `spider_worker` 阶段 2。
4. **状态**：`spider_status.json` 增加 phase 与 detail 进度。
5. **前端**：`index.php` 年份选择、缩略图与发售日展示；`calendar.php` 按日聚合。
6. **闲时任务**：`idle_run` 适配两阶段流程。
7. **回归**：跑 2025 年验证（列表 12 请求 + 详情约 500 请求 ≈ 15~20 分钟），确认旧年份页面不受影响。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 缩略图防盗链 403 | Python 带 Referer 下载到本地；下载失败不阻塞主流程（detail_fetched=2） |
| 详情页请求量大被封 | 1.5s 限速 + 断点续跑 + 失败退避重试；单年一次抓完后不再重复请求 |
| 同名同月多版本（主键冲突） | getchu_id 唯一索引去重；无 id 的行维持旧行为 |
| 旧数据与新数据 date 格式混用 | `date` 列统一按字典序排序天然兼容（`YYYY-MM` < `YYYY-MM-DD`）；展示层格式化处理 |
| Getchu 结构变动 | 详情解析按 td 文本定位（発売日：/ブランド：），结构微调不易崩；解析失败计数告警 |
