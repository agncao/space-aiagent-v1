# Space AI Agent

面向 Cesium 航天 GIS 平台的确定性多 Agent 助手。系统采用“外层确定性工作流、内层
DeepAgents Worker”的单一架构，不保留自由主 Agent 编排路径。

## 架构

```text
POST /api/v2/space/chat
  -> Worker Todo Planner
  -> PlanValidator
  -> WorkflowRun / RunRepository
  -> Scheduler + dynamic requirement insertion
  -> AgentStepExecutor
  -> scene-agent / entity-agent / future analysis-agent
  -> StreamBridge
  -> Cesium 前端
  -> POST /api/v2/space/tool-result
  -> Execution Ledger
  -> FinalizationGuard + Finalizer
```

- Planner 只按 Worker 生成自然语言 Todo，不生成 action、工具或参数。
- Scheduler、动态前置依赖、失败传播和结束判断全部由代码控制。
- DeepAgents Worker 每次只执行一个 Todo；Skill 只描述单 Todo SOP。
- `WorkflowRun.steps[].result` 是业务结果事实源，直接依赖结果由执行器只读投影给 Worker。
- `ToolExecution.result` 只用于审计、幂等和故障排查。
- Agent 不直接操作 Cesium，所有远程工具经 SSE 发给前端并等待 HTTP 回告。

详细决策见 [Agent 内核架构白皮书](readme/Agent内核架构白皮书.md)。

## API

统一前缀：`/api/v2/space`

| 端点 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `POST /chat` | 创建或续接 WorkflowRun，响应 SSE |
| `POST /tool-result` | 前端回告工具执行结果 |
| `GET /runs/{run_id}` | 获取计划和进度快照 |
| `POST /runs/{run_id}/resume` | 恢复 waiting_user Run |
| `POST /runs/{run_id}/cancel` | 取消非终态 Run |

SSE 事件包括 `plan_snapshot`、`step_update`、`run_update`、`tool_start`、`tool_args`、
`tool_result`、`tool_end`、`interrupt`、`done` 和 `error`。完整契约见
[前端 SSE 对接指南](docs/前端SSE对接指南.md)。

## 代码导航

| 关注点 | 位置 |
| --- | --- |
| FastAPI 路由 | `src/space_aiagent/api/routes.py` |
| 工作流引擎 | `src/space_aiagent/workflow/engine.py` |
| 工作流 Schema | `src/space_aiagent/models/workflow_schemas.py` |
| Planner / Validator / Scheduler | `src/space_aiagent/workflow/` |
| Worker 执行器 | `src/space_aiagent/workflow/executor.py` |
| Worker 配置 | `config/workers.yaml` |
| Worker 加载 | `src/space_aiagent/agents/workers.py` |
| Skills | `src/space_aiagent/skills/` |
| 工具 | `src/space_aiagent/tools/` |
| 工具契约 | `src/space_aiagent/tools/contracts.py` |
| SSE / Cesium Bridge | `src/space_aiagent/bridge/` |

## 本地开发

Python 3.13。现有 Conda 环境名称仍为历史名称 `space-aiagent-v1`，仅是本地环境标识。

```bash
conda activate space-aiagent-v1
pip install -e ".[dev]"
python -m space_aiagent.main
pytest
ruff check src tests scripts
ruff format --check src tests scripts
```

产品尚未上线，V2 不迁移历史 checkpoint。首次启动时自动创建全新的
`data/space_aiagent.db` 和 `data/workflow.db`。
