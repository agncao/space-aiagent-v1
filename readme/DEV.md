# DEV.md — 开发参考

## 核心链路时序图：用户查询实体列表

以"看看有哪些实体"为例，展示从用户输入到 Agent 返回结果的完整时序。

```mermaid
sequenceDiagram
    autonumber
    participant U as 👤 用户
    participant F as 🌐 前端交互端<br/>(Cesium GIS)
    participant WS as 🔌 FastAPI<br/>/ws/space
    participant B as 🌉 WSBridge<br/>(Future 桥接)
    participant O as 🧠 Orchestrator<br/>(DeepAgent)
    participant SA as 🛰️ Scene Agent<br/>(子 Agent)
    participant T as 🔧 queryScenario<br/>Entities (Tool)
    participant LLM as 🤖 LLM<br/>(DeepSeek / Qwen)

    %% === 第一阶段：连接建立与消息接收 ===
    rect rgb(240, 248, 255)
        Note over U,LLM: 阶段 1 — WebSocket 连接 & 消息接收
    end

    U->>F: 输入 "看看有哪些实体"
    F->>WS: user_input<br/>{content:"看看有哪些实体", thread_id:"t-001"}
    WS->>WS: 解析消息类型 → USER_INPUT

    %% === 第二阶段：会话与 Agent 准备 ===
    rect rgb(255, 250, 240)
        Note over U,LLM: 阶段 2 — 会话注册 & Agent 创建
    end

    WS->>B: session_manager.register(thread_id, ws)<br/>创建 WSBridge 实例
    WS->>WS: bridge_var.set(bridge)<br/>注入 ContextVar
    WS->>WS: _get_or_create_agent("t-001")<br/>检查缓存 / 首次创建

    opt 首次请求（缓存未命中）
        WS->>WS: load_subagents()<br/>读取 subagents.yaml → get_tools() 构建子 Agent 列表
        WS->>O: create_orchestrator(subagents, checkpointer)<br/>创建 DeepAgent
        O->>LLM: 发送 system_prompt<br/>(含工具组摘要 + 领域知识)
        LLM-->>O: Agent 就绪
        WS->>WS: 缓存 agent 到 _agent_cache
    end

    %% === 阶段 3：Agent 推理 & 子 Agent 调度 ===
    rect rgb(240, 255, 240)
        Note over U,LLM: 阶段 3 — Orchestrator 意图识别 & 子 Agent 调度
    end

    WS->>O: agent.ainvoke({"messages": [HumanMessage("看看有哪些实体")]})
    O->>LLM: 发送 user message + conversation history
    LLM-->>O: 意图分析：查询实体 → 匹配 Skill: scene_management / entity_management

    Note over O: Orchestrator 判断需要查询实体<br/>决定委派给 scene-agent 子 Agent

    O->>SA: 委派任务："查询场景中所有实体"
    SA->>LLM: 发送 scene_agent 提示词 + 任务上下文
    LLM-->>SA: 决定调用工具 query_scenario_entities

    %% === 阶段 4：工具调用 & 远程桥接 ===
    rect rgb(255, 240, 245)
        Note over U,LLM: 阶段 4 — 工具执行 & WebSocket 桥接
    end

    SA->>T: 调用 query_entities()
    T->>T: bridge = bridge_var.get()<br/>获取当前会话 bridge
    T->>B: bridge.send_tool_call(<br/>  "queryEntities", {})

    Note over B: 1. 生成 UUID → tool_call_id<br/>2. 创建 asyncio.Future<br/>3. 缓存到 _pending 字典

    B->>WS: 通过 WebSocket 发送 tool_call 消息
    WS->>F: tool_call<br/>{tool_func:"queryEntities", tool_call_id:"uuid-xxx"}

    Note over F: 前端执行 Cesium API<br/>遍历 currentScenario<br/>.dataSource.entities.values

    F->>WS: tool_result<br/>{success:true, data:{entities:["卫星A","地面站B"]}}
    WS->>B: bridge.resolve_tool_result(result)

    Note over B: 1. 根据 tool_call_id 从 _pending 取出 Future<br/>2. future.set_result({success, message, data})

    B-->>T: await Future 返回 → 工具执行结果
    T-->>SA: 返回实体列表

    %% === 阶段 5：结果汇总 & 返回 ===
    rect rgb(255, 255, 240)
        Note over U,LLM: 阶段 5 — 结果汇总 & 返回前端
    end

    SA->>LLM: 发送工具结果
    LLM-->>SA: 生成中文回复："当前场景共有 2 个实体：卫星A、地面站B"
    SA-->>O: 子 Agent 完成，返回结果
    O-->>WS: Agent 执行完毕，返回 messages

    WS->>WS: 遍历 messages 找最后一条 AI message
    WS->>B: bridge.send_ai_message("当前场景共有 2 个实体...")
    B->>WS: WebSocket 发送 ai_message
    WS->>F: ai_message<br/>{content:"当前场景共有 2 个实体：卫星A、地面站B"}

    WS->>B: bridge.send_end()
    B->>WS: WebSocket 发送 end
    WS->>F: end<br/>{thread_id:"t-001"}

    %% === 阶段 6：清理 ===
    rect rgb(248, 248, 248)
        Note over U,LLM: 阶段 6 — ContextVar 恢复 & 会话保持
    end

    WS->>WS: bridge_var.reset(token)<br/>恢复 ContextVar 默认值
    Note over WS: 等待下一条消息...<br/>（bridge 实例保持，供后续 tool_result 使用）

    F->>U: 显示结果："当前场景共有 2 个实体..."
```

---

## 关键节点详解

### 1. ContextVar 注入机制 (`bridge_var`)

```python
# bridge/__init__.py
bridge_var: ContextVar[WSBridge | None] = ContextVar("bridge_var", default=None)
```

- WebSocket handler 在处理 `user_input` 时调用 `bridge_var.set(bridge)` 注入当前会话的 bridge
- 工具函数通过 `bridge_var.get()` 获取 bridge，无需传参
- 请求处理完毕后 `bridge_var.reset(token)` 恢复默认值

### 2. Future 桥接机制 (`WSBridge`)

```
send_tool_call()                    resolve_tool_result()
     │                                     │
     ├─ UUID → tool_call_id                ├─ 取出 future
     ├─ 创建 Future → _pending[id]         ├─ future.set_result(...)
     ├─ 发送 tool_call 到 WebSocket        └─ 清理 _pending
     └─ await future (阻塞，等待前端返回)
```

- 每次工具调用产生唯一的 `tool_call_id`
- Future 用于跨异步上下文传递结果
- 超时默认 60 秒，超时后返回 `{success: false, message: "工具调用超时"}`

### 3. Agent 实例缓存

```python
_agent_cache: dict[str, thread_id] = {}  # thread_id → compiled graph
```

- 每个 WebSocket 会话复用同一个 Agent 实例
- 首次请求时创建（扫描 Skill + 构建子 Agent），后续请求直接取缓存
- LangGraph 的 checkpointer 负责 conversation history 持久化

### 4. 子 Agent 声明式配置

`config/subagents.yaml` 驱动：

```yaml
agents:
  - name: scene-agent
    description: 处理场景相关操作...
    skills: [scene_management]
    prompt_file: scene_agent.md
```

新增子 Agent 只需：加 YAML 条目 + `prompts/` 下加提示词 + `skills/` 下加 Skill 目录。

---

## WebSocket 消息协议速查

| 方向 | 消息类型 | 触发时机 |
|------|---------|---------|
| 前端 → 后端 | `user_input` | 用户发送消息 |
| 前端 → 后端 | `tool_result` | 前端执行完 Cesium 操作 |
| 后端 → 前端 | `tool_call` | Agent 调用工具 |
| 后端 → 前端 | `ai_message` | Agent 生成文本回复 |
| 后端 → 前端 | `end` | 当前轮次结束 |
| 后端 → 前端 | `error` | 异常发生 |

---

## 项目目录速查

```
src/space_aiagent/
├── api/websocket.py       # WebSocket 核心消息循环（入口）
├── agents/orchestrator.py # 主控 Agent 创建（DeepAgent）
├── agents/subagents.py    # 子 Agent 加载器（YAML → DeepAgent subagents）
├── bridge/ws_bridge.py    # Future 桥接核心（send_tool_call / resolve）
├── bridge/session.py      # 会话管理（thread_id → WebSocket 映射）
├── bridge/__init__.py     # bridge_var (ContextVar) 导出
├── tools/registry.py      # 静态工具注册表（标准 import + 按组分组）
├── prompts/               # 提示词模板目录
├── knowledge/             # 领域知识（AGENTS.md）
├── models/                # 数据模型（枚举、Schema、消息类型）
└── infrastructure/        # 基础设施（配置、日志、数据库）
```
