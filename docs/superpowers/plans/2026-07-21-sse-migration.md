# 传输层迁移 Implementation Plan：WebSocket → SSE+POST

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:exec-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把后端通信传输层从 WebSocket 双工改为「SSE 事件流（后端→前端）+ HTTP POST（前端→后端）」，让前端能展示 `token / tool_start / tool_args / tool_result / tool_end / interrupt / done` 渐进式事件，获得现代 AI streaming 体验；一步到位删除 `/ws/space`，避免长期维护双 transport。

**Architecture:** 新建 `StreamBridge`（解耦自 `WSBridge`，持 `asyncio.Queue` 作事件出口，Future/resolve/cleanup/超时原样保留）；新建 `api/sse.py`（`POST /chat` 返回 `text/event-stream`、`POST /tool-result` resolve Future、`POST /chat/{id}/resume` 暂 501）；agent run loop 接 `on_chat_model_stream` 发 `token`、`on_chain_end` render 后发 `done`；`SessionManager.register` 去 websocket；删 `api/websocket.py` + WS 路由；`main.py` excluded_urls 把 `/ws/space` 换 `/api/v1/space/chat`，trace span 改名 `agent.session`。工具函数、middleware、bridge_var 注入、Future 机制全部不动。interrupt 仅协议占位，graph 级实现是下一步独立任务。

**Tech Stack:** Python 3.13、FastAPI（`StreamingResponse` + `text/event-stream`）、Starlette（async generator + 客户端断开 CancelledError）、asyncio（Queue/Future/create_task/wait_for）、pydantic v2、deepagents/langchain（`astream_events` v2）、opentelemetry（`optional_span`/`set_span_io`）、pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-07-21-sse-migration-design.md`

---

## File Structure

**Create:**
- `src/space_aiagent/api/sse.py` — SSE + POST endpoints（`POST /chat`、`POST /tool-result`、`POST /chat/{thread_id}/resume` 占位）
- `src/space_aiagent/bridge/stream_bridge.py` — `StreamBridge`（解耦自 WSBridge，queue 出口）
- `src/space_aiagent/models/sse_events.py` — SSE 事件 dataclass + 帧序列化（`event:`/`data:` + 空行）
- `tests/test_api/test_sse.py` — SSE 端到端、tool-result 回环、断开取消、并发 409
- `tests/test_bridge/test_stream_bridge.py` — StreamBridge emit 序列、resolve、cleanup、超时
- `tests/test_models/test_sse_events.py` — SSE 帧序列化

**Modify:**
- `src/space_aiagent/bridge/session.py` — `register(thread_id)` 去 websocket 参数，建 `StreamBridge`
- `src/space_aiagent/bridge/__init__.py` — `bridge_var` 类型注解 `WSBridge` → `StreamBridge`，导出更新
- `src/space_aiagent/main.py` — `excluded_urls` 把 `/ws/space` 换 `/api/v1/space/chat`；注册新 SSE router，移除 WS router
- `src/space_aiagent/api/__init__.py`（或 websocket.py 的 router 来源）— 移除 `ws_router`，导出 `sse_router`
- `CLAUDE.md` — 实现完成后改写「WebSocket 消息协议」表为「SSE+POST 协议」、「远程工具桥接」段的 WS 描述

**Delete:**
- `src/space_aiagent/api/websocket.py` — WS endpoint（在 T6 删除，T1-T5 并行开发期保留以便对比/回退）

---

## Task 1: StreamBridge 解耦（WSBridge → StreamBridge）

**Files:**
- Create: `src/space_aiagent/bridge/stream_bridge.py`
- Create: `tests/test_bridge/test_stream_bridge.py`

- [ ] **Step 1: 新建 StreamBridge 类**

在 `stream_bridge.py` 实现：`__init__(thread_id)` 持 `_pending`/`_timeout`/`_queue`/`_closed`；`_emit(event, data)` 往 queue push（`_closed` 时 no-op）；`send_tool_call(namespace, tool_func, args, timeout=60)` 发 `tool_start`+`tool_args` → `await asyncio.wait_for(future, timeout)` → 发 `tool_result`+`tool_end`，超时抛 `asyncio.TimeoutError`（1B 已有的语义）；`resolve_tool_result(result)` 原样；`cleanup()` 原样 + 置 `_closed=True`。参照 spec 4.2 代码。

- [ ] **Step 2: 单测 emit 序列**

`test_send_tool_call_emits_full_lifecycle`：mock Future 立即 resolve，断言 queue 依次收到 `tool_start`→`tool_args`→`tool_result`→`tool_end` 四帧，data 字段含 `tool_call_id`/`args`/`result`，`send_tool_call` 返回值 == resolve 的 result。

- [ ] **Step 3: 单测 resolve / 未知 id**

`test_resolve_tool_result`：put 一个 pending future，resolve 后 future.done() 且 result 正确；`test_resolve_unknown_id`：未注册 id 的 resolve 不抛错（记 warning）。

- [ ] **Step 4: 单测 cleanup + 超时**

`test_cleanup_closes_emit`：cleanup 后 `_emit` 不再入队；`test_timeout_raises`：pending future 不 resolve，`asyncio.wait_for` 到期抛 `asyncio.TimeoutError`，`_pending` 清理对应 id。

- [ ] **Step 5: ruff + pytest 通过**

Run: `ruff check src/space_aiagent/bridge/stream_bridge.py tests/test_bridge/test_stream_bridge.py && pytest tests/test_bridge/test_stream_bridge.py`

---

## Task 2: SSE 事件模型 + 帧序列化

**Files:**
- Create: `src/space_aiagent/models/sse_events.py`
- Create: `tests/test_models/test_sse_events.py`

- [ ] **Step 1: 定义事件类型 + 序列化**

`sse_events.py`：定义 `SSEEventType` 字符串常量（`token`/`tool_start`/`tool_args`/`tool_result`/`tool_end`/`interrupt`/`done`/`error`）；`format_sse_frame(event: str, data: dict) -> str` 产出标准帧：

```
event: <event>\n
data: <json.dumps(data, ensure_ascii=False)>\n
\n
```

注意：`data` 若含换行需按 SSE 规范拆成多行 `data:`（json 单行通常无换行，但 `ensure_ascii=False` 中文 OK）。

- [ ] **Step 2: 单测帧格式**

`test_format_sse_frame`：断言输出含 `event: token\n`、`data: {...}\n\n`；`test_terminators`：`done`/`error` 是终态事件（常量集合 `TERMINAL_EVENTS = {"done", "error"}`）。

- [ ] **Step 3: ruff + pytest 通过**

---

## Task 3: POST /chat（SSE 流）端点

**Files:**
- Create: `src/space_aiagent/api/sse.py`（本 Task 只做 `/chat` + 通用骨架）
- Modify: `src/space_aiagent/bridge/session.py`（register 去 websocket）
- Modify: `src/space_aiagent/bridge/__init__.py`（bridge_var 类型 + 导出）
- Create: `tests/test_api/test_sse.py`（本 Task 先做端到端骨架用例）

- [ ] **Step 1: SessionManager.register 去 websocket**

`session.py`：`register(thread_id) -> StreamBridge`，不再收 websocket，不再存 `_connections`（或保留字段但不用）。`get_bridge(thread_id)` 不变。`unregister(thread_id)` 调 `bridge.cleanup()` + 删映射不变。

- [ ] **Step 2: bridge_var 类型更新**

`bridge/__init__.py`：`bridge_var: ContextVar[StreamBridge | None]`，导出 `StreamBridge`。

- [ ] **Step 3: SSE router + event_generator 骨架**

`sse.py`：`router = APIRouter(prefix="/api/v1/space", tags=["space"])`。`POST /chat` handler：
- 解析 body（复用 `UserInputMessage` 去 `type`，或新建 `ChatRequest` Pydantic 模型 `{content, thread_id, message_id="", current_scene_name=None}`）
- `SessionManager` 已有该 thread_id 活跃 session → `409 Conflict`
- `bridge = SessionManager.register(thread_id)`
- `bridge_var.set(bridge)` + `orchestrator_task_streak_var.set(0)`
- `agent_task = asyncio.create_task(run_agent(bridge, user_msg))`
- `return StreamingResponse(event_generator(bridge, agent_task, user_msg, thread_id), media_type="text/event-stream", headers={Cache-Control: no-cache, X-Accel-Buffering: no, Connection: keep-alive})`

`event_generator`：`with optional_span("agent.session", ...)` + `set_span_io(input=...)` + `try: while True: item=await bridge._queue.get(); yield format_sse_frame(...); if terminal: break` + `finally: agent_task.cancel(); bridge.cleanup(); SessionManager.unregister(thread_id)`。

- [ ] **Step 4: run_agent 迁移 + token 流式骨架**

从 `api/websocket.py:run_agent` 迁移 `astream_events(version="v2")` 循环：
- `on_chat_model_stream` → `bridge._emit("token", {content, source})`（source 从 event metadata 解析，best-effort，解析失败用空串）
- `on_chain_end` 且 `output.structured_response` → `rendered = response_util.render(...)` → `set_span_io(output=rendered)` → `bridge._emit("done", {content: rendered})` → return
- 异常 → `bridge._emit("error", {message: str(e)})`
- 末尾 `bridge_var.reset(token)` + `orchestrator_task_streak_var.reset(token)`

- [ ] **Step 5: 端到端骨架测试**

`test_chat_streams_done`：用 mock agent（或真实 orchestrator 打到 stub LLM），POST /chat，断言收到 `done` 帧且流终止；`test_chat_concurrent_409`：同 thread_id 第二个 POST 返 409。

- [ ] **Step 6: ruff + pytest 通过**

---

## Task 4: POST /tool-result 端点

**Files:**
- Modify: `src/space_aiagent/api/sse.py`（加 `/tool-result`）
- Modify: `tests/test_api/test_sse.py`

- [ ] **Step 1: handler 实现**

`POST /tool-result`：body 复用 `ToolResultMessage` 去 `type`；`bridge = SessionManager.get_bridge(thread_id)`；无 bridge → `404`；`bridge.resolve_tool_result(ToolResultMessage(...))`；return `{ok: True}`。

- [ ] **Step 2: tool-result 回环测试**

`test_tool_result_resolves_future`：mock 一个工具调用（pending future），POST /tool-result 后 future 被 resolve，原 SSE 流的 `tool_result`/`tool_end`/`done` 帧依次收到；`test_tool_result_no_session_404`：未知 thread_id 返 404。

- [ ] **Step 3: ruff + pytest 通过**

---

## Task 5: token 流式 spike + 落地

**Files:**
- Modify: `src/space_aiagent/api/sse.py`（run_agent 的 token hook）
- Modify: `tests/test_api/test_sse.py`

- [ ] **Step 1: spike 结构化输出的流式事件落点**

用真实 orchestrator（打到测试 LLM 或 mock）跑 `astream_events`，打印所有 `on_chat_model_stream` / `on_tool_*` 事件的 `name`/`tags`/`data`，确认：orchestrator 的 `ToolStrategy(AgentResponse)` 流式 chunk 落在哪个 event（`on_chat_model_stream` 内容块 vs tool-call arg delta）、source 如何从 metadata 区分 orchestrator vs 子 agent。记录结论到本 plan 的注记。

- [ ] **Step 2: 据 spike 结论定 hook 点 + source 标注**

实现 `on_chat_model_stream`（或 spike 确定的 event）→ `token` 事件的 source 提取逻辑（langgraph node 名 / tags）。若 orchestrator JSON 碎片确实不可读，文档化「前端按 source 过滤」并保证 source 字段可靠。

- [ ] **Step 3: token 流式测试**

`test_chat_emits_tokens`：mock LLM 产流式 chunk，断言收到 `token` 帧、`source` 字段存在。

- [ ] **Step 4: ruff + pytest 通过**

---

## Task 6: 删除 WebSocket + main.py 路由切换

**Files:**
- Delete: `src/space_aiagent/api/websocket.py`
- Modify: `src/space_aiagent/main.py`、`src/space_aiagent/api/__init__.py`

- [ ] **Step 1: 移除 WS router 注册**

`api/__init__.py`：删 `ws_router` 导出，加 `sse_router` 导出；`main.py`：`include_router(ws_router)` → `include_router(sse_router)`。

- [ ] **Step 2: 删 websocket.py**

`rm src/space_aiagent/api/websocket.py`。确认无残留 import（`run_agent`、`_make_progress_message` 等如被复用已迁到 `sse.py`）。

- [ ] **Step 3: excluded_urls 切换**

`main.py`：`FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/api/v1/space/chat")`（去 `/ws/space`）。

- [ ] **Step 4: 启动验证**

Run: `python -m space_aiagent.main`（应正常启动，无 WS 路由，无 import 错误）；`curl -N -X POST localhost:8028/api/v1/space/chat ...` 手测连通。

---

## Task 7: interrupt 协议占位 + /resume 端点

**Files:**
- Modify: `src/space_aiagent/api/sse.py`

- [ ] **Step 1: /resume 端点返 501**

`POST /chat/{thread_id}/resume`：handler 解析 body `{resume: {...}}`，直接 `raise HTTPException(501, detail="interrupt 实现延后，见下一步任务")`。签名 + 契约就位，实现留给下一步。

- [ ] **Step 2: SSE handler 预留 interrupt 分支**

`event_generator` 或 `run_agent` 预留对 `interrupt` 事件的处理注释位（暂不可达，标注「下一步任务实现 graph interrupt() 后启用」）。

- [ ] **Step 3: 测试 501**

`test_resume_returns_501`。

---

## Task 8: 全量测试 + 断开/并发/超时

**Files:**
- Modify: `tests/test_api/test_sse.py`、`tests/test_bridge/test_stream_bridge.py`

- [ ] **Step 1: 客户端断开测试**

`test_client_disconnect_cancels_agent`：用 `httpx.AsyncClient` POST /chat 后立即断开，断言后端 agent_task 被 cancel、bridge cleanup、SessionManager 注销（session 不残留）。

- [ ] **Step 2: 工具超时测试**

`test_tool_timeout_emits_error_or_raises`：tool_result 永不回告，`send_tool_call` 到期抛 `asyncio.TimeoutError`，流以 `error` 终止（或按 1B RetryMiddleware 逻辑重试耗尽后转 ToolMessage，视实际链路而定，记录实际行为）。

- [ ] **Step 3: 全量回归**

Run: `pytest`（全量）+ `ruff check src/ tests/`。Expected: 全绿。

---

## Task 9: CLAUDE.md 架构章节改写

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 路线图状态更新**

阶段状态表：传输层迁移行状态由 🔵 进行中 → ✅ 已完成（2026-07-XX）；interrupt 行保持 🟡 待启动（下一步）。

- [ ] **Step 2: 「WebSocket 消息协议」表改写**

把「WebSocket 消息协议」表改为「SSE+POST 协议」：列 3 个端点（POST /chat、POST /tool-result、POST /chat/{id}/resume）+ SSE 8 个事件（token/tool_start/tool_args/tool_result/tool_end/interrupt/done/error）及 data 字段。

- [ ] **Step 3: 「远程工具桥接」段更新**

bridge 描述从 `WSBridge(websocket)` 改 `StreamBridge(queue)`；`bridge_var` / Future / resolve 机制不变的部分保留；图示「Agent → bridge._emit → SSE queue → StreamingResponse → 前端」替换原 WS 图示。

- [ ] **Step 4: 「任务循环防护」「意图追踪」等段引用核对**

确认 `PrimaryAgentMiddleware` / `orchestrator_task_streak_var` / `bridge_var.set` 的描述与新 `sse.py` 一致（这些逻辑迁移自 websocket.py，语义不变，只需更新文件路径引用）。

---

## Verification（端到端）

- [ ] `pytest`（全量绿）+ `ruff check src/ tests/`
- [ ] SSE 手测：`curl -N -X POST localhost:8028/api/v1/space/chat -H 'Content-Type: application/json' -d '{"content":"创建测试场景","thread_id":"t1","current_scene_name":null}'` → 逐行收到 `event: tool_start/tool_args/tool_result/tool_end/done`
- [ ] tool-result 回环：上一步流到 `tool_start` 后，另一终端 `curl -X POST localhost:8028/api/v1/space/tool-result -H 'Content-Type: application/json' -d '{"tool_call_id":"<id>","thread_id":"t1","success":true,"message":"ok","data":{}}'`，原流继续走到 `done`
- [ ] 断开测试：curl 中途 Ctrl+C → 后端日志见 agent task 取消 + bridge cleanup，无残留 session
- [ ] 并发测试：同 thread_id 第二个 POST /chat 返 409
- [ ] /resume 返 501
- [ ] 可观测性：`observability.enabled=true` 时 Langfuse trace root 为 `agent.session`，input/output 非空

## 不在范围 / 后续

- **interrupt 完整实现**（graph `interrupt()` + resume 续跑 + 前端决策 UI）= 下一步独立任务
- 前端 `sceneAgent` 改造（契约见 spec 第 5 节，由前端团队/后续任务执行）
