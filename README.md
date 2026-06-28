# space-aiagent

航天分析平台智能助手 - 基于 DeepAgents (LangChain) 的多 Agent 系统

## 项目背景

本项目为上海航天研究院 805 所定制的航天分析平台（基于 Cesium 的航天 GIS 系统）提供 AI 智能助手能力。

### 业务场景

航天分析平台是一个基于 Cesium（前端 JS 库）的 GIS 系统，核心能力均以前端技术实现。智能助手的交互入口在前端，用户通过自然语言提出需求（如"创建场景"、"添加卫星"），后端 Agent 服务解析意图后，生成操作指令通过 WebSocket 发送到前端，前端根据指令调用 Cesium API 完成操作。

### 通信架构

```
用户输入 → 智能助手交互端（前端） → WebSocket → AI Agent 服务（后端）
                                                    ↓
前端调用 Cesium API ← 操作指令 ← WebSocket ← Agent 工具调用
```

### 核心业务流程

以"创建卫星"为例：
1. 用户输入"我要创建卫星"，前端携带 `current_scene_name`（来自 Cesium CurrentScenario）
2. 智能体识别意图，调用 entity 工具
3. 后端 `ToolValidationMiddleware` 检查 `current_scene_name`：
   - 如果为 null → **A 方案短路**：中间件返回 `Command(goto=END)` 携带 NO_SCENE 的 ToolMessage，强制终止子 Agent 图（跳过"解释工具结果"那次 LLM 调用），ToolMessage 内容回流到 orchestrator，由 orchestrator LLM 生成 AgentResponse，**状态由 LangGraph 自动持久化**保证多轮对话上下文完整
   - 如果非 null → 工具通过 WebSocket 发指令到前端

   > orchestrator 上的 `PrimaryAgentMiddleware` 是另一种短路机制：监测连续 `task` 调用 ≥ 20 次时，在 `awrap_model_call` 后置阶段直接**改写 `ModelResponse`**（构造一个携带 AgentResponse tool_call 的 AIMessage），输出 `task_loop_guard` 模板。与 A 方案 `ToolValidationMiddleware` 用 `Command(goto=END)` 终止子 Agent 图不同——`PrimaryAgentMiddleware` 不终止图，而是把 LLM 输出替换为短路响应（详见 CLAUDE.md「任务循环防护」）。
4. 前端收到指令后调用 Cesium API 执行创建卫星
5. 前端返回结果给后端，渲染分两条路径（同一 `ResponseRenderer`，输出一致）：
   - **中间件路径**：`ResponseStabilizationMiddleware.awrap_model_call` 后置处理时调 `ResponseRenderer.render()` 把渲染文本写回 `AIMessage.content`，由 checkpointer 持久化供下一轮 LLM cross-turn 上下文使用
   - **websocket 出口路径**：`on_chain_end` 事件读 `output.structured_response` 字段，独立调 `ResponseRenderer.render()` 渲染后发给前端。**不走 messages 列表**，避免 `Overwrite` 包装导致的 `'Overwrite' object is not reversible` bug

## 技术选型

| 类别 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.13 | 现代异步生态 |
| Web 框架 | FastAPI | 异步支持、自动文档、WebSocket 内建 |
| Agent Harness | [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) | LangChain 团队开发的 Agent Harness，内置任务规划、子 Agent 生成、长期记忆 |
| Agent 运行时 | LangGraph | DeepAgent 底层运行时，支持持久化执行、流式输出（astream_events） |
| 结构化输出 | ToolStrategy | 利用模型 tool calling API 强制输出 AgentResponse 结构，保证回复一致性 |
| 可观测性 | LoggingMiddleware | Agent 中间件，记录 LLM 调用决策和工具执行过程 |
| LLM 接口 | langchain-openai | OpenAI 兼容接口，统一支持 DeepSeek 和阿里 DashScope（Qwen） |
| 持久化 | SQLite + aiosqlite | 开发阶段使用，后续可迁移 PostgreSQL |
| 配置管理 | YAML + .env | YAML 放业务配置，.env 放敏感信息（不提交 Git），knowledge 外部化到 config/ 可动态修改 |
| 日志 | structlog | 结构化 JSON 日志，控制台 + 文件轮转，可接入 ELK |
| 代码质量 | ruff + pre-commit | 格式化 + lint + Git hooks |

## 架构设计

### 多 Agent + 工具组管理

```
                        用户输入(WebSocket)
                              │
                              ▼
                    ┌─────────────────┐
                    │  Orchestrator   │  主控Agent：意图识别、任务规划
                    │  (DeepAgents)    │  只知道工具组摘要列表
                    │  + ToolStrategy │  结构化输出 AgentResponse
                    │  + LoggingMW    │  执行日志中间件
                    │  + astream      │  流式事件驱动
                    └───────┬─────────┘
                            │ subagent 调度
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │  Scene   │  │  Entity  │  │ Analysis │
        │  Agent   │  │  Agent   │  │  Agent   │  ← 未来扩展
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

### Agent 职责

| Agent | 职责                         | 加载的工具组 |
|-------|----------------------------|-------------|
| Orchestrator | 意图识别、任务规划、子 Agent 调度、结构化输出 | ToolStrategy(AgentResponse) + memory（AGENTS.md） + LoggingMiddleware + PrimaryAgentMiddleware + ResponseStabilizationMiddleware + agents_dynamic_prompt |
| Scene Agent | 场景创建/重命名/删除/查询             | scene_management (6 个工具) + ToolValidationMiddleware + ResponseStabilizationMiddleware + agents_dynamic_prompt |
| Entity Agent | 实体创建/SGP4轨道/样式更新           | entity_management + orbit_management (4 个工具) + ToolValidationMiddleware + ResponseStabilizationMiddleware + agents_dynamic_prompt |
| Analysis Agent | 数据分析（未来扩展）                 | （未来扩展） |

> 子 Agent 通过 `config/subagents.yaml` 声明式配置，新增 Agent 只需加 YAML 条目 + `prompts/` 加提示词文件 + `tools/registry.py` 注册工具组。

### 动态提示词注入

`agents_dynamic_prompt`（`middleware/dynamic_prompt.py`，用 `@dynamic_prompt` 装饰器声明）挂在 orchestrator 和所有子 Agent 上，每次 LLM 调用前把 `request.state["current_scene_name"]` 当前值追加到 system message 末尾（如「当前场景: 测试场景, 如果不为 None或者空字符串，说明当前场景已打开」），让 LLM 感知前端场景状态。`current_scene_name` 由前端在 `user_input` 携带，websocket handler 通过 `astream_events` input 注入到 `SpaceAgentState`（`agents/state.py`），scene 工具成功后通过 `Command(update={"current_scene_name": ...})` 写入，本中间件只读不写。**关键**：deepagents `task` 工具自动双向同步 state（父↔子），所以 scene-agent 创建场景后写入的 `current_scene_name` 会自动传给后续 task 调用的 entity-agent——避免 ContextVar 跨 task 边界丢失。

### 远程工具桥接

工具在后端定义但实际在前端 Cesium 执行。通过 `asyncio.Future` + `ContextVar` 桥接：

```
Agent 调用工具 → bridge.send_tool_call() → WebSocket 发送指令到前端
                                                      ↓
Agent 得到结果 ← await Future ← bridge.resolve() ← WebSocket 收到前端结果
```

- `bridge_var` (ContextVar)：WebSocket handler 在创建 Agent 前注入，工具函数通过 `get()` 获取
- 每次 `send_tool_call()` 创建 Future 绑定到唯一 `tool_call_id`
- WebSocket 收到 `tool_result` 时根据 ID resolve 对应 Future

### WebSocket 消息协议

#### 前端 → 后端

**用户输入 (`user_input`)**
```json
{
  "type": "user_input",
  "thread_id": "abc-123",
  "content": "帮我创建一个场景",
  "message_id": "msg-001",
  "current_scene_name": "测试场景"
}
```

`current_scene_name` 由前端从 `yyastk.CurrentScenario?.dataSource?.name` 携带，无场景时为 `null`。后端 `ToolValidationMiddleware` 据此做 fail-fast 校验：除场景创建工具外，无场景上下文（`request.state.get("current_scene_name")` 为空）时返回 `Command(goto=END)`（携带 NO_SCENE 的 ToolMessage），终止子 Agent 图——ToolMessage 关闭 tool_call（LLM API 协议要求），Command(goto=END) 跳过子 Agent 后续 LLM 调用，状态由 LangGraph 持久化到 checkpointer。

**工具执行结果 (`tool_result`)** — 前端执行完 Cesium 操作后返回
```json
{
  "type": "tool_result",
  "thread_id": "abc-123",
  "tool_func": "createScenario",
  "tool_call_id": "uuid-xxx",
  "args": {},
  "success": true,
  "message": "场景创建成功",
  "data": {"scenarioName": "测试场景"}
}
```

#### 后端 → 前端

**AI 文本回复 (`ai_message`)**
```json
{"type": "ai_message", "thread_id": "abc-123", "content": "好的，正在为您创建场景"}
```

**工具调用指令 (`tool_call`)** — 让前端执行 Cesium 操作
```json
{
  "type": "tool_call",
  "thread_id": "abc-123",
  "tool_func": "createScenario",
  "tool_func_args": {"sceneName": "测试场景", "centralBody": "Earth"},
  "tool_call_id": "uuid-xxx",
  "message_id": ""
}
```

**对话结束 (`end`)**
```json
{"type": "end", "thread_id": "abc-123"}
```

**错误 (`error`)**
```json
{"type": "error", "thread_id": "abc-123", "message": "工具调用超时: createScenario"}
```

#### 交互时序

```
前端                          后端
  │  user_input ──────────→  │
  │  ←──── ai_message       │  流式状态："正在执行 queryEntities..."
  │  ←──── tool_call        │  Agent 调用工具
  │  tool_result ─────────→  │  前端执行 Cesium 操作
  │  ←──── ai_message       │  Agent 结构化回复（ResponseRenderer 渲染）
  │  ←──── end              │  轮次结束
```

> 流式执行：后端使用 `astream_events` 驱动 Agent，在工具调用前发送 `ai_message` 状态提示，前端可实时感知进度。最终回复由 `ToolStrategy(AgentResponse)` 结构化输出 → **双渲染路径**：(1) `ResponseStabilizationMiddleware.awrap_model_call` 后置渲染写 `AIMessage.content`（供下一轮 LLM 上下文，checkpointer 持久化）；(2) websocket `on_chain_end` 读 `output.structured_response` 独立渲染发给前端（避开 messages 列表的 `Overwrite` 包装问题）。详见 `readme/python教程.md` 9.5 节。

### 工具清单

前端需要实现以下 `tool_func` 对应的方法：

| 工具函数名 (`tool_func`) | 所属工具组 | 参数 (`tool_func_args`) | 说明         |
|--------------------------|-----------|------------------------|------------|
| `createScenario` | scene_management | `{sceneName, centralBody, startTime?, endTime?, description?}` | 创建场景       |
| `renameScenario` | scene_management | `{sceneName}` | 重命名场景      |
| `deleteScene` | scene_management | `{}` | 删除场景       |
| `clearEntities` | scene_management | `{}` | 清除所有实体     |
| `queryScenario` | scene_management | `{sceneName?}` | 查询场景信息     |
| `queryEntities` | scene_management | `{}` | 查询实体列表     |
| `addPointEntity` | entity_management | `{entityType, name, position: {longitude, latitude, height}, properties?}` | 添加实体      |
| `createSGP4Orbit` | orbit_management | `{name, tles, start?, end?}` | 创建 SGP4 轨道 |
| `updateSGP4Orbit` | orbit_management | `{name, color?, glowPower?, taperPower?}` | 更新轨道样式     |

参数中 `?` 表示可选字段。`entityType` 支持的值: `place`, `target`, `facility`, `aircraft`, `missile`, `satellite`, `sensor`, `groundVehicle`, `ship`, `launchVehicle`, `lineTarget`, `areaTarget`。

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
│   └── subagents.py        # 子 Agent 加载器（从 YAML 配置构建）
├── middleware/              # Agent 中间件
│   ├── logging.py          # LoggingMiddleware（LLM 调用/工具执行可观测性）
│   ├── primary_agent_middleware.py # PrimaryAgentMiddleware（orchestrator task 死循环硬兜底，阈值 20，awrap_model_call 改写 ModelResponse 输出 TASK_LOOP_GUARD）
│   ├── dynamic_prompt.py   # agents_dynamic_prompt（每次 LLM 调用前把 current_scene_name 注入 system message）
│   ├── response_stabilization.py # ResponseStabilizationMiddleware（AgentResponse 模板渲染稳定化，写 AIMessage.content）
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
│   ├── schemas.py          # Pydantic 模型（工具参数、API 请求响应）
│   ├── messages.py         # WebSocket 消息类型
│   └── response_schema.py  # AgentResponse 结构化响应模型 + ResponseCode 枚举（ToolStrategy 输出格式）
├── bridge/                 # 远程工具桥接层
│   ├── ws_bridge.py        # WSBridge（Future 桥接 + 消息发送）
│   ├── response_renderer.py # 响应渲染器（纯 YAML 模板渲染；稳定化职责在 ResponseStabilizationMiddleware）
│   ├── response_shortcut.py # A 方案：预构建 AgentResponse 注册表（被中间件转成 ToolMessage 配合 Command 用）
│   ├── session.py          # SessionManager（thread_id → WebSocket 映射）
│   └── __init__.py         # bridge_var (ContextVar) 导出
└── infrastructure/         # 基础设施
    ├── config.py           # 配置管理（YAML + .env + 多环境合并）
    ├── logging.py          # structlog 结构化日志
    └── database.py         # SQLite + AsyncSqliteSaver checkpointer
```

## 环境配置

### 多环境支持

通过 `APP_ENV` 环境变量切换，YAML 中 `${VAR:default}` 语法引用环境变量。

| 环境 | 配置文件 | 日志级别 | 格式 | 文件输出 |
|------|---------|---------|------|---------|
| dev | `config/dev.yaml` | INFO (项目包 DEBUG) | Spring 风格控制台 | 不写文件 |
| staging | `config/staging.yaml` | INFO | JSON | 写文件 |
| prod | `config/prod.yaml` | WARNING | JSON | 写文件，30 个备份 |

### LLM 配置

使用统一的 OpenAI 兼容接口，切换提供商只需修改 `LLM_BASE_URL` 和 `LLM_MODEL`：

```bash
# .env 示例

# DeepSeek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# 或阿里 Qwen（DashScope）
# LLM_API_KEY=sk-xxx
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_MODEL=qwen-plus
```

## 快速开始

```bash
# 1. 复制环境变量配置
cp .env.example .env
# 编辑 .env 填写实际的 API Key

# 2. 创建 conda 环境
conda create -n space-aiagent-v1 python=3.13 -y
conda activate space-aiagent-v1

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 生成 requirements.txt（可选，供 CI/CD 使用）
python scripts/gen_requirements.py

# 5. 安装 pre-commit hooks
pre-commit install

# 6. 启动开发服务器
python -m space_aiagent.main
```

## 常用命令

```bash
# 激活 conda 环境
conda activate space-aiagent-v1

# 启动服务器（开发模式，支持热重载）
python -m space_aiagent.main

# CLI 方式启动
space-aiagent run --host 0.0.0.0 --port 8028 --reload

# 查看工具组列表
space-aiagent tools list

# 查看工具组详情
space-aiagent tools show scene_management

# 运行测试
pytest

# 代码检查
ruff check src/ tests/

# 代码格式化
ruff format src/ tests/
```

## 参考

- 原有航天分析助手智能体仓库：https://gitee.com/910922164/space-aiagent
- 航天分析平台智能体助手前端交互：`https://gitee.com/910922164/space2024/tree/master/plugins/sceneAgent`
