# space-aiagent 仓库说明

本项目为 Cesium 航天 GIS 平台提供确定性多 Agent 助手。当前只有 V2 架构：
外层 LangGraph 工作流控制顺序和状态，内层 DeepAgents Worker 执行单个领域步骤。

## 主链路

```text
POST /api/v2/space/chat
  -> Worker Todo Planner -> PlanValidator -> WorkflowRun
  -> Scheduler / dynamic requirement insertion
  -> AgentStepExecutor -> scene-agent / entity-agent
  -> StreamBridge -> Cesium
  -> POST /api/v2/space/tool-result
  -> Execution Ledger -> FinalizationGuard -> Finalizer
```

## 不可破坏的约束

- 协议优先：修改 SSE、工具回告、Run/Step 模型时先更新设计和前端文档。
- 系统只提供 `/api/v2/space`；不恢复 V1 路由、自由主 Agent 或兼容中间件。
- Planner 只按 Worker 拆分自然语言 Todo，不生成 action、工具、参数或运行状态。
- Scheduler、动态前置依赖、失败传播和结束判断必须是代码，不交给模型。
- 跨 Run 恢复：引擎确定性筛选上一 Run 可恢复步骤（非 succeeded/cancelled，且无错误或错误码为 NO_SCENE/DEPENDENCY_FAILED），以 recovered_tasks 清单注入 Planner 提示词，由 Planner 并入计划并做语义去重；Planner 不得自行从会话历史生成 Todo。
- 领域知识放在 `config/workers.yaml`、Skills 或工具契约，通用工作流内核不硬编码航天业务。
- Agent 不直接操作 Cesium；所有前端副作用必须经 StreamBridge 并等待 HTTP 回告。
- `RunRepository` 是 Run、Step、WaitingContext 和 Execution Ledger 的业务事实源。
- `WorkflowRun.steps[].result` 是业务结果事实源；直接依赖结果由执行器只读投影给 Worker。
- `ToolExecution.result` 只用于审计、幂等和故障排查，不作为业务输入。
- 同一 `thread_id` 只允许一个活跃 SSE 和一个非终态 Run；重入返回 `409`。
- `done` / `error` 是 SSE 终态。等待用户时发 `interrupt`，再发
  `done {interrupted:true}`，之后通过 Run resume 新开流。
- 已成功的副作用工具调用不得重复执行；重试必须复用 `idempotency_key`。

## 实现导航

| 关注点 | 位置 |
| --- | --- |
| FastAPI 入口 | `src/space_aiagent/main.py` |
| V2 API / SSE | `src/space_aiagent/api/routes.py` |
| SSE schema | `src/space_aiagent/models/sse_schemas.py` |
| 工作流 schema | `src/space_aiagent/models/workflow_schemas.py` |
| Bridge / 会话 | `src/space_aiagent/bridge/` |
| 工作流核心 | `src/space_aiagent/workflow/` |
| Worker 配置 | `config/workers.yaml` |
| Worker 加载 | `src/space_aiagent/agents/workers.py` |
| Skills | `src/space_aiagent/skills/<scope>/<skill>/SKILL.md` |
| 工具 | `src/space_aiagent/tools/` |
| 工具契约 | `src/space_aiagent/tools/contracts.py` |
| 前端协议 | `docs/前端SSE对接指南.md` |

## 修改规则

### API / SSE

- 事件名统一使用 `SSEEventType`。
- 所有帧必须带 `thread_id`；V2 帧必须带 `run_id/seq/revision/timestamp`。
- 工具生命周期保持 `tool_start -> tool_args -> tool_result -> tool_end`。
- 修改字段时同步 schema、API 测试和前端对接文档。

### Worker / Skills

- Worker 每次只执行一个 Todo，不规划或委派后续 Todo。
- Worker 只能调用 `workers.yaml` 绑定且被 Skill 路由允许的工具。
- Skill 只负责单步骤 SOP，不管理跨步骤依赖、Todo 或结束判断。
- Skill 放在虚拟 `/skills/<scope>/` 后端，不拼接宿主机路径。

### Tools

- 在 `tools/<group>/` 中用 `@tool` 定义，注册表自动发现。
- 跨 Worker 前置事实使用 `@workflow_tool` 的 `requires/effects/invalidates` 声明。
- 后端参数用 snake_case，发前端按工具协议转 camelCase。
- 远程工具通过 `bridge_var.get()` 取 StreamBridge，不得绕过桥接。

## 看板与验证

架构阶段、当前焦点和下一任务的唯一事实源是
`readme/Agent内核架构白皮书.md` 第 6.1 节。完成架构任务后同步状态和证据。

```bash
conda activate space-aiagent-v1
pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

环境名称 `space-aiagent-v1` 是历史本地标识，不代表代码仍提供 V1 架构。
