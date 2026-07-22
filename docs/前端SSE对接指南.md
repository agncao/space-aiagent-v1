# 前端 SSE 对接指南

> 面向前端开发者。后端通信传输层已从 WebSocket 迁移到 **SSE（后端→前端事件流）+ HTTP POST（前端→后端离散指令）**。本文给出对接所需的**接口契约**、变更背景、UX 目标与联调方法，供前端无缝对接。
>
> 本文只给契约与参考指针，**不含前端具体实现代码**（前端实现由前端团队负责）。

---

## 1. 变更背景

### 1.1 为什么要迁移

原架构所有通信走 **WebSocket 双工**：前端 `user_input` / `tool_result` → 后端，后端 `ai_message` / `tool_call` / `end` / `error` → 前端，靠消息体的 `type` 字段区分方向与种类。

问题：前端要展示 **渐进式事件**（`token` 逐字流式、`tool_start`/`tool_args`/`tool_result`/`tool_end` 工具执行过程、`interrupt` 人工接管、`done` 结束），而「事件流」本质是后端→前端的**单向流**。把它塞进全双工 WS 管道靠 `type` 字段区分，语义错位——能跑，但模型拧巴，长期维护一个「全双工管道假装是流」的体系不划算。

### 1.2 借鉴了什么

- **ERP_OPENCLAW**（`HarnessEngineeringBased_DeepAgents_Course` 课程的核心工程）—— 其 **SSE+POST 范式**（`POST` 触发、响应本身是 `text/event-stream` 流）与 **事件分类**（`onToken / onToolStart / onToolArgs / onToolResult / onToolEnd / onInterrupt / onDone / onError`）是本次直接对标的参考。**借鉴的是范式与事件分类，不是代码**（技术栈、业务场景不同）。
- **Vercel AI SDK / OpenAI 官方流式** 的通用范式（POST 触发 + 流式响应、`fetch + ReadableStream` 消费）。

> 参考实现可看本地 ERP_OPENCLAW 前端：`/Users/caojianming/projects/mashibing/HarnessEngineeringBased_DeepAgents_Course/ERP_OPENCLAW/frontend/src/api/chat.js`（`streamChat` + `_processStream` 是 SSE 消费的现成范式）。

### 1.3 解决了什么

- 事件流获得 SSE 原生语义：标准帧格式、`Last-Event-ID` 断线重连、HTTP/2 多路复用、无协议升级握手。
- 上行（用户输入、工具回告）与下行（事件流）按职责拆开：**SSE 管流，POST 管离散指令**，心智模型清晰。
- 一步到位删除 WebSocket，避免长期维护双 transport。

---

## 2. 前端 UX 目标（建议以此设计 UI）

迁移是为了支撑以下体验，建议前端**围绕这 8 种事件类型设计展示组件**，而非照搬旧的 `ai_message` 单文本框：

1. **token 逐字流式**（头号目标）：agent 的自由文本回复逐字流出，用户看着字一个个出现，体感延迟骤降。
2. **工具执行可视化**：`tool_start`/`tool_args`/`tool_result`/`tool_end` 让用户看见 agent 在调用什么工具、传什么参数、得到什么结果——透明度与信任。
3. **可中断（未来）**：`interrupt` 事件协议已就位（当前后端返回 501 占位），前端可预留人工接管（HITL）决策 UI。

---

## 3. 传输模型总览

```
前端 ──HTTP POST──> 后端        POST /chat（触发，返回 SSE 流）
                      /tool-result（工具执行结果回告）
                      /chat/{thread_id}/resume（中断恢复，暂 501）

前端 <──SSE───────── 后端        token / tool_start / tool_args / tool_result / tool_end
                                        / interrupt / done / error
```

- **一轮对话 = 一个 `POST /chat`（响应是 SSE 流）+ N 个 `POST /tool-result`**（每次工具执行回告一次）。
- **`POST /chat` 的 HTTP 响应体本身就是 `text/event-stream`**——前端发完 POST 后，在同一个连接上读流即可，无需另开 GET 长连接。
- ⚠️ **浏览器原生 `EventSource` 只支持 GET，用不了**。前端必须用 `fetch + response.body.getReader()` 手写 SSE 解析（ERP_OPENCLAW `_processStream` 是现成参考）。

---

## 4. 接口契约

### 4.1 `POST /api/v1/space/chat`（触发对话，返回 SSE 流）

**请求体**（JSON）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `content` | string | 是 | 用户输入文本 |
| `thread_id` | string | 是 | 会话 ID（后端按此持久化多轮上下文，前端自行生成/复用） |
| `message_id` | string | 否 | 消息唯一 ID（前端生成，默认 `""`） |
| `current_scene_name` | string \| null | 否 | 当前已打开的场景名（来自 Cesium 当前场景；后端注入 agent 状态） |

**响应**：`Content-Type: text/event-stream`，逐帧推送 SSE 事件，直到 `done` 或 `error`（终态帧）后关闭流。

**并发护栏**：同一 `thread_id` 已有活跃会话（agent 在跑）时，返回 **`409 Conflict`**。前端纪律：一轮一请求，等流结束（`done`/`error`）再发下一轮。

**响应头**：`Cache-Control: no-cache`、`X-Accel-Buffering: no`（Nginx 透传禁缓冲）、`Connection: keep-alive`。

### 4.2 `POST /api/v1/space/tool-result`（工具执行结果回告）

前端收到 `tool_start`/`tool_args` 事件后在 Cesium 执行操作，执行完通过此端点回告结果，后端按 `tool_call_id` resolve 对应的工具 Future，agent 才会继续。

**请求体**（JSON）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool_func` | string | 是 | 工具函数名（与 `tool_start` 帧一致） |
| `tool_call_id` | string | 是 | 工具调用 ID（与 `tool_start` 帧一致，关联用） |
| `thread_id` | string | 是 | 会话 ID（定位活跃会话） |
| `args` | object | 否 | 原始工具参数（默认 `{}`） |
| `success` | bool | 否 | 是否成功（默认 `true`） |
| `message` | string | 否 | 结果消息（默认 `""`） |
| `data` | object \| array \| null | 否 | 返回数据（默认 `null`） |
| `code` | string | 否 | 消息码（默认 `""`） |

**响应**：`200 { "ok": true }`。无活跃会话时返回 **`404`**（会话已结束 / 未先 `POST /chat` / `thread_id` 错误）。

### 4.3 `POST /api/v1/space/chat/{thread_id}/resume`（中断恢复，暂未实现）

**请求体**：`{ "resume": { ... } }`（resume 数据格式取决于中断类型）。

**响应**：当前返回 **`501 NotImplemented`**（`interrupt` 的 graph 级实现是下一步任务）。协议先就位，前端可先按此契约预留对接；实现落地后响应将变为 SSE 流（续跑事件）。

### 4.4 SSE 事件契约（后端→前端）

帧格式（标准 `text/event-stream`，每帧以空行结尾）：

```
event: <事件类型>
data: <JSON 对象>

```

8 种事件类型：

| event | data 字段 | 终态？ | 说明 |
|-------|-----------|--------|------|
| `token` | `{content, source, thread_id}` | 否 | LLM 自由文本 token（逐字）。**只含可读自由文本**——结构化输出的 JSON 参数碎片已被后端过滤，前端无需再过滤。`source` 目前统一为 `"model"`/`"agent"`（**无法区分 orchestrator/子 agent**，前端不要依赖 source 做来源过滤） |
| `tool_start` | `{tool_func, namespace, tool_call_id, thread_id}` | 否 | 工具开始。`namespace` 为工具组（当前取值 `scene_tools` / `entity_tools`） |
| `tool_args` | `{tool_func, tool_call_id, args, thread_id}` | 否 | 工具参数（紧跟 `tool_start`） |
| `tool_result` | `{tool_func, tool_call_id, result, thread_id}` | 否 | 工具执行结果（前端回告的数据回显，供事件时间线展示） |
| `tool_end` | `{tool_func, tool_call_id, thread_id}` | 否 | 工具结束（紧跟 `tool_result`） |
| `interrupt` | `{interrupt_id, type, message, thread_id}` | 否 | **协议占位，当前后端不触发**（graph `interrupt()` 未实现） |
| `done` | `{thread_id, content}` | ✅ 是 | 本轮结束。`content` 是最终回复（后端 `render` 后的自然语言） |
| `error` | `{thread_id, message}` | ✅ 是 | 出错。流终止 |

**关键语义**：
- `done` / `error` 是**终态帧**，收到后流关闭，本轮结束。`content`（done）是权威的最终回复文本。
- 一次工具调用的完整生命周期：`tool_start` → `tool_args` →（前端执行 + `POST /tool-result`）→ `tool_result` → `tool_end`。
- 帧里 `data` 的 JSON 含中文时**不转义**（`ensure_ascii=False`），直接是 UTF-8 可读文本。

---

## 5. 后端已提供的技术支撑

前端对接时可依赖的后端能力（无需前端实现）：

| 能力 | 说明 |
|------|------|
| **真 LLM token 流式** | 已用真实模型 spike 验证：自由文本逐字流出；结构化输出的 JSON 参数碎片在后端自动过滤，前端拿到的 token 都是可读文本 |
| **工具执行桥接** | 前端在 Cesium 执行工具 → `POST /tool-result` 回告 → 后端 resolve Future → agent 自动续跑。前端只管「收指令、执行、回告」 |
| **多轮上下文持久化** | 按 `thread_id` 持久化（SQLite checkpointer），跨轮对话记忆不丢、不受进程重启影响 |
| **失败恢复（重试 + 降级）** | LLM 临时失败（429/超时/5xx）、工具超时自动重试；耗尽或不可恢复时给结构化降级响应，不中断会话 |
| **并发护栏** | 同 `thread_id` 重入返回 `409`，避免 checkpointer 冲突 |
| **客户端断开自动清理** | 前端关页 / `AbortController` 取消 → 后端自动取消正在跑的 agent + 清理资源，不留僵尸会话 |
| **场景上下文同步** | `current_scene_name` 跨 agent 自动同步（创建实体前必须先有场景的依赖由后端校验） |
| **可观测性** | 全链路 trace（OTel + Langfuse），便于联调时定位问题（前端不直接用，但出问题时可让后端查 trace） |

---

## 6. 前端对接要点（契约视角）

> 本文不提供前端实现代码。以下是改造方向与约束，供前端团队评估工作量；具体实现参照 ERP_OPENCLAW。

- **消费 SSE 流**：现有 `HttpClient.post` 写死了 `.then(res => res.json())`（假设响应是单个 JSON），**不能直接复用**来消费 SSE 流。需新增一个流式消费方法（`fetch(POST) + response.body.getReader()` 逐块解析 SSE 帧）。`EventSource` 因 GET-only 不可用。
- **WebSocket 客户端退役**：现有 WS 长连接 / 重连机制不再需要；但事件分发（如 `EventTarget`）与工具结果的 Promise 关联机制逻辑可保留，只是数据入口从 WS `onmessage` 换成 SSE 流解析。
- **用户输入提交**：从 WS 发消息改为 `POST /chat`，响应流单独消费。
- **工具结果回告**：从 WS 发消息改为 `POST /tool-result`（携带 `tool_call_id`）。
- **Cesium 工具执行业务零改动**：工具执行函数（如 `sceneTools.js` 里的 Cesium 调用）与传输无关，不需要改——只需把「执行完结果发回去」那一行从 WS 换成 POST。
- **`thread_id` 由前端管理**：前端生成并跨轮复用（与 WS 时代一致）。

---

## 7. 联调方法

后端默认监听 `0.0.0.0:8028`（`config/application.yaml`）。

### 7.1 手测 SSE 流

```bash
curl -N -X POST http://localhost:8028/api/v1/space/chat \
  -H 'Content-Type: application/json' \
  -d '{"content":"创建一个测试场景","thread_id":"t1","current_scene_name":null}'
```

应逐行收到 `event: tool_start` / `tool_args` /（等回告）/ `tool_result` / `tool_end` / `done` 帧。

### 7.2 工具结果回环（两个终端）

终端 1 跑上面的 `curl`，流到 `tool_start` 后拿到 `tool_call_id`；终端 2 回告：

```bash
curl -X POST http://localhost:8028/api/v1/space/tool-result \
  -H 'Content-Type: application/json' \
  -d '{"tool_func":"createScenario","tool_call_id":"<上面拿到的 id>","thread_id":"t1","success":true,"message":"ok","data":{"sceneName":"测试场景"},"code":"SCENE_CREATED"}'
```

回告后，终端 1 的流应继续走到 `tool_result` → `tool_end` → `done`。

### 7.3 其他场景

- **并发**：同 `thread_id` 第二个 `POST /chat` → `409`。
- **断开**：`curl` 中途 `Ctrl+C` → 后端日志应见 agent 取消 + 清理。
- **/resume**：`POST /api/v1/space/chat/t1/resume -d '{"resume":{}}'` → `501`。

---

## 8. 参考资源

- **后端设计文档**：`docs/superpowers/specs/2026-07-21-sse-migration-design.md`（完整设计决策、事件分类、StreamBridge 机制）
- **后端实现计划**：`docs/superpowers/plans/2026-07-21-sse-migration.md`
- **后端 SSE 端点代码**：`src/space_aiagent/api/sse.py`（`POST /chat` / `/tool-result` / `/resume`、`event_generator`、`run_agent`）
- **SSE 事件定义**：`src/space_aiagent/models/sse_events.py`（`SSEEventType` + `format_sse_frame`）
- **参考前端实现**：`/Users/caojianming/projects/mashibing/HarnessEngineeringBased_DeepAgents_Course/ERP_OPENCLAW/frontend/src/api/chat.js`（`streamChat` / `resumeChat` / `_processStream`，SSE 消费的现成范式）

---

## 附：事件序列示例（一次带工具调用的对话）

```
event: token
data: {"content":"我将","source":"model","thread_id":"t1"}

event: token
data: {"content":"为您创建场景。","source":"model","thread_id":"t1"}

event: tool_start
data: {"tool_func":"createScenario","namespace":"scene_tools","tool_call_id":"tcid-1","thread_id":"t1"}

event: tool_args
data: {"tool_func":"createScenario","tool_call_id":"tcid-1","args":{"sceneName":"测试场景"},"thread_id":"t1"}

（前端在 Cesium 执行创建场景，POST /tool-result 回告）

event: tool_result
data: {"tool_func":"createScenario","tool_call_id":"tcid-1","result":{"success":true,"data":{"sceneName":"测试场景"}},"thread_id":"t1"}

event: tool_end
data: {"tool_func":"createScenario","tool_call_id":"tcid-1","thread_id":"t1"}

event: done
data: {"thread_id":"t1","content":"已创建场景「测试场景」。"}
```
