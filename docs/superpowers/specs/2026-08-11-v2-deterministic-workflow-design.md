# V2 确定性工作流设计

日期：2026-08-11  
状态：已实施

## 1. 决策

V2 由 LangGraph 承载确定性工作流，领域执行复用 DeepAgents Worker。LLM Planner
只输出结构化 `PlanDraft`，代码负责校验、依赖推进、前置条件、执行账本和结束判断。
复用 `scene-agent`、`entity-agent` 作为步骤执行器，对外只提供 `/api/v2`。
自由主 Agent 委派、V1 API 和兼容中间件不是目标架构的一部分。旧代码只有在承担 V2
明确职责时才复用，可以拆分或重命名，不为旧接口保留兼容层。

## 2. 分层与事实源

```text
User -> Planner -> PlanValidator -> RunRepository -> Scheduler
                                                    -> PreconditionEngine
                                                    -> AgentStepExecutor
                                                    -> FinalizationGuard
```

- `RunRepository` 是运行、步骤、等待上下文和执行账本的业务事实源，首版为独立 SQLite。
- LangGraph checkpointer 只保存图游标和 Agent interrupt 上下文。
- 通用工作流内核只识别 action、fact、dependency 和 status；领域约束来自
  `config/actions.yaml`，不得硬编码进 Scheduler。
- Cesium 副作用仍只能经 `StreamBridge` 发给前端并等待 HTTP 回告。
- 一个 `thread_id` 同时最多有一个非终态 Run 和一个活跃 SSE 流。

### 2.1 通用结果通道

V2 Worker 使用默认 `DeepAgentState`，不增加场景、查询、分析或报表等领域字段。结果按职责分层：

- `SceneContext` 只保存当前场景等持续有效的环境事实。
- `PlanStep.result` 保存规范化步骤结果，是后续步骤和前端展示的业务事实源。
- `ToolExecution.result` 保存原始工具回告，只用于审计、幂等和故障排查。
- 大型报告、图表和数据集通过 `StepResult.artifacts` 中的 `ArtifactRef` 引用，正文和二进制
  不写入 WorkflowRun。

下游步骤只能通过 `input_bindings` 中的明确 `ResultRef` 消费上游结果，禁止扫描“最近一次结果”
或按 action 名猜测来源。Planner 使用本计划内的 `source_ref`；PlanValidator 将其转换为可信
`source_step_id`，补充直接依赖并校验引用方向。结果路径使用 RFC 6901 JSON Pointer。

## 3. 状态和执行语义

Run 状态：`planning/running/waiting_user/succeeded/partially_succeeded/failed/cancelled`。

Step 状态：
`pending/ready/running/waiting_tool/waiting_user/succeeded/failed/blocked/skipped/cancelled`。

Scheduler 只领取依赖已完成、facts 已满足且没有活跃 attempt 的 ready step。必需步骤失败时，
所有依赖步骤转为 blocked，无依赖步骤可继续。FinalizationGuard 是唯一允许把 Run 置为终态的
组件；`waiting_user` 只结束当前 SSE，不结束 Run。

`scene.opened` 由 `SceneContext(status, scene_name, revision, verified_at)` 表示。
缺少场景时，工作流插入 `ensure_scene_context` 并等待用户选择打开或创建；前置步骤成功后，
原始待办自动恢复，不再询问是否继续。

工具调用指纹由 `step_id + tool_func + canonical_args + scene_revision` 构成。副作用调用使用稳定
`idempotency_key`；成功结果写入账本后不得再次执行。网络超时可以使用同一幂等键重发，业务失败
不得盲目重试。每步默认最多 8 次工具调用，同一失败指纹连续两次无进展则失败。

## 4. V2 协议

端点：

- `POST /api/v2/space/chat`：新建 Run；存在 waiting Run 时把输入作为续跑信息。
- `POST /api/v2/space/tool-result`：先持久化工具回告，再 resolve bridge future。
- `GET /api/v2/space/runs/{run_id}`：读取权威 Run Snapshot。
- `POST /api/v2/space/runs/{run_id}/resume`：恢复 waiting Run。
- `POST /api/v2/space/runs/{run_id}/cancel`：取消非终态 Run。

新增 SSE 事件为 `plan_snapshot/step_update/run_update`。所有 V2 事件包含
`thread_id/run_id/seq/revision/timestamp`；工具事件还包含
`step_id/execution_id/tool_call_id/idempotency_key`。工具生命周期保持
`tool_start -> tool_args -> tool_result -> tool_end`。`done/error` 仍是 SSE 终态；等待用户时先发
`interrupt`，再发 `done {interrupted:true}`。

前端必须按 `run_id + revision` 展示计划，按 `seq` 去重；按 `idempotency_key` 缓存 Cesium
副作用结果，收到重复请求时返回原结果而不重复执行。

Run Snapshot 和步骤事件中的 `PlanStep` 增加 `input_bindings`，`StepResult` 增加 `artifacts`。
持久化的 `WaitingContext` 通过 `result_ref` 指向等待步骤的结果；API/SSE 序列化时附加
`resolved_data` 方便前端展示，数据库不重复保存候选列表。场景选择 resume 的 `scene_name`
必须逐字符匹配候选结果，否则拒绝恢复。

## 5. 发布

- Worker 使用 `WorkerResponse + ToolStrategy` 生成单步骤结构化输出，再由 adapter
  统一为 `StepResult`。
- 首版固定 `deepagents==0.6.10`，0.7 升级单独评估。
- 首版仅定义 `StepExecutor`/`sandbox_policy` 扩展点，不实现脚本沙箱。
- 升级为断代切换，不保留 V1 URL 或自由主 Agent 会话恢复能力。

## 6. 实施证据

- 单一 V2 架构、工作流、API、Bridge、Repository、执行守卫及结果通道全量测试：
  `95 passed, 2 skipped`。
- `ruff check src/ tests/` 与 Python 源码格式检查通过。
- `uv lock --check --offline` 通过。

## 7. 结果通道加固与数据处置（2026-08-11）

V2 首版完成后追加本项加固，避免 Phase 1C 引入数据分析和报告能力时继续扩张领域 State。
本项直接删除 `scenario_query_results`：场景查询结果只经 ToolMessage 进入 Worker，再规范化为
StepResult；跨步骤消费统一经 ResultResolver。

产品尚未上线，历史 checkpoint 和 WorkflowRun 没有迁移价值。本次采用全新数据库启动：删除旧
`space_aiagent.db`、`workflow.db` 及其 WAL/SHM 文件，由 V2 首次启动重新建库。仓库不保留
字段级 checkpoint 迁移脚本、迁移模块或迁移测试。
