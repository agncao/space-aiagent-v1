# SP1 — Human-in-the-Loop 传输层 设计

- 日期：2026-07-29
- 状态：设计稿（待 review）
- 范围：把 DeepAgents/LangGraph 的 `interrupt()` 接入现有 SSE+POST 传输层，使子 Agent 能在执行中暂停、等用户决策、再续跑。
- 依赖：无（地基）。SP3（open_scenario skill）依赖本 spec。
- 参考实现：`~/projects/mashibing/HarnessEngineeringBased_DeepAgents_Course/ERP_OPENCLAW`（同一套 deepagents + 同构 SSE+POST，已跑通 HITL）

## 1. 背景与现状

当前 `api/sse.py:run_agent` 用 `agent.astream_events(version="v2")` 驱动执行：

- `on_chat_model_stream` → 抽 token（`_extract_chunk_text`）→ emit `token`
- `on_tool_start` → 跳过 `AgentResponse`（结构化输出占位工具），其余工具的 `tool_start/tool_args` 由 `StreamBridge.send_tool_call` 负责
- `on_chain_end` → 读 `output.structured_response` → `response_util.render()` → emit `done` → `return`

`/chat/{thread_id}/resume` 端点已存在但返 501（`sse.py:390-403`）。`interrupt` SSE 事件类型已定义但无 emit 源（`sse_events.py:35`，非终态）。

**问题**：`astream_events` 检测不到 LangGraph `interrupt()`。interrupt 触发时图暂停、`__interrupt__` 写入 state，但 `astream_events` 不把它作为事件吐出。要支持 HITL，必须切换执行原语。

## 2. 目标 / 非目标

**目标**
1. 子 Agent（含 orchestrator）内任何工具/节点调 `interrupt()` 或声明式 `interrupt_on` 都能被传输层捕获。
2. 捕获后向前端发结构化 `interrupt` 事件 + `done(interrupted=true)` 收尾，流正常关闭。
3. `POST /chat/{thread_id}/resume` 用 `Command(resume=...)` 续跑，续跑事件走新 SSE 流。
4. 保持现有 token / tool_* / done / error 行为不变（前端无感升级，仅新增 interrupt 帧）。

**非目标**
- 不实现任何业务级 HITL 用例（open_scenario 的两个中断点是 SP3）。
- 不引入沙箱 / 代码执行（Phase 3）。
- 不改 checkpointer（`AsyncSqliteSaver` 已满足 interrupt 前置条件）。
- 不动 orchestrator/子 Agent 的 middleware 链与 `interrupt_on` 配置（那是各业务 spec 的事；本 spec 只保证传输层能接住）。

## 3. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| **D1 resume 传输** | 新开 SSE 流 | 与 ERP_OPENCLAW 一致（`chat.py:255-369`：resume 分支 `Command(resume=resume_data)`，interrupt 后 `done(interrupted=True)` + `return`，`/resume` 返回新 `StreamingResponse`）。现有 `StreamBridge` 为工具结果 Future 设计，硬塞 graph `Command(resume)` 需改 bridge 语义，得不偿失。checkpointer 按 `thread_id` 桥接两条独立流。 |
| **D2 执行原语** | 全切 `astream(stream_mode=["messages","values"], subgraphs=True, version="v2")` | `subgraphs=True` 是子 Agent interrupt 上浮到顶层流的硬要求（ERP `chat.py:293`）。保留 `astream_events` + 轮询 `get_state` 会形成双代码路径、hacky。 |

## 4. 设计

### 4.1 执行原语切换（`run_agent` 重构）

把 `astream_events` 循环换成 `astream`，chunk 按 `stream_mode` 分派（参考 ERP `chat.py:288-377`）：

```python
async for chunk in agent.astream(
    input=current_input,            # chat: {"messages":[HumanMessage], ...state}; resume: Command(resume=...)
    config={"configurable":{"thread_id":thread_id}, "recursion_limit":100},
    stream_mode=["messages", "values"],
    subgraphs=True,                 # 子 Agent interrupt 上浮必需
    version="v2",
):
    chunk_type = chunk.get("type")
    # values 模式：先判中断（必须在 messages 之前）
    if chunk_type == "values" and chunk.get("interrupts"):
        await _handle_interrupts(bridge, chunk["interrupts"], thread_id)
        return                       # emit interrupt + done(interrupted) 后结束本流
    # messages 模式：token
    if chunk_type == "messages":
        token_msg, metadata = chunk["data"]
        text = _extract_chunk_text(token_msg)
        if text:
            source = (metadata or {}).get("langgraph_node") or "agent"
            await bridge._emit(SSEEventType.TOKEN, {"content": text, "source": source})
```

**结构化输出 / done 检测**：现状靠 `on_chain_end` 读 `structured_response`。切到 `astream` 后，`response_format=ToolStrategy(AgentResponse)` 仍把最终 `AgentResponse` 写进 state 的 `structured_response`。两种取法（实现时二选一，推荐前者以保持现状"尽早 done"语义）：

- **A（推荐）**：在 values 流里检测 `structured_response` 出现 → `render()` → emit `done` → `return`（与现状 `on_chain_end` 早返回对齐）。
- **B（兜底）**：`async for` 正常结束（未中断）后 `await agent.aget_state(config)` 取 `values["structured_response"]` → render → done。

> 实现注意：values chunk 的 `data` 是该 super-step 的 state 增量/快照；`structured_response` 由 ToolStrategy 在模型调用后写入。需在实现期 spike 确认 values chunk 里 `structured_response` 的可见时机，据此选 A/B。

### 4.2 中断检测与发射（`_handle_interrupts`）

interrupt 列表里每个 `Interrupt` 对象的 `.value` 就是传给 `interrupt()` 的 dict。按 payload 形状判别 `interrupt_type`（与 ERP `chat.py:308-341` 对齐）：

```python
async def _handle_interrupts(bridge, interrupts, thread_id):
    for intr in interrupts:
        v = intr.value
        if "action_requests" in v:
            # 声明式 interrupt_on（HumanInTheLoopMiddleware）
            await bridge._emit(SSEEventType.INTERRUPT, {
                "interrupt_type": "hitl_approval",
                "action_requests": v["action_requests"],
                "review_configs": v.get("review_configs", []),
            })
        elif v.get("type"):           # 编程式 interrupt()，业务自定义 type
            await bridge._emit(SSEEventType.INTERRUPT, {
                "interrupt_type": v["type"],     # e.g. "scene_select" / "save_confirm"
                "payload": v,
            })
        else:
            await bridge._emit(SSEEventType.INTERRUPT, {
                "interrupt_type": "unknown", "interrupt_value": str(v)[:2000],
            })
    await bridge._emit(SSEEventType.DONE, {"content": "", "interrupted": True})
```

emit 顺序：先所有 `interrupt` 帧（可能多个，批量），再一个 `done`（`interrupted=true`）。`done` 是终态，`event_generator` 收到后关流、cleanup、unregister。

### 4.3 `/resume` 端点（替换 501）

`/resume` 返回**新 StreamingResponse**，与 `/chat` 共用一个抽出来的 `stream_agent(...)` 异步生成器：

```python
async def stream_agent(thread_id, input_, current_scene_name):
    # 注册新 bridge + session；注入 bridge_var / streak_var；create_task(run_agent)
    # 消费 bridge._queue → format_sse_frame → yield；终态 break；finally cleanup
    ...  # 基本是把现 event_generator + run_agent 参数化：input_ 决定是 chat 还是 resume

@router.post("/chat/{thread_id}/resume")
async def resume(thread_id: str, req: ResumeRequest):
    # 并发护栏、session 检查同 /chat
    bridge = session_manager.register(thread_id)
    return StreamingResponse(
        stream_agent(thread_id, Command(resume=req.resume), current_scene_name=None),
        media_type="text/event-stream", headers=...,
    )
```

resume 的 `input_` 是 `Command(resume=req.resume)`，LangGraph 把该 dict 作为 `interrupt()` 的返回值送达暂停点，图继续。`current_scene_name` 续跑时从 checkpointer state 恢复（不再从请求体注入）。

**复用 agent 缓存**：`_get_or_create_agent(thread_id)` 已按 thread 缓存 agent，resume 命中同一实例 + 同一 checkpointer thread → 续跑成立。

**resume 不需要 `current_scene_name` 入参**：`SpaceAgentState.current_scene_name` 已持久化在 checkpoint，resume 时 state 自动恢复（不同于首轮 `/chat` 需注入初值）。

### 4.4 SSE schema 增量（`sse_events.py` + 前端对接指南）

- `interrupt` 事件 data 增字段：`interrupt_type`（`hitl_approval` / 业务自定义 type / `unknown`）+ 对应 payload（`action_requests`/`review_configs` 或业务 payload）。`thread_id` 由 `_emit` 自动注入（已就绪）。
- `done` 事件 data 增字段：`interrupted: bool`（默认 `false`，interrupt 收尾时 `true`）。
- `TERMINAL_EVENTS` 不变（仍是 `done/error`）；`interrupt` 仍非终态。
- 帧格式不变（`event: <type>\ndata: <json>\n\n`）。

更新 `docs/前端SSE对接指南.md`：新增 `interrupt` 帧结构 + resume 流程（收到 `interrupt` → 渲染决策 UI → `POST /chat/{thread_id}/resume` body `{"resume": {...}}` → 消费新 SSE 流）。

### 4.5 并发与清理

- `/chat` 与 `/resume` 都走 `session_manager` 并发护栏（同 thread 活跃 session → 409）。interrupt 后 `/chat` 流已 `done`+cleanup+unregister，故 `/resume` 可注册新 session。
- `event_generator` 的 finally cleanup 逻辑（cancel task、bridge.cleanup、unregister、reset ContextVar）对 resume 流同样适用——抽进 `stream_agent` 后两边复用。

## 5. 受影响文件

| 文件 | 改动 |
|---|---|
| `src/space_aiagent/api/sse.py` | `run_agent` 切 `astream`；抽 `stream_agent` 公共生成器；`/resume` 实现（替换 501）；`_handle_interrupts` 新增 |
| `src/space_aiagent/models/sse_events.py` | 无代码改动（类型已齐）；注释更新（interrupt 已有 emit 源） |
| `docs/前端SSE对接指南.md` | 新增 interrupt 帧 + resume 流程章节 |
| 测试 | 新增 interrupt/resume 端到端测试（见 §6） |

## 6. 测试策略

- **单元**：`_handle_interrupts` 对三种 `interrupt_type` 的 emit 顺序（interrupt* → done(interrupted=true)）。
- **集成（fake agent）**：monkeypatch `_get_or_create_agent` 注入一个带 `interrupt()` 的假 agent，断言：首轮 SSE 流出 `interrupt` + `done(interrupted=true)` 并关流；`/resume` 开新流、`Command(resume=...)` 送达、后续 token/done 正常。
- **回归**：无 interrupt 的正常对话路径行为不变（token 流、structured_response→done）。
- **参考**：ERP `src/test/agent_test.py:72-211` 的 `while True:` resume 循环驱动器可作 CLI 集成测试模板。

## 7. 风险与开放问题

- **`structured_response` 取时机**：values 流里 `structured_response` 的可见性需 spike（§4.1 A/B）。这是切 `astream` 后唯一行为不确定点。
- **`subgraphs=True` 的 token 噪声**：开启子图流后，子 Agent 的 token 也会上浮（现状 `astream_events` 已如此，`source` 字段无法区分 agent——已知限制，本次不解决）。
- **多 interrupt 批量**：一次暂停可能含多个 interrupt（多工具审批），前端需按顺序对应。`_handle_interrupts` 已逐个 emit。
- **resume 的 `recursion_limit`**：续跑沿用 100，若中断链很长需评估。

## 8. 非范围 / 后续

- 声明式 `interrupt_on` 的具体配置（哪个子 Agent 哪个工具要审批）→ 各业务 spec（如 SP3 open_scenario 的保存确认）。
- 前端决策 UI → 前端任务。
- interrupt 审计 trace 埋点（`optional_span`）→ 实现时顺手加，`agent.session` span 的 IO 应反映中断。
