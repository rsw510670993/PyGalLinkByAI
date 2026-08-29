# Getchu × Nyaa 匹配判断逻辑重构计划书（aigc2d AI 判定）

## 1. 背景与目标

### 1.1 背景
当前 `pyGal` 从 Getchu 抓取游戏后，使用固定关键词规则（`girlcelly`、`2D.G.F.`、发售月 `YYMM` 等）在 Nyaa 搜索结果中挑选磁力链接。随着游戏名称中“限定版 / 下载地址名 / 特典同梱版”等变化增多，纯规则匹配的误判、漏判越来越严重。

### 1.2 目标
- 新建工作分支 `feature/aigc2d-nyaa-match-refactor`，对 Getchu/Nyaa 获取对象后的“判断逻辑”做较大规模重构。
- 引入 aigc2d AI API 作为 Nyaa 候选项的判定核心，用自然语言理解能力识别“游戏本体 + 版本变化 + 下载名变化”。
- AI 通讯部分参照 `phpAiAPI` 工程：`config/key/aigc2d.key` 存 Key、OpenAI SDK 直连 `https://next.aigc2d.com/v1`、`gpt-5.6-luna` 作为默认模型。
- 保留规则判断作为降级兜底，避免 AI 不可用时整条链路中断。

## 2. 现状分析

### 2.1 现有流程
1. `get_raw_getchu_games(year, month)`：抓取 Getchu 月榜页，按 `config.skip` 过滤“再販 / 普及版 / 廉価版 / ヌキコレ”。
2. `deduplicate_games(raw_games)`：按 `config.delete` 删除“初回版 / 限定版 / 豪华版”等版本词，再按公司名 + 名称长度排序去重。
3. `get_all_getchu_games`：入库到 `getchu_games`。
4. `download_games_by_month` / `get_download_link`：
   - `get_nyaa_data(game_name, company)` 请求 sukebei.nyaa.si 搜索并解析成 `NyaaData` 列表。
   - 通过多层 `next(...)` 按“girlcelly + YYMM / 2D.G.F. + YYMM / YYMM / 第一条”挑选。
5. `set_downloaded_status`、`update_game_record` 等负责落库与后续 115 校验。

### 2.2 当前判断逻辑的具体缺陷
1. **版本词硬编码**：`config.delete` 中的“限定版、豪華版”等词条无法穷举，且错误地使用 `in` 匹配会误删正常名称中的片段。
2. **Nyaa 侧名称变化多**：下载名可能是罗马音、缩写、被裁剪的标题、自购/自炊标记、特典同梱名等，`girlcelly` / `2D.G.F.` 只覆盖少量发布者习惯。
3. **优先级规则脆弱**：`next(...)` 链条一旦命中“含 YYMM 但实际不是同一游戏”的候选项，就会直接选错，且没有置信度记录。
4. **结果不可审计**：命中/未命中没有记录原因，误判无法回溯。
5. **`get_download_link` 存在现网 bug**：`get_nyaa_data(game_name)` 少传了 `company` 参数，会抛 `TypeError`；该函数逻辑与 `download_games_by_month` 重复，需要一并重构。
6. **去重误伤**：`deduplicate_games` 使用 `key.rsplit(" ", 1)[0]` 去重，容易把同一公司的系列作合并。

## 3. 重构方案

### 3.1 总体架构

```
Getchu 抓取 → 清洗/去重 → getchu_games
                              ↓
Nyaa 搜索 → 候选列表(NyaaData) → 规则初筛(可选) → AI 判断(核心) → 落库
                              ↓                        ↓
                        强规则直判               AI 异常时降级规则
```

### 3.2 新增模块

- `tool/ai_matcher.py`
  - `Aigc2dConfig`：读取 `tool/config.json` 的 `aigc2d` 段 + `tool/config/key/aigc2d.key`。
  - `Aigc2dClient`：封装 OpenAI SDK 调用（**照搬 phpAiAPI 的通讯方式**）。
  - `build_match_prompt(game, candidates)`：构造 system/user prompt。
  - `parse_match_response(content)`：解析并校验 AI 返回的 JSON。
  - `judge_nyaa_match(game, nyaa_data_list)`：对外统一入口，返回 `MatchResult`。
  - `fallback_rule_match(game, nyaa_data_list)`：现有规则重构后的降级实现。
  - `maybe_refresh_model_catalog()`：可选，参照 phpAiAPI `admin_get_aigc2d_models` 拉取 `/models`，缓存模型目录。

### 3.3 判断流程

1. 从 DB 取出一条未匹配的 `GetchuGame`。
2. 调用 `get_nyaa_data(game_name, company)` 得到候选列表。
3. 对候选列表做**规则初筛**（保留强信号，不直接决定）：
   - 计算每个候选与 Getchu 名称的归一化相似度（去版本词后的编辑距离/字符重合度）。
   - 提取候选名称中的 `YYMM`、发布者、文件大小等结构化信号。
4. 若候选 ≤ 1：直接走旧逻辑或留空，不调用 AI。
5. 若存在**强规则命中**（例如归一化后完全相等且日期吻合）：可以直接判定，减少 AI 调用量。
6. 其余情况调用 `judge_nyaa_match`：
   - 将 Getchu 字段 + 候选列表（最多 10 条，超出的按日期/相似度截断）作为上下文。
   - 要求 AI 返回严格 JSON：`{"matched_index": 0, "confidence": 0.0, "reason": "...", "matched_name": "..."}`。
   - `matched_index` 为候选列表下标；`confidence` 为 0~1；无匹配时 `matched_index = -1`。
7. 根据 `confidence` 落库：
   - `confidence >= 0.7`：更新 `link / nyaa_name / size`，记录 `match_source='ai'`。
   - `0.4 <= confidence < 0.7`：更新链接，但标记 `need_manual_review=1`。
   - `confidence < 0.4` 或 AI 失败：使用规则降级结果，标记 `match_source='rule'`。
8. 所有 AI 调用写日志和 `ai_match_log`，失败时保留原始候选信息，方便人工修正。

### 3.4 数据模型变更

`getchu_games` 新增列：
- `match_source TEXT`：`ai` / `rule` / `manual` / `none`
- `match_confidence REAL`
- `match_reason TEXT`
- `need_manual_review INTEGER DEFAULT 0`
- `candidate_count INTEGER`

新增表 `ai_match_log`：
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `date TEXT`、`name TEXT`、`company TEXT`
- `candidates_json TEXT`（候选列表快照）
- `request_json TEXT`（AI 请求载荷，脱敏后不含 Key）
- `response_json TEXT`
- `selected_index INTEGER`、`confidence REAL`
- `success INTEGER`、`error_message TEXT`
- `created_at TEXT DEFAULT (datetime('now','localtime'))`

迁移沿用 `ensure_getchu_schema` 的 `ALTER TABLE + PRAGMA user_version` 模式。

### 3.5 CLI / API / UI 变更

- `tool/cli.py` 新增命令：
  - `match month --year YYYY --month MM [--dry-run] [--limit N] [--force-ai]`：对指定月份执行匹配判断。
  - `match status`：查看匹配任务进度。
  - `match approve --date ... --name ... --candidate-index N`：人工确认。
- `tool/api.php` 新增 action：
  - `match_status` / `start_match` / `stop_match`
  - `manual_match`：人工指定候选。
- `tool/data.php` / 前端：
  - 显示 `match_source`、置信度、候选数、`need_manual_review` 标记。
  - 需要人工复核的记录置顶显示。
- `download_games_by_month` / `get_download_link` 统一改为调用 `judge_nyaa_match`，删除重复代码。

## 4. AI 通讯设计（参照 phpAiAPI）

### 4.1 对照表

| 项目 | phpAiAPI 现实现 | pyGal 本次采用 |
| --- | --- | --- |
| 配置文件 | `config/global.ini` 的 `[aigc2d]` 段 | `tool/config.json` 的 `"aigc2d"` 段 |
| API Key | `config/key/aigc2d.key` | `tool/config/key/aigc2d.key`（同目录结构） |
| base_url | `https://next.aigc2d.com/v1` | `https://next.aigc2d.com/v1` |
| 默认模型 | `gpt-5.6-luna` | `gpt-5.6-luna` |
| SDK | Python `openai` 包 | Python `openai` 包 |
| 客户端 | `OpenAI(api_key=..., base_url=...)` | 同左 |
| 调用方法 | `client.chat.completions.create(...)` | 同左 |
| 请求参数 | `model`, `messages`, `stream=False`, `timeout`, `max_tokens` | 同左 |
| 温度/tools | 默认不传；报错时自动降级重试 | 本任务不传，保持简单 |
| 模型目录 | `GET {base_url}/models`（Bearer），`GET {origin}/api/pricing` | 可选实现，缓存到 `status/aigc2d_models.json` |

### 4.2 配置示例

`tool/config.json` 增加：

```json
{
  "aigc2d": {
    "base_url": "https://next.aigc2d.com/v1",
    "default_model": "gpt-5.6-luna",
    "max_tokens": 1024,
    "timeout": 60,
    "max_candidates": 10,
    "confidence_threshold": 0.7,
    "review_threshold": 0.4
  }
}
```

Key 文件：`tool/config/key/aigc2d.key`，内容为纯 Key 字符串（与 phpAiAPI 一致，读取时 `trim` 掉引号/空白）。Nginx 需增加对该文件的拒绝规则。

### 4.3 调用实现要点

```python
from openai import OpenAI

client = OpenAI(api_key=api_key, base_url=base_url)
kwargs = {
    "model": model_name,
    "messages": messages,
    "stream": False,
    "timeout": float(timeout_seconds),
    "max_tokens": max_tokens,  # 注意：只有 openaiapi 供应商才改用 max_completion_tokens
}
response = client.chat.completions.create(**kwargs)
content = response.choices[0].message.content
```

### 4.4 错误处理与降级

- 参照 phpAiAPI `chat.py` 的 3 次重试；网络/超时错误退避重试。
- API 返回非 JSON 或 JSON 校验失败：重试一次，再失败则回退规则判断。
- HTTP 4xx/5xx：记录错误到 `ai_match_log`，回退规则判断，不阻塞后续任务。
- 同一 `(game_date, game_name, nyaa_name_hash)` 的结果可短时缓存（本次重构可选，后续优化）。

## 5. Prompt 设计

### 5.1 System Prompt

> 你是成人游戏（美少女ゲーム/エロゲ）磁力链接匹配专家。用户会给出 Getchu 游戏信息和若干 Nyaa 候选。请根据标题、公司、发售日期、文件大小和候选命名习惯，判断哪个候选最可能是该游戏本体的下载链接。注意：候选名常包含 限定版/特典/自炊/自购/罗马音/缩写/下载站命名 等变化。只返回 JSON，不要输出其他内容。

### 5.2 User Prompt 模板

```text
Getchu 信息：
- 发售月份：2026-08
- 游戏名：...
- 公司：...

Nyaa 候选（下标从 0 开始）：
[0] 名称: ... | 大小: ... | 日期: ...
[1] ...

请返回严格 JSON：
{"matched_index": 0, "confidence": 0.0, "reason": "简短说明", "matched_name": "候选名称"}
无匹配时 matched_index = -1，confidence = 0。
```

### 5.3 解析与校验

- 使用 `json.loads` 解析，允许剔除首尾的 Markdown 代码块围栏。
- 校验 `matched_index` 必须是 `-1` 或合法候选下标。
- `confidence` 必须为 0~1 数字，越界时截断。
- 校验失败即视为 AI 失败，走规则降级。

## 6. 实施步骤

### 阶段 1：基础设施
1. `tool/requirements.txt` 增加 `openai`。
2. `tool/config.json` 增加 `aigc2d` 段。
3. 新建 `tool/config/key/aigc2d.key`（从 phpAiAPI 复制 Key），并加入 nginx 拒绝规则。
4. 新建 `tool/ai_matcher.py`，先实现 `Aigc2dClient` 和最小可用测试脚本。

### 阶段 2：AI 判断核心
1. 实现 `build_match_prompt` / `parse_match_response` / `judge_nyaa_match`。
2. 实现 `fallback_rule_match`（旧规则重构）。
3. 编写单元测试（使用假响应，不实际请求 API）。

### 阶段 3：数据库与主流程接入
1. `ensure_getchu_schema` 增加新列和新表，升级 `PRAGMA user_version`。
2. 重构 `download_games_by_month`，调用 `judge_nyaa_match`。
3. 删除/重写 `get_download_link`，修复缺 `company` 参数 bug，统一逻辑。
4. `spider_worker.py` / `download_worker.py` 状态文件增加 `match` 相关字段。

### 阶段 4：CLI/API/UI
1. `tool/cli.py` 增加 `match` 子命令。
2. `tool/api.php` 增加匹配相关 action。
3. 前端展示 AI 置信度、匹配来源、人工复核入口。

### 阶段 5：灰度与切换
1. 先用 `--dry-run` 对最近 3 个月跑 AI 判断，人工抽查准确率。
2. 对比旧规则与 AI 结果差异，确认无误后默认启用 AI。
3. 保留旧规则作为降级，监控日志与 `ai_match_log`。
4. 更新 README 与部署脚本。

## 7. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| AI API 不可用/限流 | 匹配任务中断 | 自动重试 3 次，失败回退规则；队列继续 |
| AI 返回格式不稳定 | 判定失败 | 解析容错 + JSON 校验 + 二次重试 + 规则兜底 |
| 误判 | 链接错误 | `confidence < 0.7` 标记人工复核；保留候选快照 |
| 成本/调用量 | API 费用 | 强规则命中不调 AI；候选数截断；后续按需缓存 |
| 旧逻辑回归 | 匹配率下降 | 规则降级路径保持可用；灰度阶段对比 |
| Key 泄露 | 安全 | Key 文件放在 `tool/config/key/`，nginx 拒绝；不写入 DB/日志 |

## 8. 验收标准

1. `tool/ai_matcher.py` 可独立运行并成功调用 aigc2d 返回合法 JSON。
2. `download_games_by_month` 对单月执行后，每条记录均写入 `match_source` 与 `match_confidence`。
3. AI 不可用时，任务可完成并走规则降级，不中断。
4. `get_download_link` 旧 bug 修复，且与 `download_games_by_month` 共用同一判断入口。
5. 人工复核入口可用，能手动指定候选并写回 `link / nyaa_name / match_source='manual'`。
6. 最近 3 个月灰度测试，AI 判断准确率优于旧规则（以人工抽查 100 条为准）。

## 9. 文件改动清单

- 新增：`tool/ai_matcher.py`
- 新增：`docs/plan-aigc2d-match-refactor.md`（本计划书）
- 修改：`tool/core.py`（判断入口重构、schema 迁移）
- 修改：`tool/models.py`（增加 `MatchResult` 数据类）
- 修改：`tool/cli.py`（`match` 子命令）
- 修改：`tool/api.php`（匹配接口）
- 修改：`tool/data.php`、`index.php`（前端展示与复核入口）
- 修改：`tool/config.json`（`aigc2d` 配置）
- 修改：`tool/requirements.txt`（增加 `openai`）
- 修改：`README.md`（配置说明与使用方式）
- 新增：`tool/config/key/aigc2d.key`（实际部署时写入，不提交到 git）
