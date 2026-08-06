# space-aiagent 仓库说明

本文件是仓库根目录的 Codex 持久指令，适用于整个仓库。产品介绍和接口示例见
[`README.md`](README.md)，架构决策见 [`readme/Agent内核架构白皮书.md`](readme/Agent内核架构白皮书.md)
及 [`docs/superpowers/specs/`](docs/superpowers/specs/)；不要在本文件复制大段设计历史。

## 项目目标

本项目为基于 Cesium 的航天 GIS 平台提供多 Agent 智能助手。后端使用 FastAPI、
DeepAgents 和 LangGraph；Agent 不直接操作 Cesium，而是通过 SSE 向前端发出工具事件，
前端执行后用 HTTP POST 回告结果。

当前主链路：

```text
POST /api/v1/space/chat
  -> Orchestrator
  -> task(subagent)
  -> 后端工具
  -> SSE tool_start/tool_args
  -> 前端 Cesium
  -> POST /api/v1/space/tool-result
  -> SSE tool_result/tool_end/done
```

## 不可破坏的架构约束

- 协议优先：改变 SSE、工具参数、HITL 或 Skill 格式时，先更新协议/设计文档，再改实现。
- 内核零业务知识：航天业务流程放在 `skills/`、工具或外部 knowledge 中，不要硬编码进通用内核。
- Agent 不直接操作前端场景；所有 Cesium 操作必须经过 `StreamBridge` 和前端回告。
- 创建实体前必须存在场景。当前后端的场景前置校验被临时关闭，前端工具结果是该约束的最终兜底；
  不要在文档中误写成后端正在 fail-fast。
- `current_scene_name` 属于 `SpaceAgentState`，由 `/chat` 注入并由场景工具的 `Command(update=...)`
  更新；不要改回 ContextVar。
- 同一 `thread_id` 只允许一个活跃流；`/chat` 和 `/resume` 重入必须保持 `409`。
- `done` 与 `error` 是 SSE 终态。中断时先发 `interrupt`，再发
  `done {interrupted: true}` 关闭当前流，之后通过 `/chat/{thread_id}/resume` 新开流续跑。
- `AgentResponse` + `ToolStrategy` 是当前最终结构化输出方案；不要根据中间提交误删。
- 可观测性关闭或 Langfuse 不可用时，业务必须通过 NoOp 路径正常运行。
- 重试必须区分可恢复与不可恢复错误，且不得重复已成功的有副作用工具调用。

## 当前实现边界

- 传输层已从 WebSocket 迁移为 SSE + POST；不要新增或恢复 WebSocket 业务路径。
- 声明式 HITL 已启用：`config/subagents.yaml` 中的 `interrupt_on` 保护删除场景、重命名场景、
  清空实体等操作。
- `SceneAgentHitlMiddleware` 代码仍保留，但当前在 `agents/subagents.py` 中未挂载；
  `hitl_select` / `hitl_yn` 只能视为协议兼容能力，不能写成当前必然触发的行为。
- Skill Package 层已接入：`CompositeBackend` 将 `/skills/` 路由到
  `src/space_aiagent/skills/`。当前内置 `open-scenario`、`query-scenario`、`add-entity`。
- Skill Backend/Policy 层（命令白名单、脚本沙箱、完整审计、安装脚本）尚未实现。
- `PrimaryAgentMiddleware` 当前承担日志、trace 与连续 `task` 循环保护；旧的意图捕获/自动续接
  已移除。委派要求主要由 orchestrator prompt 约束。
- `ResponseStabilizationMiddleware` 已删除；`LoggingMiddleware` 文件仅保留备用，未挂载。

## 路线与任务推进

- 架构阶段、当前焦点和下一任务的唯一权威来源是
  [`readme/Agent内核架构白皮书.md` 第 6.1 节](readme/Agent内核架构白皮书.md#61-执行进度看板单一事实源)。
- 开始架构层或跨模块任务前，先读取该看板，确认任务属于当前阶段或明确说明为何插队。
- 完成一个看板任务后，同步更新该行的状态、完成日期/证据，并把 `▶ 下一任务` 移到下一个
  可执行项；若下一项受前置条件阻塞，记录阻塞条件并跳到下一个可执行项。
- `AGENTS.md`、`README.md`、`CLAUDE.md` 不再复制完整阶段表，只保留指向白皮书的导航，
  避免多个进度表相互漂移。

## 代码导航

| 关注点 | 位置 |
| --- | --- |
| FastAPI 启动与路由注册 | `src/space_aiagent/main.py` |
| SSE、工具回告、resume | `src/space_aiagent/api/sse.py` |
| SSE schema | `src/space_aiagent/models/sse_schemas.py` |
| 工具事件/Future 桥接 | `src/space_aiagent/bridge/stream_bridge.py` |
| Orchestrator 构建 | `src/space_aiagent/agents/orchestrator.py` |
| 子 Agent 配置加载 | `src/space_aiagent/agents/subagents.py`, `config/subagents.yaml` |
| 会话状态 | `src/space_aiagent/agents/state.py` |
| 主/子 Agent 中间件 | `src/space_aiagent/middleware/` |
| Agent 提示词 | `src/space_aiagent/prompts/` |
| 内置 Skills | `src/space_aiagent/skills/<scope>/<skill>/SKILL.md` |
| 工具实现与自动发现 | `src/space_aiagent/tools/`、`tools/registry.py` |
| 结构化响应 | `src/space_aiagent/models/response_schema/` |
| 配置 | `config/application.yaml`、`config/{dev,staging,prod}.yaml`、`.env` |
| 前端契约 | `docs/前端SSE对接指南.md` |

## 修改规则

### SSE / HITL

- 事件名统一使用 `SSEEventType`，不要散落新的字符串字面量。
- 每个 SSE `data` 必须带 `thread_id`；优先通过 `StreamBridge._emit()` 自动注入。
- 工具生命周期保持 `tool_start -> tool_args -> tool_result -> tool_end`。
- 修改请求或事件字段时，同时更新 `sse_schemas.py`、端点测试和前端对接指南。
- LangGraph 中断检测依赖 `agent.astream(..., stream_mode=["messages", "values"],
  subgraphs=True, version="v2")`；不要改回无法可靠检测 graph interrupt 的旧事件路径。
- resume 必须复用同一个 `thread_id` 和 checkpointer，不重新注入首轮状态。

### Agent / 中间件

- Orchestrator 只做意图识别、委派和汇总，不绑定领域工具。
- 子 Agent 由 `config/subagents.yaml` 声明；新增 Agent 时同步提示词、工具组和所需 Skills。
- 中间件顺序会影响短路、重试、HITL 和 trace；调整顺序前先补回归测试。
- 场景工具更新状态时返回 `Command(update={"current_scene_name": ...})`，保持父子 Agent 双向同步。

### Tools

- 在 `tools/<group>/` 内用 `@tool` 定义工具；注册表会自动发现该目录的 `BaseTool`，
  通常不需要手工编辑 `registry.py`。
- 工具 description 的首段首句必须是用户视角的能力描述，供 suggestions 候选集使用。
- 后端参数使用 snake_case；发给前端时按现有工具约定转换为 camelCase。
- 所有远程工具通过 `bridge_var.get()` 获取 `StreamBridge`，不得绕过桥接直接调用前端。

### Skills

- Skill 放在 `src/space_aiagent/skills/<scope>/<skill-name>/SKILL.md`，使用 YAML frontmatter
  的 `name` 和 `description`。
- `description` 负责触发判断；正文只写命中后需要执行的流程，避免与 prompt 重复。
- Skill 内引用真实工具名、参数和返回码；更改工具契约时同步检查所有相关 Skill。
- Skill 路径在 `config/subagents.yaml` 中使用 `/skills/<scope>/` 虚拟路径，不拼接宿主机路径。
- 在脚本型 Skill 的安全 Backend/Policy 落地前，不要加入依赖任意 shell 执行的生产 Skill。

## 本地开发与验证

环境要求 Python 3.13。常用命令：

```bash
conda activate space-aiagent-v1
pip install -e ".[dev]"
python -m space_aiagent.main
python -m space_aiagent.cli --help
pytest
ruff check src/ tests/
ruff format --check src/ tests/
```

按改动范围选择最小测试，交付前再扩大：

- SSE / HITL：`pytest tests/test_api tests/test_bridge`
- Skills：`pytest tests/test_skills`
- 工具：`pytest tests/test_tools`
- 中间件 / 重试：`pytest tests/test_middleware`
- 响应模型：`pytest tests/test_models`

若测试因本地缺少 API Key、外部服务或环境依赖无法运行，明确报告未验证项，不要伪造结果。

## 代码与文档风格

- Python 参数和返回值必须有类型注解；业务解释使用简洁中文注释。
- 遵循 Ruff，行宽 120；不要顺手格式化或改写无关文件。
- 保留用户已有的工作区修改，不使用破坏性 Git 命令。
- `README.md` 描述当前可用能力；`docs/superpowers/specs/` 记录设计决策；
  `AGENTS.md` 只保留持久、可执行的仓库约定。
- 删除或改名模块后，使用 `rg` 清理 `AGENTS.md`、`README.md`、前端指南和代码注释中的陈旧引用。

## 前端代码
- 前端交互代码（仅参考，不修改）：`https://gitee.com/910922164/space2024/tree/master/plugins/sceneAgent`
