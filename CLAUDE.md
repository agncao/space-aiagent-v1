# space-aiagent

航天分析平台智能助手 - 基于 DeepAgents (LangChain) 的多 Agent 系统

## 项目概述

为上海航天研究院 805 所定制的航天分析平台（基于 Cesium 的航天 GIS 系统）提供 AI 智能助手能力。

核心通信链路：前端交互端 ←SSE（后端→前端事件流）+ HTTP POST（前端→后端离散指令）→ 后端 Agent 服务。前端 Cesium 是 JS 库，所有场景操作在前端执行，Agent 不直接操作场景，而是生成指令通过 SSE `tool_start`/`tool_args` 事件发给前端，前端执行后通过 `POST /api/v1/space/tool-result` 回告。

业务约束：创建实体（卫星等）前必须先有场景，前端会校验此依赖关系。

## 产品演进路线（架构层）

> 完整架构决策、五个关键决策点、成熟度模型、横切关注点、Skill 系统设计
> 见 [`readme/Agent内核架构白皮书.md`](readme/Agent内核架构白皮书.md)。本节仅记录阶段状态。

### 产品定位

提供具备任务编排能力的航天 AI 助理，内置通用工具能力，支持客户/合作伙伴
通过 Skill 文档扩展垂直业务流程。客户既能直接使用内置能力，也能自行编写
Skill，还能购买产品方或第三方的 Skill 包。

### 阶段状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| **Phase 1A-1** | 可观测性 - AI 维度（OTel instrumentation + Langfuse v3 自部署，trace + token 归因）| ✅ 已完成（2026-07-02） |
| **Phase 1B** | 失败恢复（重试 + 降级）| ✅ 已完成（2026-07-10）|
| **传输层迁移** | WebSocket → SSE+POST 事件流（token/tool_start/tool_args/tool_result/tool_end/interrupt/done），删 WS（[spec](docs/superpowers/specs/2026-07-21-sse-migration-design.md) / [plan](docs/superpowers/plans/2026-07-21-sse-migration.md)）| ✅ 已完成（2026-07-21）|
| **Human-in-the-loop** | interrupt 人工接管（graph interrupt() + /resume 续跑 + 前端决策 UI）| 🟡 待启动（传输层迁移之后）|
| **Phase 2** | Skill 系统第一版（Anthropic 风格协议 + LLM 主动检索 + load_skill 工具 + 3-5 个示例 + 多模型路由 + 基础审计）| 🔵 准备中 |
| **Phase 1C** | 工具能力补全（数据查询、报告生成等）| 🟡 待启动 |
| **Phase 1A-2** | 可观测性 - 系统指标（Prometheus 采集，QPS/延迟/资源）| 🟡 待启动（延后至 1C 之后）|
| **Phase 5** | 横切关注点简化版（用户/角色权限 + 完整审计 + Skill 版本管理）| 🟡 待启动 |
| **Phase 3** | 客户工具插件机制 | ⚪ 暂缓（协议预留）|
| **Phase 1A-3** | 可观测性 - 可视化（Grafana 统一面板 + 跨数据源关联）| 🟡 待启动（延后至 Phase 3 之后，依赖 1A-2）|

**执行顺序调整（2026-07-08）**：Phase 1A-2 后移至 Phase 1C 之后，Phase 1A-3 后移至 Phase 3 之后。AI 维度可观测性（1A-1）已为故障定位与恢复提供足够 trace 支撑，失败恢复（1B）优先于系统指标，作为 Skill 系统（Phase 2）的稳定性托底先落地；系统指标与统一可视化等业务功能与权限体系稳定后再补。1A-2 / 1A-3 依赖关系不变（1A-3 仍依赖 1A-2 的指标数据）。

### 关键架构原则（防漂移）

1. **协议优先于实现**：每加一个能力，先定协议再写代码
2. **内核零业务知识**：航天领域逻辑全部在 Skill/工具/Memory 里
3. **可观测优先**：任何新功能必须先考虑怎么 trace、怎么 metric
4. **失败可恢复**：任何新功能必须设计失败降级路径
5. **客户期望管理**：未实现的能力要在文档中明确说明边界

## 技术栈

- Python 3.13
- FastAPI (REST + SSE)
- deepagents + LangGraph (Agent Harness)
- langchain-openai (DeepSeek / Qwen 兼容接口)
- OpenTelemetry SDK + Langfuse v3（AI 维度可观测性，自部署，对业务零依赖）
- SQLite (持久化，后续可迁移 PostgreSQL)
- ruff (代码质量) + pre-commit (Git hooks)
- pytest (测试)

## 项目结构

```
src/space_aiagent/
├── main.py                 # FastAPI 应用入口
├── cli.py                  # CLI 管理入口
├── api/                    # API 层
│   ├── routes.py           # REST 端点
│   └── sse.py              # SSE + POST 端点（POST /chat 流 + POST /tool-result + POST /chat/{thread_id}/resume，astream_events 流式执行）
├── agents/                 # Agent 层
│   ├── orchestrator.py     # 主控 Agent（DeepAgents + ToolStrategy 结构化输出）
│   ├── state.py            # SpaceAgentState（state_schema，跨 task 边界自动同步字段）
│   ├── subagents.py        # 子 Agent 加载器（YAML 配置驱动）
│   └── subagents_util.py   # load_subagents_yaml_config + resolve_subagent_type（自动续接路由分类，从 PrimaryAgentMiddleware 迁出）
├── middleware/              # Agent 中间件
│   ├── logging.py          # LoggingMiddleware（**已退役**：orchestrator/子 Agent 均不再挂载，类保留供未来复用，可观测性职责由 PrimaryAgentMiddleware 内联日志承担）
│   ├── primary_agent_middleware.py # PrimaryAgentMiddleware（orchestrator task 死循环硬兜底 + 意图捕获 + 自动续接 + 内联 LLM/工具调用日志）
│   ├── dynamic_prompt.py   # agents_dynamic_prompt（@dynamic_prompt 装饰器声明，每次 LLM 调用前把 current_scene_name 注入 system message）
│   ├── response_stabilization.py # ResponseStabilizationMiddleware（**已退役**：模板渲染废弃后无职责，类保留挂在中间件链上作为占位，参考 LoggingMiddleware 退役模式）
│   └── tool_validation.py  # ToolValidationMiddleware（bridge + 场景上下文 fail-fast + suggestion 候选集注入）
├── prompts/                # 提示词模板
│   ├── orchestrator.md     # 主控 Agent 提示词
│   ├── scene_agent.md      # 场景子 Agent 提示词
│   └── entity_agent.md     # 实体子 Agent 提示词
├── tools/                  # 工具组管理
│   ├── registry.py         # 静态工具注册表（标准 import + 按组分组）
│   ├── scene_management/   # 场景管理工具组
│   ├── entity_management/  # 实体管理工具组
│   └── orbit_management/   # 轨道管理工具组
├── models/                 # 数据模型
│   ├── enums.py            # 枚举
│   ├── schemas.py          # Pydantic 模型（工具参数、API 请求响应、SubagentClassification）
│   ├── messages.py         # WS 时代消息类型（POST body 模型字段来源，已去除 type 字段）
│   ├── sse_events.py       # SSE 事件类型 + format_sse_frame 帧序列化（token/tool_start/tool_args/tool_result/tool_end/interrupt/done/error）
│   └── response_schema/    # AgentResponse 模型包（拆分自原 response_schema.py）
│       ├── agent_struct_response.py # AgentResponse + ResponseCode 枚举（suggestions 越界过滤）
│       ├── response_constants.py    # INTENTION_TO_CATCH_CODES / INTENTION_RESUME_TRIGGER_CODES + SHORTCUT_RESPONSES（A 方案预构建 AgentResponse 注册表，原 bridge/response_shortcut.py 迁入）
│       └── response_util.py         # find_agent_response_tool_call / get_agent_response_code_from_model_response / render（summary + suggestions 出口渲染，原 bridge/response_renderer.py 迁入）
├── bridge/                 # 远程工具桥接层
│   ├── stream_bridge.py    # StreamBridge（asyncio.Queue 事件出口，Future 桥接）
│   ├── ws_bridge.py        # **dead code**：旧 WSBridge，test_ws_bridge 引用，待后续删除
│   └── session.py          # 会话管理（register 不再收 websocket，创建 StreamBridge）
└── infrastructure/         # 基础设施
    ├── config.py           # 配置管理（YAML + .env，含 LLMConfig + LLMFlashConfig + ObservabilityConfig）
    ├── llm.py              # build_model() + build_flash_model()（OpenAI 兼容，Flash 专供路由分类等轻量调用）
    ├── logging.py          # 结构化日志（structlog，含 `_add_trace_info` processor 注入 trace_id/span_id）
    ├── database.py         # SQLite 持久化（AsyncSqliteSaver checkpointer）
    ├── observability/      # OTel + Langfuse v3（Phase 1A-1，对业务零依赖，enabled=false 时 NoOp）
    │   ├── tracing.py      # setup_telemetry / shutdown_telemetry / get_tracer / optional_span / set_span_io
    │   └── processors.py   # add_trace_info structlog processor
    ├── response_template_yaml.py # 加载 config/response_templates.yaml → DEFAULT_TEMPLATES（仅 SHORTCUT_RESPONSES 用作 summary 默认值，不再做 {var} 渲染）
    └── utils/              # 通用工具
        ├── string_util.py  # snake/camel 转换、args_to_camel、truncate、flat_tuple_list
        ├── message_util.py # 消息提取/构造工具（extract_last_task / extract_last_human_intent / extract_last_existing_intent / build_task_response / build_primary_agent_response / msg_preview / serialize_messages / serialize_model_response）
        └── collection_util.py # trim_list 等
```

## 常用命令

```bash
# 激活虚拟环境
conda activate space-aiagent-v1

# 安装依赖
pip install -e ".[dev]"

# 生成 requirements.txt
python scripts/gen_requirements.py

# 运行开发服务器
python -m space_aiagent.main

# 运行 CLI
python -m space_aiagent.cli --help

# 运行测试
pytest

# 代码检查
ruff check src/ tests/

# 代码格式化
ruff format src/ tests/
```

## 架构设计

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

- **Orchestrator**: 意图识别、任务规划、子 Agent 调度。不直接绑定工具，只持有工具组摘要。使用 `ToolStrategy(AgentResponse)` 强制结构化输出，state_schema 用 `SpaceAgentState`（`agents/state.py`，含 `current_scene_name` 字段，跨 task 边界自动同步）。middleware 顺序为 `PrimaryAgentMiddleware`（task 死循环硬兜底 + 意图捕获 + 自动续接 + 内联 LLM/工具调用日志，详见「任务循环防护」和「意图追踪与自动续接」）→ `ResponseStabilizationMiddleware`（**已退役占位**，模板渲染废弃后无职责）→ `agents_dynamic_prompt`（动态注入 current_scene_name 到 system message）。**LoggingMiddleware 已退役**——orchestrator 不再挂载，可观测性职责由 `PrimaryAgentMiddleware.awrap_model_call` / `awrap_tool_call` 内联日志承担（类保留供未来复用）
- **子 Agent**: 通过 `config/subagents.yaml` 声明式配置，`agents/subagents.py` 加载。新增 Agent 改配置 + 提示词 + `tools/registry.py` 注册。middleware 顺序为 `ToolValidationMiddleware(tool_groups=...)` → `ResponseStabilizationMiddleware`（**已退役占位**）→ `agents_dynamic_prompt`
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

**场景上下文注入（state_schema 双向同步）**: `current_scene_name` 通过 `SpaceAgentState`（`agents/state.py`）持久化，由 SSE handler（`api/sse.py`）在 `astream_events` 的 input 中注入初值，scene 工具（`create_scenario`/`rename_scenario`/`query_scenario` 成功路径）通过返回 `Command(update={"current_scene_name": ...})` 更新，`ToolValidationMiddleware` 通过 `request.state.get("current_scene_name")` 读取。**关键**：deepagents `task` 工具自动双向同步 state（父→子 `_validate_and_prepare_state`，子→父 `_return_command_with_state_update`，排除 `_EXCLUDED_STATE_KEYS = {"messages","todos","structured_response","skills_*","memory_contents"}`），所以 scene-agent 创建场景后写入的 `current_scene_name` 会自动回传到 orchestrator，再自动传给后续 task 调用的 entity-agent——**避免 ContextVar 跨 task 边界丢失**（LangGraph 每个 node 用 `copy_context() + asyncio.create_task(context=...)` 隔离运行）。

**9 个工具函数**: createScenario, renameScenario, deleteScene, clearEntities, queryScenario, queryEntities, addPointEntity, createSGP4Orbit, updateSGP4Orbit。每个工具函数调 `bridge.send_tool_call(namespace, tool_func, args)`（tool_func 为函数名），`StreamBridge` 依次 emit `tool_start`/`tool_args` → 前端 Cesium 执行后通过 `POST /tool-result` 回告 → resolve Future → emit `tool_result`/`tool_end`。scene 写工具（create/rename/delete/query）签名加 `runtime: ToolRuntime` 第一参数（langgraph 标准 API），从 `runtime.tool_call_id` 拿 ID 构造 `Command(update={...})` 返回。详细参数格式见 README。

### 场景依赖处理

三层保障（fail-fast 优先）：
1. **`ToolValidationMiddleware`**（后端硬校验，最新一层）：工具调用前检查 `request.state.get("current_scene_name")`，无场景时**返回 `Command(goto=END)`**（A 方案短路，update 里塞携带 NO_SCENE 的 ToolMessage，shortcut 取自 `response_constants.SHORTCUT_RESPONSES["no_scene"]`）。ToolMessage 关闭 AI 的 tool_call（LLM API 协议要求），Command(goto=END) 跳过子 Agent 后续 LLM 调用，**状态由 LangGraph 持久化到 checkpointer**——多轮对话上下文完整。挂在每个子 Agent 上（子 Agent 有独立 middleware 列表；Orchestrator 不绑定工具组故不挂 `ToolValidationMiddleware`，自身通过 `PrimaryAgentMiddleware` 提供运行时护栏，Orchestrator 的中间件不向子 Agent 传递）
2. **Prompt 规则**：Orchestrator system prompt 中明确规则：创建实体前必须确保场景已创建
3. **前端校验**：前端收到工具指令时也会检查场景状态，未创建则返回错误

`current_scene_name` 由前端在 `POST /chat` 请求体（`ChatRequest.current_scene_name`，来源：`yyastk.CurrentScenario?.dataSource?.name`）中携带，由 SSE handler 通过 `astream_events` input 注入到 `SpaceAgentState`，中间件从 `request.state` 读取，scene 工具通过 `Command(update=...)` 写入。

### 动态提示词注入（DYNAMIC_PROMPT）

`agents_dynamic_prompt`（`middleware/dynamic_prompt.py`，用 deepagents 内置 `@dynamic_prompt` 装饰器声明）挂在 orchestrator 和所有子 Agent 上，每次 LLM 调用前把 `request.state["current_scene_name"]` 当前值通过 `append_to_system_message` 追加到 system message 末尾（如「当前场景: 测试场景, 如果不为 None或者空字符串，说明当前场景已打开」），让 LLM 感知前端场景状态。

- **只读不写**：本中间件只从 state 读取，写入由 SSE handler（`api/sse.py`，每轮 `POST /chat` 时通过 `astream_events` input）和 scene 工具（成功后 `Command(update=...)`）负责
- **content blocks 兼容**：用 deepagents `append_to_system_message` 处理 SystemMessage.content 既可能是 str 也可能是 list[ContentBlock] 的情况（MemoryMiddleware/SubagentsMiddleware/TodoListMiddleware 会改写 content 结构），与 deepagents 其他内置 middleware 的拼接风格保持一致

### 任务循环防护（TASK_LOOP_GUARD）

`PrimaryAgentMiddleware`（`middleware/primary_agent_middleware.py`）挂在 Orchestrator 上，通过 ContextVar `orchestrator_task_streak_var` 跟踪连续 `task` 工具调用次数：
- 调用 `task` → streak +1
- 调用其他工具 → streak 重置为 0
- streak ≥ `agent.primary_task_threshold`（默认 20）→ 在 `awrap_model_call` 后置阶段**改写 `ModelResponse`**：构造 `AIMessage`（content 为 `response_util.render()` 渲染后的提示文本，tool_calls 携带 AgentResponse tool_call，args 为 `task_loop_guard` shortcut 字段）+ `structured_response=shortcut`。**不返回 `Command(goto=END)`，不终止 orchestrator 图**，而是替换 LLM 原始输出，让 ToolStrategy 解析 + 后续 `on_chain_end` 事件照常走

替代旧版 `api/websocket.py`（**该文件已删除，`run_agent` 逻辑迁至 `api/sse.py`**）内 `LOOP_THRESHOLD=2` + `task_call_count` 的硬兜底逻辑：
- 优势：状态由 LangGraph 持久化到 checkpointer；复用 `response_constants.SHORTCUT_RESPONSES` 注册表 + `response_util.render` 渲染（与 A 方案共用同一渲染路径）；ContextVar 跨重置语义清晰
- 与 A 方案 `ToolValidationMiddleware` 的关键区别：A 方案在子 Agent 上 `awrap_tool_call` 用 `Command(goto=END)` **终止子 Agent 图**（跳过"解释工具结果"那次 LLM 调用）；`PrimaryAgentMiddleware` 在 orchestrator 上 `awrap_model_call` **不终止 orchestrator 图**，只是替换 LLM 输出为短路响应，后续 ToolStrategy 解析与 `on_chain_end` 渲染照常进行

### 意图追踪与自动续接（INTENT_TRACKING）

三层保障确保用户原始意图不在多轮对话中丢失（**pending_intent 载体是 `AIMessage.additional_kwargs`，不是 ContextVar**——语义自洽：临时元数据绑定到 NO_SCENE 那条 AIMessage，跨轮由 checkpointer 持久化，子 Agent 和 LLM 都看不到）：

**方案 1 — Prompt 规则**（`prompts/orchestrator.md` 的「意图回溯规则」节）：
LLM 在生成 AgentResponse 时必须检查系统消息中的原始意图提示。前置条件满足后，suggestions[0] 必须是原始意图相关建议，而非通用建议。Prompt 同时允许在满足条件时直接 task 委派原始意图（不用等用户再说一遍）。

**方案 2 — AIMessage.additional_kwargs 持久化意图**：
- `PrimaryAgentMiddleware.awrap_model_call` 职责 2：Orchestrator 返回 `NO_SCENE`（命中 `response_constants.INTENTION_TO_CATCH_CODES`）时，**优先沿用历史 pending_intent**（避免被本轮「好的」「创建测试场景」等确认/推进型输入覆盖）；无历史时从消息历史提取用户原始意图（`message_util.extract_last_human_intent` 传 `ignore_messages=_CONFIRMATION_PHRASES`，跳过「好的」「ok」「继续」等纯确认短句），写入本轮 AgentResponse AIMessage 的 `additional_kwargs["pending_intent"]`。若 task 历史中能找到 subagent_type（流程 2），一并写入 `pending_subagent`。
- checkpointer 自动持久化 `additional_kwargs`，跨轮上下文不丢。

**方案 3 — 自动续接 + 动态 subagent 路由**（`PrimaryAgentMiddleware.awrap_model_call` 职责 3）：
Orchestrator 返回 `SCENE_CREATED`/`SCENE_RENAMED`（命中 `response_constants.INTENTION_RESUME_TRIGGER_CODES`，常量化、不再构造可配）且 messages 中存在 pending_intent 时，中间件**改写 ModelResponse**——将 AgentResponse AIMessage 替换为 `task(description=pending_intent, subagent_type=<动态解析>)`。Chain 不会终止，自动执行被中断的原始意图。

**subagent_type 解析优先级**（`agents/subagents_util.py:resolve_subagent_type`，从 PrimaryAgentMiddleware 迁出）：
1. **captured_subagent**（主路径，流程 2）：捕获时从 task 历史记录的 `pending_subagent` 直接取，零延迟
2. **LLM 路由分类**（fallback，流程 1：orchestrator 直接 NO_SCENE 没调过 task）：用 **`build_flash_model()`**（独立 Flash LLM，非主 model）调 `with_structured_output(SubagentClassification)` 分类，prompt 必须含 'json' 字样（LLM 提供商要求 `response_format=json_object` 时 messages 出现该词，否则 400）。`subagents` 列表由 `subagents_util.load_subagents_yaml_config()` 运行时读 yaml 获取，分类结果不在 `valid_names` 集合时 fallback 到 `subagents[0]["name"]` + warning 日志
3. **异常兜底**：LLM 调用异常时 fallback 到 `subagents[0]["name"]`

`PrimaryAgentMiddleware` 构造时**不再**注入 `subagent_summaries` / `model` / `precondition_met_codes`——构造签名简化为 `(thread_id, task_loop_threshold)`，Flash model 在构造函数内 `build_flash_model()` 自建，subagent 列表由 `resolve_subagent_type` 在调用时按需读 yaml（避免 orchestrator 启动期与 yaml 解耦）。

**两条主路径**（覆盖所有 NO_SCENE 触发场景）：
- **流程 1**：orchestrator 直接输出 NO_SCENE（未调 task）→ 捕获时无 captured_subagent → 自动续接时走 LLM 路由分类
- **流程 2**：orchestrator → task(entity-agent) → ToolValidationMiddleware NO_SCENE 短路 → orchestrator 输出 NO_SCENE → 捕获时从 task 历史提取 captured_subagent → 自动续接时零延迟直接用

**数据流示例**：
```
用户: "添加文昌地面站"
→ Orchestrator → 直接 NO_SCENE（流程 1，未调 task）
→ Middleware 捕获: pending_intent="添加文昌地面站", pending_subagent=None
用户: "创建一个新场景"
→ Orchestrator → task(scene-agent) → create_scenario 返回 Command(update={"current_scene_name":"新建场景"})
→ scene-agent state.current_scene_name 通过 _return_command_with_state_update 回传到 orchestrator
→ orchestrator 输出 SCENE_CREATED
→ Middleware 检测 SCENE_CREATED + pending_intent → LLM 分类: "添加文昌地面站" → entity-agent
→ 改写为 task("添加文昌地面站", entity-agent)
→ entity-agent ToolValidationMiddleware 从 request.state 读到 current_scene_name="新建场景"（非 None）
→ add_point_entity → ENTITY_ADDED
→ Orchestrator → 最终 AgentResponse
```

详细设计见对话记录 2026-06-28，"current_scene_name 迁移到 state + 自动续接路由分类"。

### 能力边界处理（防 suggestions 越界 + 链式幻觉）

三层保障：
1. **`ResponseCode` 枚举**（`models/response_schema/agent_struct_response.py`）：单一数据源，含 `OUT_OF_SCOPE`（LLM 在 prompt 约束下自觉输出，处理能力外请求）+ `TASK_LOOP_GUARD`（由 `PrimaryAgentMiddleware` 在 task 死循环时自动注入）等 code。LLM 输出未知 code 时 Pydantic 抛 ValidationError（容错降级 Phase 2 处理）
2. **Prompt 规则**：orchestrator/scene_agent/entity_agent 三个 prompt 都明确「能力外请求必须用 OUT_OF_SCOPE」「suggestions 必须对应实际工具」
3. **数据层硬约束**（兜底）：
   - `tools/registry.py:get_suggestion_candidates(group_names)` 按当前 agent 工具组从工具 description 首句提取候选集
   - `ToolValidationMiddleware.awrap_model_call`（新增）在 LLM 调用前把候选集写入 `current_suggestion_candidates_var` ContextVar
   - `AgentResponse.suggestions` 加 `@field_validator`，按候选集做子串双向匹配过滤越界建议，命中过滤时记 warning 日志

候选集生成方式（description 提取）：当前用 `_extract_first_sentence` 正则提取 description 首段首句（如「创建航天场景。场景是所有实体的容器...」→「创建航天场景」）。前提是工具 description 第一段第一句必须是用户视角的能力描述。Phase 2 升级路径见「待优化项」。

### 工具组管理

工具按工具组（tool group）组织，每个子 Agent 通过 `config/subagents.yaml` 声明自己需要哪些工具组。`tools/registry.py` 用标准 `import` 静态导入所有 `@tool` 函数，按组分组并提供 `get_tools(["group_name"])` 接口。新增工具组只需：在 `tools/<group>/tools.py` 写 `@tool` 函数 + 在 `registry.py` 加一行 import + 一行字典条目。

### SSE+POST 协议

**POST 端点（前端→后端）**：

| 端点 | body 关键字段 | 说明 |
|------|---------------|------|
| `POST /api/v1/space/chat` | `content, thread_id, message_id, current_scene_name` | 响应体是 `text/event-stream`，逐帧推 SSE 事件直到 `done`/`error`。同 `thread_id` 并发重入返 409 Conflict |
| `POST /api/v1/space/tool-result` | `tool_func, args, tool_call_id, thread_id, success, message, data, code` | 短请求，响应 `{ok: true}`。handler 通过 `SessionManager.get_bridge(thread_id)` 定位 StreamBridge，按 `tool_call_id` resolve Future |
| `POST /api/v1/space/chat/{thread_id}/resume` | `{resume: {...}}` | **协议就位，暂返 501 NotImplemented**（interrupt 续跑，下一步独立任务） |

**SSE 事件（后端→前端）**：帧格式 `event: <type>\ndata: <json>\n\n`，`done`/`error` 为终态帧，发送后流关闭、session 注销。

| event | data 字段 | 终态？ |
|-------|-----------|--------|
| `token` | `content, source, thread_id` | 否（`source` 当前统一为 `model`/`agent`，无法区分 agent） |
| `tool_start` | `tool_func, namespace, tool_call_id, thread_id` | 否 |
| `tool_args` | `tool_func, tool_call_id, args, thread_id` | 否 |
| `tool_result` | `tool_func, tool_call_id, result, thread_id` | 否 |
| `tool_end` | `tool_func, tool_call_id, thread_id` | 否 |
| `interrupt` | `interrupt_id, type, message, thread_id` | 否（**协议占位，暂不触发**） |
| `done` | `thread_id, content`（content=`response_util.render()` 最终回复） | ✅ 是 |
| `error` | `thread_id, message` | ✅ 是 |

### 环境配置

- `config/application.yaml` 通用配置，`config/{dev,staging,prod}.yaml` 环境覆盖，通过 `APP_ENV` 环境变量切换
- `config/subagents.yaml` 子 Agent 声明式配置，路径解析复用 `CONFIG_DIR`（与 application.yaml 同目录）
- `config/response_templates.yaml` 响应模板
- `config/knowledge/` 领域知识（如 AGENTS.md），外部化管理，生产环境可动态修改（通过 memory 参数加载）
- `.env` 存放 API Key 等敏感信息，`.gitignore` 排除，`.env.example` 为模板
- `config/application.yaml` 直接维护 server、logging、agent、flash_model、observability 等基础运行配置
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 统一配置主 OpenAI 兼容接口，切换提供商只需修改这三个值
- `LLM_FLASH_API_KEY` / `LLM_FLASH_BASE_URL` / `LLM_FLASH_MODEL` 配置 **Flash LLM**（专供 `PrimaryAgentMiddleware` 路由分类等轻量调用，可与主 LLM 同实例或独立更便宜的实例）
- `config/application.yaml` 的 `llm.enable_thinking`（写在 `agent` 节，由 `LLMConfig` 读取）和 `flash_model.enable_thinking` 分别控制主 LLM 与 Flash LLM 是否透传 `enable_thinking`，仅支持 `true/false`。**注意**：`enable_thinking` 已从 `AgentConfig` 迁出，旧引用 `settings.agent.enable_thinking` 已失效
- `config/application.yaml` 的 `agent.primary_task_threshold` 控制 orchestrator 连续调用 `task` 的保护阈值
- **可观测性（Phase 1A-1）**：`config/application.yaml` 的 `observability:` 段控制 OTel + Langfuse v3 集成：
  - `enabled: false`（默认）→ 全链路 NoOp，业务零开销、零依赖；`enabled: true` 时需要可访问的 Langfuse 实例
  - `langfuse_endpoint` / `langfuse_public_key` / `langfuse_secret_key` 凭证从 `.env` 注入（`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`），keys 为空时 SDK 优雅降级（warning 但不崩溃）
  - `sampler_ratio` 控制采样率（0.0-1.0，生产可降到 0.1）
  - Langfuse v3 自部署：`docker compose --env-file .env -f docker/observability/docker-compose.yml up -d`（6 服务：langfuse-web/worker + clickhouse + redis + postgres + minio，端口 3000），首次启动通过 `LANGFUSE_INIT_PROJECT_*` 自动创建项目。**`--env-file .env` 必须带**：`.env` 在仓库根目录，而 compose 用 `-f` 时默认从 compose 文件所在目录 `docker/observability/` 找 `.env`，不加会让 SALT / ENCRYPTION_KEY / NEXTAUTH_SECRET 为空、Langfuse 启动失败
  - 业务 span 埋点位置：`PrimaryAgentMiddleware.awrap_model_call/awrap_tool_call`（`orchestrator.llm` / `orchestrator.task` / `orchestrator.tool.<name>`）、`SubagentToolValidationMiddleware.awrap_model_call/awrap_tool_call`（`subagent.llm` / `tool.<name>`）、`api/sse.py:run_agent`（`agent.session` root span）。各业务 span 通过 `tracing.set_span_io()` 设 `input.value`/`output.value`（observation IO，`message_util.serialize_messages`/`serialize_model_response` 序列化 messages/ModelResponse）
  - **trace root**：`main.py` 的 `FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/api/v1/space/chat")` 排除 SSE 路径，让手动 span `agent.session`（传输无关命名，未来换 transport 也不用改名）成为 trace root（否则 FastAPI 自动 server span 会包裹整条 SSE 流当 root，无业务 IO 且 name 经常为空，导致 Langfuse Traces 列表 name/input/output 全空）；`agent.session` 的 input/output 按 root observation 规则自动成为 trace 级 IO
  - structlog 已注入 `trace_id` / `span_id` 到每条日志，便于 Loki/ELK 与 Langfuse 串联
- 环境差异：dev(全局INFO+项目DEBUG+Spring风格控制台+不写文件)、staging(INFO+JSON+写文件)、prod(WARNING+JSON+30备份)
- 支持按包名单独控制日志级别（如 `openai: WARNING`、`space_aiagent: DEBUG`），通过 `logging.loggers` 配置

## 参考资源

- 原有 DEMO 代码：https://gitee.com/910922164/space-aiagent
- 前端交互代码（仅参考，不修改）：`https://gitee.com/910922164/space2024/tree/master/plugins/sceneAgent`
- 本项目定位：在原有 DEMO 基础上升级为生产级架构

### 架构约定

- **配置分离**: YAML 管业务配置，.env 管敏感信息，不提交到 Git
- **结构化日志**: JSON 格式，支持控制台 + 文件轮转，可接入 ELK；每条日志带 `trace_id` / `span_id`（来自 OTel current span，可观测性 disabled 时省略）
- **可观测性对业务零依赖**（Phase 1A-1 原则）：`observability.enabled=false` 或 Langfuse 宕机时业务必须照常运行，OTel SDK 默认提供 NoOp Tracer 零开销兜底；中间件用 `optional_span()` context manager 包裹，enabled=false 时是 no-op
- **包管理**: pyproject.toml 定义依赖，scripts/gen_requirements.py 生成 requirements.txt 供 CI/CD

## 代码风格

- 使用 ruff 进行 lint 和 format
- 类型注解：函数参数和返回值必须有类型注解
- 中文注释：业务文件中使用中文注释说明实现步骤
