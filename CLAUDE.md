# space-aiagent

航天分析平台智能助手 - 基于 DeepAgents (LangChain) 的多 Agent 系统

## 项目概述

为上海航天研究院 805 所定制的航天分析平台（基于 Cesium 的航天 GIS 系统）提供 AI 智能助手能力。

核心通信链路：前端交互端 ←WebSocket→ 后端 Agent 服务。前端 Cesium 是 JS 库，所有场景操作在前端执行，Agent 不直接操作场景，而是生成指令通过 WebSocket 发送给前端调用 Cesium API。

业务约束：创建实体（卫星等）前必须先有场景，前端会校验此依赖关系。

## 技术栈

- Python 3.13
- FastAPI (Web + WebSocket)
- deepagents + LangGraph (Agent Harness)
- langchain-openai (DeepSeek / Qwen 兼容接口)
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
│   └── websocket.py        # WebSocket 端点（astream_events 流式执行）
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
│   ├── messages.py         # WebSocket 消息类型
│   └── response_schema/    # AgentResponse 模型包（拆分自原 response_schema.py）
│       ├── agent_struct_response.py # AgentResponse + ResponseCode 枚举（suggestions 越界过滤）
│       ├── response_constants.py    # INTENTION_TO_CATCH_CODES / INTENTION_RESUME_TRIGGER_CODES + SHORTCUT_RESPONSES（A 方案预构建 AgentResponse 注册表，原 bridge/response_shortcut.py 迁入）
│       └── response_util.py         # find_agent_response_tool_call / get_agent_response_code_from_model_response / render（summary + suggestions 出口渲染，原 bridge/response_renderer.py 迁入）
├── bridge/                 # 远程工具桥接层
│   ├── ws_bridge.py        # WebSocket Future 桥接
│   └── session.py          # 会话管理
└── infrastructure/         # 基础设施
    ├── config.py           # 配置管理（YAML + .env，含 LLMConfig + LLMFlashConfig）
    ├── llm.py              # build_model() + build_flash_model()（OpenAI 兼容，Flash 专供路由分类等轻量调用）
    ├── logging.py          # 结构化日志
    ├── database.py         # SQLite 持久化（AsyncSqliteSaver checkpointer）
    ├── response_template_yaml.py # 加载 config/response_templates.yaml → DEFAULT_TEMPLATES（仅 SHORTCUT_RESPONSES 用作 summary 默认值，不再做 {var} 渲染）
    └── utils/              # 通用工具
        ├── string_util.py  # snake/camel 转换、args_to_camel、truncate、flat_tuple_list
        ├── message_util.py # 消息提取/构造工具（extract_last_task / extract_last_human_intent / extract_last_existing_intent / build_task_response / build_primary_agent_response / msg_preview）
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
                        用户输入(WebSocket)
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
        Remote Tool Bridge (WebSocket)
              │              │
              ▼              ▼
         Cesium 前端执行
```

- **Orchestrator**: 意图识别、任务规划、子 Agent 调度。不直接绑定工具，只持有工具组摘要。使用 `ToolStrategy(AgentResponse)` 强制结构化输出，state_schema 用 `SpaceAgentState`（`agents/state.py`，含 `current_scene_name` 字段，跨 task 边界自动同步）。middleware 顺序为 `PrimaryAgentMiddleware`（task 死循环硬兜底 + 意图捕获 + 自动续接 + 内联 LLM/工具调用日志，详见「任务循环防护」和「意图追踪与自动续接」）→ `ResponseStabilizationMiddleware`（**已退役占位**，模板渲染废弃后无职责）→ `agents_dynamic_prompt`（动态注入 current_scene_name 到 system message）。**LoggingMiddleware 已退役**——orchestrator 不再挂载，可观测性职责由 `PrimaryAgentMiddleware.awrap_model_call` / `awrap_tool_call` 内联日志承担（类保留供未来复用）
- **子 Agent**: 通过 `config/subagents.yaml` 声明式配置，`agents/subagents.py` 加载。新增 Agent 改配置 + 提示词 + `tools/registry.py` 注册。middleware 顺序为 `ToolValidationMiddleware(tool_groups=...)` → `ResponseStabilizationMiddleware`（**已退役占位**）→ `agents_dynamic_prompt`
- **Analysis Agent**: 数据分析（未来扩展），独立领域单独扩展
- **Agent 执行**: `astream_events` 流式执行，`on_tool_start`（工具进度提示钩子）+ `on_chain_end`（读 `output.structured_response` + `response_util.render()` 出口渲染发送）事件驱动。task 死循环兜底已下沉到 `PrimaryAgentMiddleware`（阈值 20，详见「任务循环防护」）。详见 `readme/python教程.md` 9.5 节
- **结构化输出**: `ToolStrategy` 利用模型 tool calling API 强制输出 `AgentResponse` JSON。websocket `on_chain_end` 事件读 `output.structured_response`，调 `response_util.render()` 渲染为自然语言发前端——`render()` 直接返回 `summary` + 可选 suggestions 块（**模板渲染已废弃**，YAML 模板仅作 SHORTCUT_RESPONSES 的 summary 默认值）。**A 方案短路**：已知确定性 case（如 `NO_SCENE`）由 `ToolValidationMiddleware` 在工具调用前识别，返回 `Command(goto=END)` 短路（update 里塞携带 NO_SCENE 的 ToolMessage 关闭 AI 的 tool_call，跳过子 Agent 后续 LLM 调用）。state 由 LangGraph 自动持久化到 checkpointer（多轮对话上下文完整）。注册表在 `models/response_schema/response_constants.SHORTCUT_RESPONSES`

### 远程工具桥接（核心机制）

工具在后端定义但实际在前端 Cesium 执行。通过 asyncio.Future 桥接：

```
Agent 调用工具 → bridge.send_tool_call() → WebSocket 发送指令到前端
                                                      ↓
Agent 得到结果 ← await Future ← bridge.resolve() ← WebSocket 收到前端结果
```

每次工具调用创建 Future 绑定到唯一 tool_call_id，WebSocket 收到前端 tool_result 时根据 ID resolve。

**Bridge 注入方式**: `bridge.bridge_var` (ContextVar)，由 websocket handler 在创建 Agent 前通过 `bridge_var.set(bridge)` 注入，工具函数通过 `bridge_var.get()` 获取。

**场景上下文注入（state_schema 双向同步）**: `current_scene_name` 通过 `SpaceAgentState`（`agents/state.py`）持久化，由 websocket handler 在 `astream_events` 的 input 中注入初值，scene 工具（`create_scenario`/`rename_scenario`/`query_scenario` 成功路径）通过返回 `Command(update={"current_scene_name": ...})` 更新，`ToolValidationMiddleware` 通过 `request.state.get("current_scene_name")` 读取。**关键**：deepagents `task` 工具自动双向同步 state（父→子 `_validate_and_prepare_state`，子→父 `_return_command_with_state_update`，排除 `_EXCLUDED_STATE_KEYS = {"messages","todos","structured_response","skills_*","memory_contents"}`），所以 scene-agent 创建场景后写入的 `current_scene_name` 会自动回传到 orchestrator，再自动传给后续 task 调用的 entity-agent——**避免 ContextVar 跨 task 边界丢失**（LangGraph 每个 node 用 `copy_context() + asyncio.create_task(context=...)` 隔离运行）。

**9 个工具函数**: createScenario, renameScenario, deleteScene, clearEntities, queryScenario, queryEntities, addPointEntity, createSGP4Orbit, updateSGP4Orbit。每个工具函数发送 `tool_call` 消息（tool_func 字段为函数名），前端执行后返回 `tool_result`。scene 写工具（create/rename/delete/query）签名加 `runtime: ToolRuntime` 第一参数（langgraph 标准 API），从 `runtime.tool_call_id` 拿 ID 构造 `Command(update={...})` 返回。详细参数格式见 README。

### 场景依赖处理

三层保障（fail-fast 优先）：
1. **`ToolValidationMiddleware`**（后端硬校验，最新一层）：工具调用前检查 `request.state.get("current_scene_name")`，无场景时**返回 `Command(goto=END)`**（A 方案短路，update 里塞携带 NO_SCENE 的 ToolMessage，shortcut 取自 `response_constants.SHORTCUT_RESPONSES["no_scene"]`）。ToolMessage 关闭 AI 的 tool_call（LLM API 协议要求），Command(goto=END) 跳过子 Agent 后续 LLM 调用，**状态由 LangGraph 持久化到 checkpointer**——多轮对话上下文完整。挂在每个子 Agent 上（子 Agent 有独立 middleware 列表；Orchestrator 不绑定工具组故不挂 `ToolValidationMiddleware`，自身通过 `PrimaryAgentMiddleware` 提供运行时护栏，Orchestrator 的中间件不向子 Agent 传递）
2. **Prompt 规则**：Orchestrator system prompt 中明确规则：创建实体前必须确保场景已创建
3. **前端校验**：前端收到工具指令时也会检查场景状态，未创建则返回错误

`current_scene_name` 由前端在 `user_input` 消息中携带（来源：`yyastk.CurrentScenario?.dataSource?.name`），由 WebSocket handler 通过 `astream_events` input 注入到 `SpaceAgentState`，中间件从 `request.state` 读取，scene 工具通过 `Command(update=...)` 写入。

### 动态提示词注入（DYNAMIC_PROMPT）

`agents_dynamic_prompt`（`middleware/dynamic_prompt.py`，用 deepagents 内置 `@dynamic_prompt` 装饰器声明）挂在 orchestrator 和所有子 Agent 上，每次 LLM 调用前把 `request.state["current_scene_name"]` 当前值通过 `append_to_system_message` 追加到 system message 末尾（如「当前场景: 测试场景, 如果不为 None或者空字符串，说明当前场景已打开」），让 LLM 感知前端场景状态。

- **只读不写**：本中间件只从 state 读取，写入由 websocket handler（每轮 user_input 时通过 astream_events input）和 scene 工具（成功后 `Command(update=...)`）负责
- **content blocks 兼容**：用 deepagents `append_to_system_message` 处理 SystemMessage.content 既可能是 str 也可能是 list[ContentBlock] 的情况（MemoryMiddleware/SubagentsMiddleware/TodoListMiddleware 会改写 content 结构），与 deepagents 其他内置 middleware 的拼接风格保持一致

### 任务循环防护（TASK_LOOP_GUARD）

`PrimaryAgentMiddleware`（`middleware/primary_agent_middleware.py`）挂在 Orchestrator 上，通过 ContextVar `orchestrator_task_streak_var` 跟踪连续 `task` 工具调用次数：
- 调用 `task` → streak +1
- 调用其他工具 → streak 重置为 0
- streak ≥ `agent.primary_task_threshold`（默认 20）→ 在 `awrap_model_call` 后置阶段**改写 `ModelResponse`**：构造 `AIMessage`（content 为 `response_util.render()` 渲染后的提示文本，tool_calls 携带 AgentResponse tool_call，args 为 `task_loop_guard` shortcut 字段）+ `structured_response=shortcut`。**不返回 `Command(goto=END)`，不终止 orchestrator 图**，而是替换 LLM 原始输出，让 ToolStrategy 解析 + 后续 `on_chain_end` 事件照常走

替代旧版 `api/websocket.py` 内 `LOOP_THRESHOLD=2` + `task_call_count` 的硬兜底逻辑：
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

### WebSocket 消息协议

| 方向 | 类型 | 关键字段 |
|------|------|---------|
| 前端→后端 | `user_input` | content, thread_id, message_id, current_scene_name |
| 前端→后端 | `tool_result` | tool_func, args, tool_call_id, success, message |
| 后端→前端 | `ai_message` | content, thread_id |
| 后端→前端 | `tool_call` | tool_func, tool_func_args, tool_call_id, thread_id |
| 后端→前端 | `end` | thread_id |
| 后端→前端 | `error` | message, thread_id |

### 环境配置

- `config/application.yaml` 通用配置，`config/{dev,staging,prod}.yaml` 环境覆盖，通过 `APP_ENV` 环境变量切换
- `config/subagents.yaml` 子 Agent 声明式配置，路径解析复用 `CONFIG_DIR`（与 application.yaml 同目录）
- `config/response_templates.yaml` 响应模板
- `config/knowledge/` 领域知识（如 AGENTS.md），外部化管理，生产环境可动态修改（通过 memory 参数加载）
- `.env` 存放 API Key 等敏感信息，`.gitignore` 排除，`.env.example` 为模板
- `config/application.yaml` 直接维护 server、logging、agent、flash_model 等基础运行配置
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 统一配置主 OpenAI 兼容接口，切换提供商只需修改这三个值
- `LLM_FLASH_API_KEY` / `LLM_FLASH_BASE_URL` / `LLM_FLASH_MODEL` 配置 **Flash LLM**（专供 `PrimaryAgentMiddleware` 路由分类等轻量调用，可与主 LLM 同实例或独立更便宜的实例）
- `config/application.yaml` 的 `llm.enable_thinking`（写在 `agent` 节，由 `LLMConfig` 读取）和 `flash_model.enable_thinking` 分别控制主 LLM 与 Flash LLM 是否透传 `enable_thinking`，仅支持 `true/false`。**注意**：`enable_thinking` 已从 `AgentConfig` 迁出，旧引用 `settings.agent.enable_thinking` 已失效
- `config/application.yaml` 的 `agent.primary_task_threshold` 控制 orchestrator 连续调用 `task` 的保护阈值
- 环境差异：dev(全局INFO+项目DEBUG+Spring风格控制台+不写文件)、staging(INFO+JSON+写文件)、prod(WARNING+JSON+30备份)
- 支持按包名单独控制日志级别（如 `openai: WARNING`、`space_aiagent: DEBUG`），通过 `logging.loggers` 配置

## 参考资源

- 原有 DEMO 代码：https://gitee.com/910922164/space-aiagent
- 前端交互代码（仅参考，不修改）：`https://gitee.com/910922164/space2024/tree/master/plugins/sceneAgent`
- 本项目定位：在原有 DEMO 基础上升级为生产级架构

### 架构约定

- **配置分离**: YAML 管业务配置，.env 管敏感信息，不提交到 Git
- **结构化日志**: JSON 格式，支持控制台 + 文件轮转，可接入 ELK
- **包管理**: pyproject.toml 定义依赖，scripts/gen_requirements.py 生成 requirements.txt 供 CI/CD

## 代码风格

- 使用 ruff 进行 lint 和 format
- 类型注解：函数参数和返回值必须有类型注解
- 中文注释：业务文件中使用中文注释说明实现步骤

## 待优化项

### 扩展 Command 短路覆盖更多 code（Phase 2）

**现状**: A 方案（`Command(goto=END)` 短路）目前只覆盖 `NO_SCENE`，因为 `ToolValidationMiddleware` 已经能在工具执行前识别这种失败模式。其他 code（`SCENE_CREATED`、`ENTITY_CREATED`、`ENTITIES_LIST` 等）仍走 LLM 生成 AgentResponse——出口由 `response_util.render()` 直接渲染 summary + suggestions，每次仍付一次 LLM 调用成本。

**优化方向**:
- 工具/中间件返回结构化错误，带 `code` 字段（当前 `tool_validation.py` 和工具函数都只返回 `{success, message, data}`，缺 `code`）
- 在 `models/response_schema/response_constants.py` 的 `SHORTCUT_RESPONSES` 追加新条目
- 中间件或工具执行后检测 `(tool_func, code)`，命中则返回 `Command(goto=END)`

**更激进的 Phase 2+**：用 `Command(graph=Command.PARENT, goto=END)` 跨层级终止 orchestrator，省掉 orchestrator LLM 调用。代价是 websocket 渲染路径要重做（AgentResponse 不再从 LLM 事件来，要从 state 读）。详见 `readme/python教程.md` 第 25 节"LangGraph Command"。

**优先级**: 中（A 方案短路 + 出口 render 已解决渲染一致性的核心问题；Phase 2 主要为降低 Token 成本和延迟，等高频 case 出现时再推进）

### 能力边界处理的 Phase 2 候选（2026-06-25 本次未做）

**现状**：`ResponseCode` 枚举 + OUT_OF_SCOPE 模板 + suggestions validator 已闭环，但仍依赖 LLM 自觉使用 OUT_OF_SCOPE code + 走完整 LLM 调用。suggestions 越界靠 Pydantic validator 兜底过滤。

**Phase 2 优化方向**：

1. **LLM 非法 code 容错**（A1 容错）：当前 LLM 输出枚举外的 code 时，Pydantic 在 ToolStrategy 解析层抛 ValidationError。需要在解析层捕获并降级为 `ResponseCode.OUT_OF_SCOPE` + LLM 原始文本作 summary 兜底。

2. **OUT_OF_SCOPE 走 A 方案短路**：识别能力外请求模式（命中工具不存在/参数无法满足），在 `models/response_schema/response_constants.py` 的 `SHORTCUT_RESPONSES` 追加条目，`ToolValidationMiddleware` 提前返回 `Command(goto=END)`，省一次 LLM 调用。

3. **suggestion 候选集生成升级**：当前用 description 首句提取（方案 B），简单但精度依赖 description 写法。升级路径：
   - 方案 C：手工 catalog（每个工具文件声明 `TOOL_SUGGESTIONS = {...}`）
   - 方案 D：LLM 启动时生成（registry 加载时对每个 tool 调一次 LLM，缓存到 `.suggestion_cache.json`，description hash 失效）
   - 方案 E：混合（C 优先，B 兜底）

   当工具 description 质量不稳定或工具数增长到 20+ 时再升级。

**优先级**: 低（当前三层保障已能兜住用户日志里的 case；Phase 2 主要为降低 Token 成本和提升建议精度，等高频场景出现再推进）。
