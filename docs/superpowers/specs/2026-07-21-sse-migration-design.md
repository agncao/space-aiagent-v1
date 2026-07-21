# 传输层迁移设计文档：WebSocket → SSE+POST（事件流）

- **阶段**：传输层迁移（WebSocket → SSE+POST）
- **日期**：2026-07-21
- **范围**：后端通信传输层从 WebSocket 双工改为「SSE（后端→前端事件流）+ HTTP POST（前端→后端离散指令）」；删除 `/ws/space`；定义 `token / tool_start / tool_args / tool_result / tool_end / interrupt / done / error` 事件协议
- **不在范围**：interrupt 的 graph 级实现（`interrupt()` + resume 续跑，列为下一步独立任务）、前端 `sceneAgent` 代码改造（由前端团队/后续任务执行，本文仅交付契约）、CLAUDE.md 架构章节的全面改写（随代码实现完成后做）

---

## 1. 背景与目标

当前所有通信走 WebSocket 双工：前端 `user_input` / `tool_result` → 后端，后端 `ai_message` / `tool_call` / `end` / `error` → 前端。前端要在 UI 上展示 `token / tool_start / tool_args / tool_result / tool_end / interrupt / done` 这类**渐进式事件**（尤其 token 逐字流式），以获得现代 AI streaming 体验。

WebSocket 技术上能逐条发这些事件，但语义不对：它是全双工管道靠 `type` 字段区分消息方向，而「事件流」本质是后端→前端的单向流。继续在 WS 上堆事件类型会得到一个「既全双工又假装是流」的混乱模型。

**目标**：把传输层按职责拆开——后端→前端走 SSE（单向事件流），前端→后端走 HTTP POST（离散指令）——让事件流获得 SSE 的原生语义（标准帧格式、断线重连、HTTP/2 复用），同时一步到位删除 WebSocket，避免长期维护双 transport。

**设计原则**（继承自 CLAUDE.md）：
- 协议优先于实现（先定 SSE 事件协议 + POST body 契约）
- 内核零业务知识（传输层不含航天逻辑）
- 可观测优先（复用 1A-1 的 `optional_span`，trace root 不丢）
- 失败可恢复（客户端断开 / 工具超时 / 并发请求都有明确处理）

---

## 2. 现状（传输层缺口）

基于代码探索（`api/websocket.py`、`bridge/ws_bridge.py`、`bridge/session.py`、`models/messages.py`、`main.py`）与前端探索（`space2024/plugins/sceneAgent`）：

| 维度 | 现状 | 缺口 / 迁移要点 |
|------|------|------|
| 后端→前端通道 | WebSocket `send_json` 单管道，靠 `type` 区分 `ai_message`/`tool_call`/`end`/`error` | 改 SSE 事件流；事件分类需扩展到 token/tool_* |
| token 流式 | ❌ 不存在。handler 只处理 `on_tool_start`（发进度提示）和 `on_chain_end`（render 后整段发）| 新增 `on_chat_model_stream` → `token` 事件 |
| Bridge 与 WS 耦合 | `WSBridge.__init__(self, websocket, thread_id)` 持 `self._ws`，所有 `send_*` 走 `self._ws.send_json()`（`ws_bridge.py:38,78`）| 强耦合，必须解耦成 queue 出口 |
| 工具 Future 机制 | `_pending: dict[tool_call_id, Future]` + `resolve_tool_result` + `asyncio.wait_for` 超时（`ws_bridge.py`）| ✅ 可原样复用，只是 resolve 入口从 WS 消息改 POST handler |
| Session 注册表 | `SessionManager` 内存 dict `thread_id → (WebSocket, WSBridge)`（`session.py`）| ✅ 可复用，register 去 websocket 参数 |
| 前端 POST 能力 | `HttpClient.post()` 已有（`fetch` + POST + JSON），`sceneTools.js` 工具执行与传输解耦 | ✅ POST 零壁垒；但 `post()` 写死 `.res.json()` 不能消费 SSE，需新 `streamPost` |
| 前端 WS 客户端 | `ws.js` 长连接 + 单例 + 自动重连 + 双向 | 长连接/重连套件退役；事件分发（EventTarget）+ Promise 关联（pendingPromises）逻辑保留 |
| 可观测性 trace root | `ws.session` span 当 root（`main.py` excluded_urls 排除 `/ws/space`）| 改名 `agent.session`（传输无关），excluded_urls 换成 SSE 路径 |
| EventSource 可用性 | — | ⚠️ 浏览器原生 `EventSource` 是 GET-only，POST-返回-SSE 必须用 `fetch + getReader()` 手写解析（ERP_OPENCLAW 有现成参考）|

**已有可复用机制**：
- `WSBridge` 的 Future / resolve / cleanup / 超时（`bridge/ws_bridge.py`）
- `SessionManager` 注册表（`bridge/session.py`）
- `bridge_var` ContextVar 注入（`bridge/__init__.py`，工具函数 `bridge_var.get()` 不变）
- `response_util.render()` 出口渲染（`models/response_schema/response_util.py`）
- `optional_span()` + `tracing.set_span_io()` 埋点（1A-1）
- 前端 `HttpClient.post` / `AgentComm` 事件分发 / `_toolHandlerWrapper` 工具执行回告（`space2024/plugins/sceneAgent`）

---

## 3. 设计决策

### 3.1 选定 SSE+POST（方案 B），一步到位删 WS

**已否决**：
- **方案 A（纯 WS + 多发几个事件类型）**：改动最小，但拿不到 SSE 的原生收益（标准帧格式 / `Last-Event-ID` 断线重连 / HTTP/2 复用 / 无 Upgrade 握手 / 对齐现代 AI streaming SDK 心智模型）。WS 能做但语义错位，长期维护一个「全双工管道假装流」的模型
- **方案 C（混合：WS 上传 + SSE 下载）**：直觉上「上传不动、只改下载」省事，实际是尴尬中间地带——一个会话从此背两条连接，需 thread_id 关联 + SSE 生命周期管理 + 订阅竞态（必须先订阅 SSE 再发 user_input）；一次工具往返被劈成 SSE（tool_call）+ WS（tool_result）两条通道；且因还留着 WS，SSE「无 Upgrade 握手 / HTTP/2 复用」的优势直接没了。净效果：保留 WS 全部维护成本 + 叠加 SSE 全部新增复杂度

**选定**：纯 SSE+POST。前端壁垒已验证不大（POST 已有；消费 SSE 是新增 ~100 行解析，有 ERP_OPENCLAW 现成参考；Cesium 工具执行零改动）。

### 3.2 POST /chat 的响应本身是 SSE 流（非独立 GET /stream）

**已否决**：
- **独立 `GET /stream?thread_id=X` 长连接 + `POST /message` 触发**：需 pub/sub fan-out（`thread_id → SSE 订阅队列列表`，分布式要 Redis）、SSE 何时开/关、断一条另一条在等一堆生命周期问题

**选定**：`POST /api/v1/space/chat` 的 HTTP 响应直接是 `text/event-stream`。一轮对话 = 一个 POST（返回 SSE 流）+ N 个 POST /tool-result。流到 `done`/`error` 自然关闭，无需常驻连接、无需 fan-out。与 Vercel AI SDK / ERP_OPENCLAW 同款范式。

### 3.3 token 用真正的 LLM token 流式（非分块假流式）

**已否决**：
- **分块假流式**（render 完整段后切碎当 token 发）：不改 agent 架构、UX 接近真流式，但本质是「算完再表演」，违背用户「真 LLM token」诉求

**选定**：接 `astream_events` 的 `on_chat_model_stream`，每个 chunk 发一个 `token` 事件，`source` 标识来源 agent/model。

**已知 caveat（须如实告知前端）**：orchestrator 用 `ToolStrategy(AgentResponse)` 结构化输出，其「token」是 tool-call JSON 参数碎片（DeepSeek/Qwen 走 tool calling），展示不友好；子 agent 若产自由文本则可读。**前端按 `source` 过滤决定展示哪些**（如只显示子 agent 推理、隐藏 orchestrator JSON 碎片）。实现期须先 spike 确认结构化输出的流式 chunk 实际落在哪个 event（`on_chat_model_stream` 内容块 vs tool-call arg delta），再定最终 hook 点。

### 3.4 interrupt 协议先就位、实现延后

**已否决**：
- **本次完整实现**（graph `interrupt()` + resume 续跑）：属于独立功能，工作量与传输层迁移不在一个量级，混入会拖垮本次交付

**选定**：定义 `interrupt` 事件契约 + `POST /chat/{thread_id}/resume` 端点签名（返 501 NotImplemented），SSE handler 预留 `interrupt` 分支（暂不可达）。真正实现（graph `interrupt()` + resume 续跑 + 前端决策 UI）作为紧随其后的下一步独立任务。

### 3.5 并发护栏：同 thread_id 重入返 409

WS 时代单连接串行处理多轮，天然无并发。SSE+POST 每个 POST 独立，若前端对同一 `thread_id` 并发发两个 POST /chat，会触发 checkpointer 冲突。**护栏**：`SessionManager` 已注册该 `thread_id` 且 agent 在跑时，新 POST /chat 返 **409 Conflict**。前端纪律：一轮一请求（与 WS 时代一致）。

---

## 4. 详细设计

### 4.1 传输模型：3 个端点

| 端点 | 方法 | 方向 | body / 响应 |
|------|------|------|------|
| `/api/v1/space/chat` | POST | 前端→后端触发，后端→前端流 | body=`{content, thread_id, message_id, current_scene_name}`；响应=`text/event-stream` |
| `/api/v1/space/tool-result` | POST | 前端→后端 | body=`{tool_func, args, tool_call_id, thread_id, success, message, data, code}`；响应=`{ok: true}` |
| `/api/v1/space/chat/{thread_id}/resume` | POST | 前端→后端（**协议就位，暂 501**）| body=`{resume: {...}}`；响应=`text/event-stream` |

POST body 模型复用现有 `UserInputMessage` / `ToolResultMessage`（`models/messages.py`），去掉 `type` 字段（POST 端点自身已隐含类型）。

### 4.2 Bridge 解耦：WSBridge → StreamBridge（核心改动）

现状 `WSBridge` 持 `self._ws`，`send_ai_message`/`send_tool_call`/`send_end`/`send_error` 全走 `self._ws.send_json()`。解耦为持 `asyncio.Queue` 的 `StreamBridge`：

```python
class StreamBridge:
    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id
        self._pending: dict[str, asyncio.Future] = {}   # 原样保留
        self._timeout = 60                                # 原样保留
        self._queue: asyncio.Queue = asyncio.Queue()     # 新增：事件出口
        self._closed = False

    async def _emit(self, event: str, data: dict) -> None:
        if self._closed:
            return
        await self._queue.put({"event": event, "data": {**data, "thread_id": self._thread_id}})

    async def send_tool_call(self, namespace, tool_func, args, timeout=60) -> dict:
        tool_call_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = future
        await self._emit("tool_start", {"tool_func": tool_func, "namespace": namespace,
                                        "tool_call_id": tool_call_id})
        await self._emit("tool_args", {"tool_func": tool_func, "tool_call_id": tool_call_id, "args": args})
        result = await asyncio.wait_for(future, timeout=timeout)   # 超时抛 asyncio.TimeoutError（1B 已改）
        await self._emit("tool_result", {"tool_func": tool_func, "tool_call_id": tool_call_id, "result": result})
        await self._emit("tool_end", {"tool_func": tool_func, "tool_call_id": tool_call_id})
        return result

    def resolve_tool_result(self, result: ToolResultMessage) -> None: ...   # 原样
    def cleanup(self) -> None: ...                                           # 原样 + 置 self._closed = True
```

**关键不变量**：
- 工具函数（`entity_management/tools.py`、`scene_management/*.py`）调 `bridge.send_tool_call(...)` **完全不动**
- `bridge_var` ContextVar 注入机制不变（`bridge/__init__.py`），只是实例类型从 `WSBridge` 换 `StreamBridge`
- Future / resolve / 超时 / cleanup 语义原样，只是 resolve 的调用入口从「WS handler 收到 tool_result 消息」变成「POST /tool-result handler」

### 4.3 SSE 事件分类（精确对齐前端期望）

| event | 触发源 | data 字段 | 终态？ |
|-------|--------|-----------|--------|
| `token` | `astream_events` → `on_chat_model_stream` | `{content, source, thread_id}` | 否 |
| `tool_start` | `StreamBridge.send_tool_call` 入口 | `{tool_func, namespace, tool_call_id, thread_id}` | 否 |
| `tool_args` | 同上 | `{tool_func, tool_call_id, args, thread_id}` | 否 |
| `tool_result` | Future resolve 后 | `{tool_func, tool_call_id, result, thread_id}` | 否 |
| `tool_end` | `send_tool_call` 返回前 | `{tool_func, tool_call_id, thread_id}` | 否 |
| `interrupt` | **协议就位，暂不触发** | `{interrupt_id, type, message, thread_id}` | 否 |
| `done` | `on_chain_end` render 后 | `{thread_id, content}`（content=`response_util.render()` 最终回复）| ✅ 是 |
| `error` | 异常 | `{thread_id, message}` | ✅ 是 |

SSE 帧格式（标准 `text/event-stream`，带 `event:` 类型行，每帧以空行结尾）：

```
event: token
data: {"content":"创建","source":"scene-agent","thread_id":"t1"}

event: tool_start
data: {"tool_func":"createScenario","namespace":"scene_tools","tool_call_id":"<uuid>","thread_id":"t1"}

event: done
data: {"thread_id":"t1","content":"已创建场景「测试场景」。"}
```

**进度提示兼容**：现状 `on_tool_start` 发人类可读进度（`_make_progress_message`）。新模型前端可从 `tool_start`/`tool_args` 自行渲染进度；为保留体验，`tool_start.data` 可选带 `display` 字段（复用 `_make_progress_message` 输出），前端可选展示。

**终态语义**：`done` / `error` 是流的终止帧，发送后 SSE generator 关闭、session 注销。`token` / `tool_*` / `interrupt` 是中间帧。

### 4.4 POST /chat（SSE）端点流程

```
POST /api/v1/space/chat {content, thread_id, message_id, current_scene_name}
  │
  ├─ SessionManager.register(thread_id) → 若已存在活跃 session → 409 Conflict
  ├─ 创建 StreamBridge(thread_id)
  ├─ bridge_var.set(bridge) + orchestrator_task_streak_var.set(0)
  ├─ agent_task = asyncio.create_task(run_agent(bridge, user_msg))
  └─ return StreamingResponse(event_generator(), media_type="text/event-stream",
                              headers={Cache-Control: no-cache, X-Accel-Buffering: no, Connection: keep-alive})

event_generator():
  with optional_span("agent.session", agent.thread_id=..., agent.scene_name=...):
    set_span_io(span, input=user_msg.content)
    try:
      while True:
        item = await bridge._queue.get()
        yield format_sse_frame(item["event"], item["data"])
        if item["event"] in ("done", "error"):
          break
    finally:
      agent_task.cancel()             # 防止 agent 还在跑
      bridge.cleanup()                # pending futures 置 ConnectionError
      SessionManager.unregister(thread_id)
```

`run_agent` 内部（迁移自 `api/websocket.py:run_agent` 的 `astream_events` 循环）：
- `on_chat_model_stream` → `bridge._emit("token", {content, source})`（source 从 event metadata 解析）
- `on_tool_start`（非 AgentResponse）→ 可选 `bridge._emit` 一个带 `display` 的中间消息，或省略（由 tool_start 事件替代）
- `on_chain_end` 且 `output.structured_response` 存在 → `rendered = response_util.render(...)` → `bridge._emit("done", {content: rendered})` → return
- 异常 → `bridge._emit("error", {message: str(e)})`

### 4.5 POST /tool-result 端点

```
POST /api/v1/space/tool-result {tool_func, args, tool_call_id, thread_id, success, message, data, code}
  │
  ├─ bridge = SessionManager.get_bridge(thread_id)
  ├─ 无 bridge → 404（session 已结束 / 不存在）
  ├─ bridge.resolve_tool_result(ToolResultMessage(...))   # 原样 resolve Future
  └─ return {ok: true}
```

### 4.6 客户端断开处理（最棘手，重点测试）

SSE 客户端断开（关页 / AbortController 取消）→ Starlette 在 generator 下一次 `yield` 抛 `asyncio.CancelledError`。处理见 4.4 的 `finally`：
- 取消 `agent_task`（正在跑的 agent 图）
- `bridge.cleanup()` 对所有 pending futures 置 `ConnectionError`，防止工具 Future 永挂（被 `asyncio.wait_for` 的工具会立刻收到异常）
- `SessionManager.unregister`

**注意**：`agent_task` 内部 `await bridge._emit(...)` 会在 `queue.put` 上阻塞——cleanup 置 `_closed=True` 后 `_emit` 直接 return，不会再 put；agent_task 被 cancel 后其内部 await 点抛 CancelledError，正常退出。

### 4.7 可观测性

- `main.py` 的 `FastAPIInstrumentor.instrument_app(app, excluded_urls=...)` 把 `/ws/space` 换成 `/api/v1/space/chat`（让手动 span 当 trace root，自动 server span 不抢 root）
- 手动 span 由 `ws.session` 改名 **`agent.session`**（传输无关，未来再变 transport 也不用改名）
- `set_span_io(span, input=user_msg.content, output=rendered)` 保留（root observation IO 自动成 trace 级 IO，Langfuse Traces 列表非空）
- `POST /tool-result` 是短请求，不排除，走 FastAPI 自动 span 即可
- 业务 span 埋点位置（`PrimaryAgentMiddleware` / `SubagentToolValidationMiddleware` / 各工具）**全部不变**——它们通过 `bridge_var` 拿 bridge，与传输无关

### 4.8 interrupt 协议契约（实现延后）

- 事件：`event: interrupt` / `data: {interrupt_id, type, message, thread_id}`
- 恢复端点：`POST /api/v1/space/chat/{thread_id}/resume` body `{resume: {...}}` → 返回 SSE 流（续跑）
- 本次：`/resume` 端点定义签名但返回 **501 NotImplemented**；SSE handler 预留 `interrupt` 事件分支（暂不可达）
- 下一步独立任务：graph `interrupt()` 落点设计 + resume 续跑 + 前端决策 UI

---

## 5. 前端契约（供前端团队对照，不在本次实现范围）

`space2024/plugins/sceneAgent` 需改造，参照 ERP_OPENCLAW `src/api/chat.js`（本地 `/Users/caojianming/projects/mashibing/HarnessEngineeringBased_DeepAgents_Course/ERP_OPENCLAW/frontend/src/api/chat.js`）：

| 前端文件 | 改动 |
|----------|------|
| `js/utils/HttpClient.js` | 新增 `streamPost(url, body, callbacks)`：`fetch(POST) + response.body.getReader()` 逐块解析 SSE 帧（现有 `post()` 写死 `.then(res=>res.json())` 不能复用）。回调 `{onToken, onToolStart, onToolArgs, onToolResult, onToolEnd, onInterrupt, onDone, onError}`，支持 `AbortSignal` 取消 |
| `js/common/aiagent-promise.js:sendMessage` | `wsClient.sendMessage({type:'user_input',...})` → `streamPost('/api/v1/space/chat', {content, thread_id, message_id, current_scene_name}, callbacks)`，事件分发复用现有 `EventTarget` |
| `js/common/aiagent-promise.js:_toolHandlerWrapper` | 回告 `wsClient.sendMessage({type:'tool_result',...})` → `HttpClient.post('/api/v1/space/tool-result', {tool_call_id, ...result, thread_id})`。**Cesium 业务执行（`sceneTools.js`）零改动** |
| `js/utils/ws.js` / `js/common/agent-comm.js` | WS 长连接 / 重连套件退役；`AgentComm` 的事件分发（EventTarget）+ `pendingPromises` 关联机制逻辑保留，数据入口从 WS `onmessage` 换成 SSE 流解析 |

**关键不变量**：工具执行 handler 签名 `_toolHandler(namespace, tool_func, tool_func_args)` 与传输无关，`tool_call_id` / `thread_id` 在 wrapper 层处理，Cesium 调用代码不动。

---

## 6. 验证

- `pytest tests/test_api/test_sse.py tests/test_bridge/test_stream_bridge.py`
- 手测 SSE：`curl -N -X POST localhost:8028/api/v1/space/chat -H 'Content-Type: application/json' -d '{"content":"创建测试场景","thread_id":"t1","current_scene_name":null}'` → 逐行收到 `event: tool_start/tool_args/tool_result/tool_end/done` 帧
- tool-result 回环：流到 `tool_start` 后，另一终端 `curl -X POST localhost:8028/api/v1/space/tool-result -d '{"tool_call_id":"<id>","thread_id":"t1","success":true,...}'`，原流继续走到 `done`
- 断开测试：curl 中途 Ctrl+C → 后端日志见 agent task 取消 + bridge cleanup
- 并发 409：同 thread_id 第二个 POST /chat 返 409
- 可观测性：`observability.enabled=true` 时 Langfuse trace root 为 `agent.session`，input/output 非空

---

## 7. 不在范围 / 后续

- **interrupt 完整实现**（graph `interrupt()` + resume 续跑 + 前端决策 UI）= 紧随其后的下一步独立任务（CLAUDE.md 路线图已占位 🟡 待启动）
- 前端 `sceneAgent` 改造（由前端团队或后续任务执行，本文仅交付契约）
- CLAUDE.md 架构章节（「WebSocket 消息协议」表、「远程工具桥接」段等）的全面改写，随代码实现完成后做
