# AI 驱动的 115 整理工作台

## 1. 边界

EGS 磁链审核继续由用户在 `tool/egs.php` 完成。磁链提交到 115 以后，下载确认、目录命名、移动、合集归属、历史资源核对和结果验证交给 AI 整理代理。

规则代码只承担确定性判断和安全约束，不再尝试覆盖全部人工语义判断。AI 必须通过受约束的业务操作完成变更，不能把自由文本直接翻译成 SQL 或 115 删除请求。

## 2. 运行结构

```text
AI 整理工作台
    │ 创建后台 job
    ▼
AI Organizer Worker
    ├─ Responses API function-calling driver
    └─ Codex CLI driver（管理员后备）
    │
    ▼
pyGal 业务操作层
    ├─ 只读证据收集
    ├─ 带前置状态的原子操作
    ├─ 115 操作
    └─ 结果验证与备份
```

Web 请求只负责入队、停止和读取事件，不同步等待模型执行。每次任务写入 `egs_ai_jobs`，进度写入 `egs_ai_events`，结构化判断和动作分别写入 `egs_ai_decisions`、`egs_ai_actions`。

### 2.1 年度整理入口

`tool/year_organizer.php` 提供年度任务入口。首个入口固定为 2022 年：

- 页面只提交年份，不运行规则预筛选，也不生成供用户勾选的固定候选列表；
- AI 必须先分页读取 `/GAL/GAL-2022` 与 `/GAL/todo` 的 115 实时返回清单；
- AI 可继续读取目录子项、115 离线任务以及 2022 年 EGS 记录，自行判断归属、合集和重复；
- AI 选中记录后先调用确定性整理预览，只有无需人工确认的安全结果才能执行；
- 页面显示 AI API Provider 与 115 登录状态，并可完成 115 二维码登录。

## 3. Agent 输出协议

最终输出必须符合 `tool/ai_organizer_output.schema.json`：

- `outcome`: `completed | partial | blocked | failed`
- `summary`: 面向用户的结果摘要
- `requires_human`: 是否仍需要人工判断
- `evidence`: 实际使用的 EGS、InfoHash、CID、路径或文件证据
- `actions`: 动作、目标和验证结果

模型文本本身不构成执行授权。业务操作层必须再次校验动作目标和预期状态。

## 4. 自动执行范围

证据完整时可以自动完成：

- 检查 115 离线任务是否成功；
- 补记或回滚下载状态；
- 按种子日期和 EGS 公司/名称重命名；
- 在 `/GAL/GAL-YYYY` 范围内移动；
- 包装单文件资源、修复已知双层包装；
- 根据相同 InfoHash 或已确认的人工关系处理重复；
- 按既有约定将合集归给较新的续作或补丁记录；
- 执行后验证 CID、父目录、名称、映射和数据库完整性。

以下情况不猜测，转为 `requires_human=true`：

- 不同 InfoHash 但内容是否相同无法证明；
- 多个候选目录且 CID/文件证据冲突；
- 合集包含未确认的番外、特典或独立作品；
- 删除前仍存在独有文件；
- 目标在授权年份或 `/GAL` 范围之外。

## 5. Driver 策略

默认 driver 使用 Responses API function calling，把 115 实时清单、离线任务、EGS 查询、整理预览与安全执行作为明确函数工具暴露。Provider、Base URL、模型和 Key 沿用 phpAiAPI 的分离配置方式；Key 只在 worker 内存中读取，不进入页面、数据库或 Git。

本地 Codex CLI 保留为管理员后备，不再作为 Web 年度入口的运行前提。dsh 只用于调试和紧急接管，不作为业务状态来源。

## 6. 安全要求

- 一个时刻只运行一个写入型 AI 整理任务；
- 首次修改前创建任务级 `egs.db` 备份；
- 删除、覆盖和跨范围移动必须有精确目标及执行前状态；
- 每个动作都要留存参数、预期状态、结果和时间；
- 失败不得伪装为完成，安全项目可继续并以 `partial` 结束；
- Cookie、API Key、完整个人目录和运行会话不得写入 Git。

## 7. 迭代顺序

1. 后台任务、审计表和结构化输出；
2. Web 工作台的任务输入、状态和事件时间线；
3. Responses API function-calling driver 与 phpAiAPI Provider/Key 配置；
4. 115/EGS 只读工具及带前置状态的限权整理工具；
5. 扩充合集、重复目录等 AI 决策后的原子业务操作；
6. 将旧的逐条整理待办 UI 最终移除。
