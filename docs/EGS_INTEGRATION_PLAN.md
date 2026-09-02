# EGS（ErogameScape）情报源接入方案与实施状态

> 分支：`feature/egs-integration`
> 日期：2026-09-02
> 用户已确认范围：**只抓 PC + 18禁；新建独立 DB；完全新爬；最终放弃 getchu 老数据**
> 当前状态：第一阶段已落地——`egs.db` + `egs_games` 表 + EGS PC 18禁 2026 爬取入库完成
> 未做：EGS 磁链/下载链接获取、搬月处理、115/Web 接入（等后续阶段）

---

## 1. EGS 数据端点实测

### 1.1 可用端点（均已实测返回 200）

| 端点 | 方法/参数 | 结果形态 | 说明 |
|---|---|---|---|
| `https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/sql_for_erogamer_form.php` | POST，表单字段 `sql` | HTML：`QUERY PLAN` + 结果 `<table>` | 当前实际使用入口 |
| `https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/select.php` | POST，表单字段 `SQL` | HTML：`QUERY PLAN` + 结果 `<table>` | Codex 给的旧入口，也可用 |
| `https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/toukei_hatubaibi_month.php?year=2026&month=01` | GET | HTML 发售列表 | 人工浏览用，不适合精确过滤 |
| `https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/game.php?game=39637` | GET | 作品详情 HTML | 后续可补充详情 |

要点：
- 无官方 JSON API；SQL 表单返回 HTML 表格。
- 抓取只拼受控年/月，固定 `SELECT` 列，不拼接外部输入。
- 返回 bool 为 `t`/`f`，入库转 `1/0`。
- 2026 全年 PC 18禁约 412 行；按月请求稳定。

### 1.2 官方公开表

- `gamelist`：主表。含 `id`、`gamename`、`furigana`、`sellday`、`brandname`(FK)、`model`、`erogame`、`comike`、`dmm`、`dlsite_id`、`genre`、`shoukai`、`tourokubi` 等。
- `brandlist`：品牌表。含 `id`、`brandname`、`kind`（`CORPORATION`/`CIRCLE`）等。

### 1.3 2026 数据量实测

| 范围 | 数量 |
|---|---|
| 全部 model | 约 658 |
| `model='PC'` | 约 568 |
| `model='PC' AND erogame=true` | **412**（已按此入库） |
| PC eroge + `brand_kind='CORPORATION'` | 约 189 |

注意：EGS 不是 getchu 的 1:1 超集；`進撃の巨人3`、`三國志8 REMAKE …`、`Winning Post 10 2026` 等一般商业 PC 游戏在 EGS 查不到。用户已确认只做 EGS 覆盖范围内的 PC 18禁，因此不追平 getchu 全量。

### 1.4 “作品视角”验证

- EGS 会按 `model` 拆行（NS/PS/PC），但 PC 档内不拆特典/店铺限定/TREASURE BOX。
- 2026 PC 档 `gamename` 基本唯一。
- 因此本方案限定 `model='PC' AND erogame=true`，不需要 getchu 那套 AI 去重。

---

## 2. 已确认抓取范围与 SQL

已确认范围：

```text
g.model = 'PC'
AND g.erogame = true
```

未排除 `CIRCLE` 同人，因为用户只要求 PC+18禁。

实际抓取 SQL（按月）：

```sql
SELECT
  g.id                                  AS egs_id,
  g.gamename                            AS egs_name,
  g.furigana                            AS name_kana,
  g.sellday                             AS egs_date,
  b.id                                  AS brand_id,
  b.brandname                           AS egs_company,
  b.kind                                AS brand_kind,
  g.model                               AS model,
  g.erogame                             AS erogame,
  g.comike                              AS getchu_id,
  g.dmm                                 AS dmm,
  g.dlsite_id                           AS dlsite_id,
  g.genre                               AS genre,
  g.shoukai                             AS official_url,
  g.tourokubi                           AS registered_at
FROM gamelist g
LEFT JOIN brandlist b ON g.brandname = b.id
WHERE g.sellday >= 'YYYY-MM-01'
  AND g.sellday <  'YYYY-MM-01'::date + interval '1 month'
  AND g.model = 'PC'
  AND g.erogame = true
ORDER BY g.sellday, g.id
```

---

## 3. 字段映射

| 本地字段 | EGS 来源 | 说明 |
|---|---|---|
| `egs_id` | `gamelist.id` | 主键/源 ID |
| `egs_date` | `sellday` | 原始发售日（身份层，不因搬月改变） |
| `egs_name` | `gamename` | 原始 EGS 名（身份层） |
| `egs_company` | `brandlist.brandname` | 原始品牌名（身份层） |
| `date` | `substr(egs_date,1,7)` | 展示月/日历桶 |
| `name` | `egs_name` | 展示名，初始同原始 |
| `company` | `egs_company` | 展示公司，初始同原始 |
| `release_ts` | `egs_date` | 精确发售日，后续搬月时按真实磁链日期调整 |
| `name_kana` | `furigana` | 注音 |
| `brand_id` | `brandlist.id` | 品牌 ID |
| `brand_kind` | `brandlist.kind` | `CORPORATION`/`CIRCLE` |
| `is_eroge` | `erogame` | 本次恒为 `1` |
| `getchu_id` | `comike` | 仅作旧数据对照，不参与新流程 |
| `dmm` | `dmm` | 外部 ID |
| `dlsite_id` | `dlsite_id` | 外部 ID |
| `genre` | `genre` | 官方ジャンル |
| `official_url` | `shoukai` | OHP |
| `registered_at` | `tourokubi` | EGS 收录日 |
| `link/nyaa_name/size/downloaded/...` | 预留 | EGS 下载链接阶段再回填 |

---

## 4. 已落地表结构：`egs.db`

新 DB 文件：`egs.db`（与 `getchu.db` 分离）。

```sql
CREATE TABLE egs_games (
    egs_id          INTEGER PRIMARY KEY,
    source          TEXT    NOT NULL DEFAULT 'egs',
    model           TEXT    NOT NULL,
    -- 原始身份层
    egs_date        TEXT    NOT NULL,
    egs_name        TEXT    NOT NULL,
    egs_company     TEXT    NOT NULL,
    -- 展示/日历层
    date            TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    release_ts      TEXT,
    name_kana       TEXT,
    brand_id        INTEGER,
    brand_kind      TEXT,
    is_eroge        INTEGER NOT NULL DEFAULT 1,
    -- 对照/外部 ID
    getchu_id       TEXT,
    dmm             TEXT,
    dlsite_id       TEXT,
    genre           TEXT,
    official_url    TEXT,
    registered_at   TEXT,
    -- 磁链/115 后续阶段预留
    size            TEXT,
    link            TEXT,
    nyaa_name       TEXT,
    comment         TEXT,
    downloaded      INTEGER NOT NULL DEFAULT 0,
    infohash_hex    TEXT,
    submitted_115   INTEGER NOT NULL DEFAULT 0,
    submitted_pick_code TEXT,
    fetched_at      TEXT,
    updated_at      TEXT
);

CREATE UNIQUE INDEX idx_egs_games_date_name ON egs_games(date, name);
CREATE INDEX idx_egs_games_release_ts ON egs_games(release_ts);
```

说明：
- `egs_id` 作为稳定主键，方便后续改名/搬月仍能定位原行。
- `(date,name)` 唯一索引继续保留旧流程的键语义，给后续磁链/目录表复用。
- 保留 `egs_date/egs_name/egs_company` 身份层，专门为“搬月”场景准备：即使展示 `date/name/company` 改变，原始 EGS 信息不丢。

---

## 5. 实施进度

### 已完成
- [x] 新建 `feature/egs-integration` 分支
- [x] 实测 EGS SQL/HTML/详情端点
- [x] 新建独立 `egs.db`（默认仓库根目录）
- [x] 实现 `tool/egs_core.py`：建表、按月拉取、HTML 解析、按 `egs_id` 幂等 upsert
- [x] 在 `tool/cli.py` 增加 `egs crawl` / `egs status`
- [x] 抓取 2026 全年 PC+18禁：**412 行**已入库，重复执行不产生写入
- [x] `.gitignore` 增加 `egs.db*`

### 尚未做（按用户确认留到后续）
- [ ] EGS 下载链接获取（nyaa 等）
- [ ] EGS 搬月/重标注逻辑
- [ ] 115 整理新规
- [ ] Web/calendar 切换
- [ ] 最终清理/冻结 getchu 老数据

---

## 6. 已确认决策记录

1. 范围：只抓 `PC + 18禁`。
2. 数据库：新建独立 DB（`egs.db`），不复用 `getchu.db`。
3. 旧 getchu 数据：不考虑迁移，完全新爬；最终目标放弃 getchu 老数据。
4. 下载链接：后续再做 EGS 的磁链获取，但同样要考虑搬月情况。
5. 下游：全部新规，最终以 EGS 体系替代 getchu 体系。
