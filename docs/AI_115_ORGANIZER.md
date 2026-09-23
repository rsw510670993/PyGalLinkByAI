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
    ├─ Codex CLI driver（首版）
    ├─ Codex app-server driver（后续）
    └─ Responses API driver（后续）
    │
    ▼
pyGal 业务操作层
    ├─ 只读证据收集
    ├─ 带前置状态的原子操作
    ├─ 115 操作
    └─ 结果验证与备份
```

Web 请求只负责入队、停止和读取事件，不同步等待模型执行。每次任务写入 `egs_ai_jobs`，进度写入 `egs_ai_events`，结构化判断和动作分别写入 `egs_ai_decisions`、`egs_ai_actions`。

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

首版使用本地 `codex exec --json --output-schema`，后台 worker 保存线程 ID、事件、结果和任务前数据库备份。Codex 使用 `workspace-write` 且不允许交互式批准；Skill 明确禁止改源码和 Git。

正式版应优先迁移到 Codex app-server 或 Responses API function calling，把 115/数据库能力作为明确的函数工具暴露。dsh 保留为管理员调试和紧急接管入口，不作为业务状态的唯一来源。

## 6. 安全要求

- 一个时刻只运行一个写入型 AI 整理任务；
- 首次修改前创建任务级 `egs.db` 备份；
- 删除、覆盖和跨范围移动必须有精确目标及执行前状态；
- 每个动作都要留存参数、预期状态、结果和时间；
- 失败不得伪装为完成，安全项目可继续并以 `partial` 结束；
- Cookie、API Key、完整个人目录和运行会话不得写入 Git。

## 7. 迭代顺序

1. 后台任务、审计表、结构化输出和 Codex driver；
2. Web 工作台的任务输入、状态和事件时间线；
3. 将现有整理动作收口为带预期状态的限权工具；
4. 接入 app-server 的持续会话和流式工具事件；
5. 增加 Responses API driver，沿用 phpAiAPI 的 Provider/Key 配置方式；
6. 将旧的逐条整理待办 UI 降级为只读兼容页并最终移除。
