# space-aiagent 仓库说明

本项目为 Cesium 航天 GIS 平台提供确定性多 Agent 助手。当前只有 V2 架构：
外层 LangGraph 工作流控制顺序和状态，内层 DeepAgents Worker 执行单个领域步骤。

## 主链路

```text
POST /api/v2/space/chat
  -> StructuredPlanner -> PlanValidator -> WorkflowRun
  -> Scheduler / PreconditionEngine
  -> AgentStepExecutor -> scene-agent / entity-agent
  -> StreamBridge -> Cesium
  -> POST /api/v2/space/tool-result
  -> Execution Ledger -> FinalizationGuard
```

## 不可破坏的约束

- 协议优先：修改 SSE、工具回告、Run/Step 模型时先更新设计和前端文档。
- 系统只提供 `/api/v2/space`；不恢复 V1 路由、自由主 Agent 或兼容中间件。
- Planner 只生成结构化 action DAG，不绑定领域工具、不决定运行状态。
- Scheduler、前置条件、失败传播和结束判断必须是代码，不交给模型。
- 领域知识放在 `config/actions.yaml`、Skills 或工具，通用工作流内核不硬编码航天业务。
- Agent 不直接操作 Cesium；所有前端副作用必须经 StreamBridge 并等待 HTTP 回告。
- `RunRepository` 是 Run、Step、WaitingContext 和 Execution Ledger 的业务事实源。
- `WorkflowRun.steps[].result` 是业务结果事实源；跨步骤只能通过 `ResultRef` 消费。
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
| ActionCatalog | `config/actions.yaml` |
| Worker 配置 | `config/workers.yaml` |
| Worker 加载 | `src/space_aiagent/agents/workers.py` |
| Skills | `src/space_aiagent/skills/<scope>/<skill>/SKILL.md` |
| 工具 | `src/space_aiagent/tools/` |
| 前端协议 | `docs/前端SSE对接指南.md` |

## 修改规则

### API / SSE

- 事件名统一使用 `SSEEventType`。
- 所有帧必须带 `thread_id`；V2 帧必须带 `run_id/seq/revision/timestamp`。
- 工具生命周期保持 `tool_start -> tool_args -> tool_result -> tool_end`。
- 修改字段时同步 schema、API 测试和前端对接文档。

### Worker / Skills

- Worker 每次只执行一个 action，不规划或委派后续 action。
- Worker 只能调用 ActionCatalog 对当前步骤授权的工具。
- Skill 只负责单步骤 SOP，不管理跨步骤依赖、Todo 或结束判断。
- Skill 放在虚拟 `/skills/<scope>/` 后端，不拼接宿主机路径。

### Tools

- 在 `tools/<group>/` 中用 `@tool` 定义，注册表自动发现。
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
