# space-aiagent

面向航天 GIS 分析平台的多 Agent 智能助手，基于 FastAPI、DeepAgents 和 LangGraph。
后端理解用户意图、编排专业 Agent 并生成工具指令；场景操作仍由前端 Cesium 执行。

## 当前能力

- Orchestrator 将场景、实体与轨道任务委派给专业子 Agent。
- SSE + HTTP POST 实时传输 token、工具调用过程、人工审批和最终结果。
- `StreamBridge` 用 `asyncio.Future` 将后端工具调用与前端 Cesium 执行结果关联。
- SQLite checkpointer 按 `thread_id` 保存多轮状态，并支持 LangGraph interrupt/resume。
- `ToolStrategy(AgentResponse)` 约束最终输出，统一渲染 summary、suggestions 和场景查询表格。
- 内置 Skills 通过 Flash 预路由自动加载；required Skill 的工具门禁阻止模型绕过业务 SOP。
- RetryMiddleware 提供 LLM/工具重试与降级。
- OpenTelemetry + Langfuse v3 提供可选的 trace、token 归因和日志关联。

当前内置 Skill：

| Scope | Skill | 用途 |
| --- | --- | --- |
| scene | `open-scenario` | 查询并打开唯一匹配场景，多结果返回候选 |
| scene | `query-scenario` | 查询、筛选或列出现有场景 |
| entity | `add-entity` | 校验参数后添加点实体或 SGP4 卫星 |

> Skill Package、基础审计、构建期质量校验和 required Skill 路由门禁已经落地；脚本沙箱、
> 命令白名单和持久化完整审计等 Backend/Policy 能力仍在后续阶段。

## 架构设计

### 整体工作流

```text
用户
  |
  | POST /api/v1/space/chat（响应为 SSE）
  v
Orchestrator -- task --> Scene Agent / Entity Agent
                              |
                              | Python tool
                              v
                         StreamBridge
                              |
                              | SSE tool_start / tool_args
                              v
                         前端 Cesium
                              |
                              | POST /api/v1/space/tool-result
                              v
                         StreamBridge Future
                              |
                              | SSE tool_result / tool_end / done
                              v
                             用户
```

一轮普通对话由一个 `POST /chat` 流和零到多个 `POST /tool-result` 组成。同一
`thread_id` 同时只能有一个活跃流，否则返回 `409 Conflict`。

### 多 Agent + 工具组管理

```
                        用户输入(POST /chat)
                              │
                              ▼
                    ┌─────────────────┐
                    │  Orchestrator   │  主控Agent：意图识别、任务规划
                    │  (DeepAgents)    │  只知道工具组摘要列表
                    │  + ToolStrategy │  结构化输出（AgentResponse）
                    │  + LoggingMW    │  LLM调用/工具执行日志
                    └───────┬─────────┘
                            │ 路由到子Agent
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │  Scene   │  │  Entity  │  │ Analysis │
        │  Agent   │  │  Agent   │  │  Agent   │
        └────┬─────┘  └────┬─────┘  └──────────┘
             │              │
     ┌───────┴──┐    ┌─────┴──────────┐
     │ 工具组:   │    │ 工具组:          │
     │ scene    │    │ entity          │
     │ manage   │    │ manage          │
     └──────────┘    ├─────────────────┤
                     │ 工具组:          │
                     │ orbit           │
                     │ manage          │
                     └─────────────────┘
              │              │
              ▼              ▼
        Remote Tool Bridge (SSE tool_start/tool_args + POST /tool-result)
              │              │
              ▼              ▼
         Cesium 前端执行
```

- **Orchestrator**: 意图识别、任务规划、子 Agent 调度。不直接绑定工具，只持有工具组摘要。使用 `ToolStrategy(AgentResponse)` 强制结构化输出，state_schema 用 `SpaceAgentState`（`agents/state.py`，含 `current_scene_name` 字段，跨 task 边界自动同步）。middleware 顺序为 `PrimaryAgentMiddleware`（task 死循环硬兜底 + 意图捕获 + 自动续接 + 内联 LLM/工具调用日志，详见「任务循环防护」和「意图追踪与自动续接」）→ `agents_dynamic_prompt`（动态注入 current_scene_name 到 system message）→ `RetryMiddleware`（Phase 1B 失败重试+降级）。**LoggingMiddleware / ResponseStabilizationMiddleware 已退役**——orchestrator 不再挂载（类保留供未来复用），可观测性职责由 `PrimaryAgentMiddleware.awrap_model_call` / `awrap_tool_call` 内联日志承担。backend 为 `CompositeBackend`（`/skills/` 路由见「Skill 加载」节）
- **子 Agent**: 通过 `config/subagents.yaml` 声明式配置，`agents/subagents.py` 加载。新增 Agent 改配置 + 提示词 + `tools/registry.py` 注册。middleware 顺序为 `SubagentToolValidationMiddleware(tool_groups=...)` → `agents_dynamic_prompt` → `RetryMiddleware`；**scene-agent 额外在 index 1 插入 `SceneAgentHitlMiddleware`**（open_scenario 两个 HITL 中断点，见「Human-in-the-loop」节）。子 Agent 可选挂 `skills`（`subagents.yaml` 的 `skills: ["/skills/<scope>/"]`，经共享 backend 加载）
- **Analysis Agent**: 数据分析（未来扩展），独立领域单独扩展
- **Agent 执行**: `astream_events` 流式执行，`on_tool_start`（工具进度提示钩子）+ `on_chain_end`（读 `output.structured_response` + `response_util.render()` 出口渲染发送）事件驱动。task 死循环兜底已下沉到 `PrimaryAgentMiddleware`（阈值 20，详见「任务循环防护」）。详见 `readme/python教程.md` 9.5 节
- **结构化输出**: `ToolStrategy` 利用模型 tool calling API 强制输出 `AgentResponse` JSON。SSE handler 的 `on_chain_end` 事件（`api/sse.py:run_agent`）读 `output.structured_response`，调 `response_util.render()` 渲染为自然语言，作为 `done` 事件的 `content` 发前端——`render()` 直接返回 `summary` + 可选 suggestions 块（**模板渲染已废弃**，YAML 模板仅作 SHORTCUT_RESPONSES 的 summary 默认值）。**A 方案短路**：已知确定性 case（如 `NO_SCENE`）由 `ToolValidationMiddleware` 在工具调用前识别，返回 `Command(goto=END)` 短路（update 里塞携带 NO_SCENE 的 ToolMessage 关闭 AI 的 tool_call，跳过子 Agent 后续 LLM 调用）。state 由 LangGraph 自动持久化到 checkpointer（多轮对话上下文完整）。注册表在 `models/response_schema/response_constants.SHORTCUT_RESPONSES`

### 远程工具桥接（核心机制）

工具在后端定义但实际在前端 Cesium 执行。通过 `StreamBridge`（`bridge/stream_bridge.py`）持 `asyncio.Queue` 事件出口 + `asyncio.Future` 桥接：

```
Agent 调用工具 → StreamBridge.send_tool_call()
                  ├─ emit tool_start/tool_args 到 SSE queue → POST /chat 响应流出前端
                  └─ await Future
Agent 得到结果 ← await Future ← StreamBridge.resolve_tool_result() ← POST /tool-result handler
                  └─ emit tool_result/tool_end 到 SSE queue → 流出前端
```

每次工具调用创建 Future 绑定到唯一 `tool_call_id`，`POST /tool-result` handler（通过 `SessionManager.get_bridge(thread_id)` 定位 bridge）收到前端结果时根据 ID resolve。Future / resolve / cleanup / 超时（默认 60s）语义从旧 `WSBridge` 原样迁移。

**Bridge 注入方式**: `bridge.bridge_var` (ContextVar)，由 SSE `event_generator`（`api/sse.py`）在当前请求 context 内 `bridge_var.set(bridge)` 注入（Starlette 把 StreamingResponse body 迭代放在 copy 出来的 context 里，故 set/reset 都在 `event_generator` 内），`asyncio.create_task(run_agent)` 拷贝该 context → agent 任务及工具函数通过 `bridge_var.get()` 可见。

### Agent 组成

| Agent | 职责 | 工具 / Skill |
| --- | --- | --- |
| Orchestrator | 意图识别、任务委派、结果汇总 | `task`、memory、`AgentResponse` |
| Scene Agent | 创建、查询、打开、重命名、删除场景 | `scene_management`、scene Skills |
| Entity Agent | 添加/查询/清空实体，创建和更新 SGP4 轨道 | `entity_management`、`orbit_management`、entity Skills |

子 Agent 由 [`config/subagents.yaml`](config/subagents.yaml) 声明。Orchestrator 使用
`CompositeBackend`：默认根目录提供 `config/knowledge/AGENTS.md` memory，`/skills/`
虚拟路径路由到 `src/space_aiagent/skills/`。

### 状态与结构化输出

`SpaceAgentState` 保存 `current_scene_name`、场景查询结果等字段。`/chat` 注入前端当前场景，
场景工具成功后通过 `Command(update=...)` 更新，DeepAgents 在父子 task 边界同步状态。

最终响应由 `ToolStrategy(AgentResponse)` 生成。SSE 流正常结束后，后端从 checkpoint 终态读取
`structured_response`，通过 `response_util.render()` 生成 `done.content`。场景查询结果会被渲染为
可点击的 Markdown 表格，而不是直接暴露工具 JSON。

`AgentResponse` 的字段为 `status`、`code`、`summary`、`data` 和 `suggestions`。其中 `data`
在工具 schema 中是原生 JSON 数组（每项为对象）或 `null`，禁止模型把整个数组再次编码成 JSON
字符串。为兼容部分模型的 tool calling 偶发二次序列化，模型边界会将内容合法且顶层为数组的 JSON
字符串规范化为数组；其他错误类型仍按结构化输出校验失败处理。

### Human-in-the-loop

`config/subagents.yaml` 当前启用三类声明式审批：

- 删除场景：approve / reject
- 重命名场景：approve / reject
- 清空实体：approve / reject

发生中断时，当前 SSE 流依次发送：

```text
interrupt
done {"interrupted": true, "content": ""}
```

前端收集决策后调用 `POST /api/v1/space/chat/{thread_id}/resume`，该响应是一条新的 SSE 流。
代码仍兼容 `hitl_select` / `hitl_yn` 自定义中断格式，但 `SceneAgentHitlMiddleware` 当前未挂载，
因此不能依赖这两类中断自动出现。

### 场景前置条件

创建实体等操作在业务上要求已有场景。`current_scene_name` 仍在后端状态中维护，但当前版本把
是否存在场景的最终校验交给前端工具接口；`SubagentToolValidationMiddleware` 中的后端
fail-fast 分支暂时禁用。前端应通过工具结果返回 `NO_SCENE` 等明确 code，Skill/Agent 再据此回复。

## SSE + POST 接口

API 前缀：`/api/v1/space`。完整前端契约见
[`docs/前端SSE对接指南.md`](docs/前端SSE对接指南.md)。

### `POST /chat`

请求：

```json
{
  "content": "查询测试场景",
  "thread_id": "thread-001",
  "message_id": "message-001",
  "current_scene_name": null
}
```

响应为 `text/event-stream`。事件类型：

| Event | 主要 data 字段 | 含义 |
| --- | --- | --- |
| `token` | `content`, `source`, `thread_id` | 可读的 LLM 文本 token |
| `tool_start` | `tool_func`, `namespace`, `tool_call_id`, `thread_id` | 工具开始 |
| `tool_args` | `tool_func`, `tool_call_id`, `args`, `thread_id` | 工具参数 |
| `tool_result` | `tool_func`, `tool_call_id`, `result`, `thread_id` | 工具结果回显 |
| `tool_end` | `tool_func`, `tool_call_id`, `thread_id` | 工具结束 |
| `interrupt` | `interrupt_type` 等、`thread_id` | 图暂停，等待人工决策 |
| `done` | `content`, `thread_id`, 可选 `interrupted` | 正常终态或暂停流终态 |
| `error` | `message`, `thread_id` | 异常终态 |

SSE 帧格式：

```text
event: tool_start
data: {"tool_func":"queryScenario","tool_call_id":"...","thread_id":"thread-001"}

```

`done` 和 `error` 会关闭流。浏览器原生 `EventSource` 只支持 GET，因此前端需用
`fetch()` + `ReadableStream` 消费 POST 响应。

### `POST /tool-result`

前端执行 Cesium 方法后回告：

```json
{
  "tool_func": "queryScenario",
  "tool_call_id": "<tool_start 中的 id>",
  "thread_id": "thread-001",
  "args": {"sceneName": "测试"},
  "success": true,
  "message": "查询成功",
  "data": [],
  "code": "SCENE_QUERY_SUCCESS"
}
```

成功返回 `{"ok": true}`；没有对应活跃流时返回 `404`。

### `POST /chat/{thread_id}/resume`

声明式审批示例：

```json
{
  "resume": {
    "decisions": [{"type": "approve"}]
  }
}
```

拒绝时使用 `{"type": "reject"}`。必须复用触发中断的 `thread_id`。

## 工具清单

工具在 Python 中使用 snake_case，发给前端的 `tool_func` 使用 camelCase。

| Python 工具 | 前端 `tool_func` | namespace | 说明 |
| --- | --- | --- | --- |
| `create_scenario` | `createScenario` | `scene_tools` | 创建场景 |
| `query_scenario` | `queryScenario` | `scene_tools` | 查询场景 |
| `open_scenario` | `openScenario` | `scene_tools` | 打开已有场景 |
| `rename_scenario` | `renameScenario` | `scene_tools` | 重命名当前场景，需审批 |
| `delete_scene` | `deleteScene` | `scene_tools` | 删除当前场景，需审批 |
| `add_point_entity` | `addPointEntity` | `entity_tools` | 添加非卫星点实体 |
| `query_entities` | `queryEntities` | `entity_tools` | 查询实体 |
| `clear_entities` | `clearEntities` | `entity_tools` | 清空实体，需审批 |
| `create_sgp4_orbit` | `createSgp4Orbit` | `entity_tools` | 根据两行 TLE 创建卫星 |
| `update_sgp4_orbit` | `updateSgp4Orbit` | `entity_tools` | 更新 SGP4 轨道样式 |

具体参数以 `src/space_aiagent/models/schemas.py`、工具的 args schema 及 Skill 为准。

## 项目结构

```
src/space_aiagent/
├── main.py                 # FastAPI 应用入口，初始化配置和日志
├── cli.py                  # CLI 管理入口（run / tools list / tools show）
├── api/                    # API 层
│   ├── routes.py           # REST 端点（invoke / health）
│   └── websocket.py        # WebSocket 端点（astream_events 流式事件驱动）
├── agents/                 # Agent 层
│   ├── orchestrator.py     # 主控 Agent（create_deep_agent + subagents + memory + ToolStrategy）
│   ├── state.py            # SpaceAgentState（state_schema，含 current_scene_name，跨 task 边界自动同步）
│   ├── subagents.py        # 子 Agent 加载器（从 YAML 配置构建）
│   └── subagents_util.py   # load_subagents_yaml_config + resolve_subagent_type（自动续接路由分类）
├── middleware/              # Agent 中间件
│   ├── logging.py          # LoggingMiddleware（**已退役**，类保留；可观测性职责转交 PrimaryAgentMiddleware 内联日志）
│   ├── primary_agent_middleware.py # PrimaryAgentMiddleware（task 死循环硬兜底 + 意图捕获 + 自动续接 + 内联 LLM/工具调用日志）
│   ├── dynamic_prompt.py   # agents_dynamic_prompt（每次 LLM 调用前把 current_scene_name 注入 system message）
│   ├── response_stabilization.py # ResponseStabilizationMiddleware（**已退役占位**，模板渲染废弃后无职责，类保留挂中间件链）
│   └── tool_validation.py  # ToolValidationMiddleware（bridge + 场景上下文 fail-fast + suggestion 候选集注入）
├── prompts/                # 提示词模板（与代码分离）
│   ├── orchestrator.md     # 主控 Agent 提示词（含 {tool_summaries} 占位符）
│   ├── scene_agent.md      # 场景子 Agent 提示词
│   └── entity_agent.md     # 实体子 Agent 提示词
├── tools/                  # 工具组管理
│   ├── registry.py         # 静态工具注册表（标准 import + 按组分组）
│   ├── scene_management/   # 场景管理工具组（6 个工具）
│   ├── entity_management/  # 实体管理工具组（2 个工具）
│   └── orbit_management/   # 轨道管理工具组（2 个工具）
├── models/                 # 数据模型
│   ├── enums.py            # 枚举（EntityType / WSMessageType / LLMProvider）
│   ├── schemas.py          # Pydantic 模型（工具参数、API 请求响应、SubagentClassification）
│   ├── messages.py         # WebSocket 消息类型
│   └── response_schema/    # AgentResponse 模型包
│       ├── agent_struct_response.py # AgentResponse + ResponseCode 枚举（ToolStrategy 输出格式 + suggestions 越界过滤）
│       ├── response_constants.py    # INTENTION_TO_CATCH_CODES / INTENTION_RESUME_TRIGGER_CODES + SHORTCUT_RESPONSES（A 方案预构建 AgentResponse 注册表）
│       └── response_util.py         # find_agent_response_tool_call / get_agent_response_code_from_model_response / render（summary + suggestions 出口渲染）
├── bridge/                 # 远程工具桥接层
│   ├── ws_bridge.py        # WSBridge（Future 桥接 + 消息发送）
│   ├── session.py          # SessionManager（thread_id → WebSocket 映射）
│   └── __init__.py         # bridge_var (ContextVar) 导出
└── infrastructure/         # 基础设施
    ├── config.py           # 配置管理（YAML + .env + 多环境；含 LLMConfig + LLMFlashConfig + ObservabilityConfig）
    ├── llm.py              # build_model() + build_flash_model()（Flash 专供路由分类等轻量调用）
    ├── logging.py          # structlog 结构化日志（含 `_add_trace_info` processor 注入 trace_id/span_id）
    ├── database.py         # SQLite + AsyncSqliteSaver checkpointer
    ├── observability/      # OTel + Langfuse v3（Phase 1A-1，对业务零依赖，enabled=false 时 NoOp）
    │   ├── tracing.py      # setup_telemetry / shutdown_telemetry / get_tracer / optional_span / set_span_io（span IO 属性）
    │   └── processors.py   # add_trace_info structlog processor
    ├── response_template_yaml.py # 加载 config/response_templates.yaml → DEFAULT_TEMPLATES（仅 SHORTCUT_RESPONSES 用作 summary 默认值）
    └── utils/              # 通用工具
        ├── string_util.py  # snake/camel 转换、args_to_camel、truncate、flat_tuple_list
        ├── message_util.py # extract_last_task / extract_last_human_intent / extract_last_existing_intent / build_task_response / msg_preview / serialize_messages / serialize_model_response（后两者供 set_span_io 序列化 IO）
        └── collection_util.py # trim_list 等
```

## 技术栈

- Python 3.13
- FastAPI + Uvicorn
- DeepAgents + LangGraph
- langchain-openai（OpenAI 兼容接口，可接 DeepSeek / Qwen）
- SQLite + `AsyncSqliteSaver`
- OpenTelemetry + Langfuse v3
- structlog、tenacity、Pydantic
- pytest、Ruff、pre-commit

## 快速开始

```bash
cp .env.example .env

conda create -n space-aiagent-v1 python=3.13
conda activate space-aiagent-v1

pip install -e ".[dev]"
pre-commit install

python -m space_aiagent.main
```

默认监听 `0.0.0.0:8028`。健康检查和 API 文档可通过 FastAPI 路由查看。

### LLM 配置

`.env` 至少配置主模型；Flash 模型用于轻量路由等辅助调用：

```dotenv
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

LLM_FLASH_API_KEY=sk-xxx
LLM_FLASH_BASE_URL=https://api.deepseek.com
LLM_FLASH_MODEL=deepseek-chat
```

主模型参数在 `config/application.yaml` 的 `agent` 段，Flash 模型参数在 `flash_model` 段。

### 可观测性

默认 `observability.enabled: false`，全链路走 NoOp。启用时需要 Langfuse v3：

```bash
docker compose --env-file .env -f docker/observability/docker-compose.yml up -d
```

必须显式传 `--env-file .env`，否则 Compose 会从 `docker/observability/` 查找 `.env`，
可能导致 Langfuse 初始化密钥为空。业务 trace 以 `agent.session` 为根，LLM 和工具调用为子 span。

## 开发命令

```bash
python -m space_aiagent.cli --help
python -m space_aiagent.cli tools list
python -m space_aiagent.cli tools show scene_management

pytest
pytest tests/test_api tests/test_bridge
pytest tests/test_skills tests/test_tools

ruff check src/ tests/
ruff format --check src/ tests/
python scripts/gen_requirements.py
```

## 设计与对接文档

- [`AGENTS.md`](AGENTS.md)：Codex 仓库级工作约定
- [`docs/前端SSE对接指南.md`](docs/前端SSE对接指南.md)：详细 SSE、HITL、前端联调契约；若其中的阶段性说明与当前实现冲突，以本 README 和代码为准
- [`docs/superpowers/specs/2026-07-21-sse-migration-design.md`](docs/superpowers/specs/2026-07-21-sse-migration-design.md)：SSE 迁移设计
- [`docs/superpowers/specs/2026-07-29-hitl-transport-design.md`](docs/superpowers/specs/2026-07-29-hitl-transport-design.md)：HITL 传输设计
- [`docs/superpowers/specs/2026-07-29-skill-system-design.md`](docs/superpowers/specs/2026-07-29-skill-system-design.md)：Skill 三层架构设计
- [`readme/Agent内核架构白皮书.md`](readme/Agent内核架构白皮书.md)：产品架构与演进路线
- [`readme/DEV.md`](readme/DEV.md)：开发补充说明

## 当前阶段

架构进度不在 README 维护副本，统一以
[`Agent内核架构白皮书 §6.1 执行进度看板`](readme/Agent内核架构白皮书.md#61-执行进度看板单一事实源)
为准。

当前已完成 Skill Package、基础审计与质量门槛，并为 3 个内置 Skill 启用 Flash 预路由和
通用工具门禁；下一任务是 **Phase 2C：多模型动态路由**。完成任务后应更新白皮书看板，
而不是只修改本段摘要。
