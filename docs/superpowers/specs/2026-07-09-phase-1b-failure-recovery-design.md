# Phase 1B 失败恢复设计文档

- **阶段**：Phase 1B（失败恢复）
- **日期**：2026-07-09
- **范围**：LLM 调用重试+降级、远程工具调用重试+降级
- **不在范围**：部分成功的状态回滚、WebSocket 重连机制

---

## 1. 背景与目标

Phase 1A-1（OTel + Langfuse AI 维度可观测性）已完成，为故障定位提供了 trace 支撑。Phase 1B 在此基础上补齐**失败恢复**能力，作为后续 Phase 2 Skill 系统的稳定性托底。

**目标**：工具调用与 LLM 调用遇到**可恢复的临时失败**时自动重试，重试耗尽或遇到**不可恢复失败**时给用户结构化降级响应，而非会话中断。

**设计原则**（继承自 CLAUDE.md）：
- 协议优先于实现
- 可观测优先（复用 1A-1 的 `optional_span`）
- 失败可恢复（本次核心）
- 可观测性对业务零依赖（retry 与 observability 独立，retry 失败走降级不阻塞业务）

---

## 2. 现状（失败处理缺口）

基于代码探索（`bridge/ws_bridge.py`、`middleware/`、`api/websocket.py`、`infrastructure/llm.py`）：

| 失败点 | 现状 | 缺口 |
|--------|------|------|
| LLM 调用（429/超时/5xx）| `build_model` 无 `max_retries`/`timeout`，异常直接冒泡 | ❌ 零重试，一崩到底 |
| LLM 返回非法 JSON（ToolStrategy 解析失败）| `ValidationError` 抛出无处理 | ❌ 无降级 |
| 工具 bridge 超时 | `bridge.send_tool_call` 内部 `except TimeoutError` 返回 `{success:false}` dict | ⚠️ 超时被吞成业务失败语义，无法区分 |
| 工具抛异常 | middleware 记录后抛出 → websocket 发 error | ❌ 会话中断 |
| 前端 `success=false` | 工具拿到 dict 自行处理 | ✅ 业务失败，无需重试 |
| 状态回滚 | checkpointer 仅持久化，零 rollback 代码 | ⏸ 本次不做 |

**已有可复用机制**：
- `ToolValidationMiddleware` 的 NO_SCENE 短路（`Command(goto=END)` + `ToolMessage`）
- `PrimaryAgentMiddleware` 的 task loop guard（改写 `ModelResponse` 为短路响应）
- `SHORTCUT_RESPONSES` 注册表 + `response_util.render()` 降级出口
- `message_util.build_primary_agent_response()` 构造降级 `ModelResponse`
- `optional_span()` + `tracing.set_span_io()` 埋点

---

## 3. 设计决策

### 3.1 范围界定：重试 + 降级，不含状态回滚

状态回滚延后。航天场景下「撤销已成功的操作」未必符合用户预期——用户大概率想「保留已成功部分 + 重试失败部分」。真要做时大概率重新定义成保留+重试语义，而非整体撤销，复杂度高，单独设计。

### 3.2 重试哲学：白名单（只重试明确可恢复的临时失败）

**已否决**：
- 乐观重试（失败就重试）—— 对确定性业务失败（实体已存在）浪费往返
- `tool_result` 加 `retriable` 字段让前端标注 —— YAGNI，过度设计；前端 `success=false` 一般是参数缺失等业务错误，无重试必要；需重试的个别场景作为业务需求扩展，不进基础架构

### 3.3 架构：新建统一 RetryMiddleware + tenacity 退避引擎

**已否决**：
- 扩展现有 middleware（PrimaryAgentMiddleware / ToolValidationMiddleware）—— 违反单一职责（前者已担 task loop guard + 意图捕获 + 自动续接三职责），两处重复实现
- tenacity 装饰 LLM 客户端层 —— 重试循环在 `model.ainvoke` 内部，middleware 不感知每次 attempt；虽可用 langchain callback handler 补埋点，但 LLM/工具两套机制不统一，错误分类/降级/配置分散

**选定**：单一 `RetryMiddleware`，内部用 tenacity 做退避引擎。重试循环在 middleware 层 → 每次 attempt 天然在 `optional_span` 内 → 埋点统一、LLM/工具共享错误分类与降级出口。

### 3.4 工具重试只重试 `asyncio.TimeoutError`

工具调用失败分两类：
- **通信层失败**（前端没响应 / 超时）→ 可重试
- **业务失败**（`success=false`，如参数缺失）→ 不重试，原样给 LLM

`asyncio.TimeoutError` 是唯一值得重试的（连接还在、前端偶发无响应）。连接断开类异常（`WebSocketDisconnect` / `WebSocketException` / `ConnectionError`）**不处理**——当前无 WebSocket 重连机制，重试 `send_json` 必然失败，原样冒泡由现有 `websocket.py` try/except 处理。

### 3.5 bridge 超时改抛异常

由 3.4 推出：`bridge.send_tool_call` 的超时分支移除 `except TimeoutError → return {success:false}`，改为让 `asyncio.TimeoutError` 抛出，由 RetryMiddleware 按异常类型重试。否则超时（最该重试的通信失败）会被当成 `success=false` 业务失败不重试，违背初衷。

### 3.6 工具超时重试耗尽：转 ToolMessage 给 LLM 消化

超时重试 3 次仍失败 → 构造 `{success:false, message:"工具调用超时，请稍后重试"}` 的 `ToolMessage` 返回，LLM 自然告诉用户超时并建议重试。**不短路、不新增 ResponseCode**。会话不中断。LLM 若再调工具由 task loop guard 兜底。

### 3.7 LLM 降级：新增 `LLM_UNAVAILABLE`，复用 task_loop_guard 改写模式

LLM 是核心，不返回响应则流程卡死，必须短路降级。新增唯一 `ResponseCode.LLM_UNAVAILABLE`，复用 `PrimaryAgentMiddleware._build_shortcut_response` 的改写 `ModelResponse` 模式。

---

## 4. 架构与组件

### 4.1 RetryMiddleware 挂载位置

挂在 orchestrator 和所有子 Agent 的 middleware 链**最内层**（紧贴真实 LLM/工具调用），确保重试只重试实际调用，不重复执行 task loop guard / 意图捕获 / dynamic_prompt 等外层逻辑。具体列表位置在实现时按 deepagents middleware 执行顺序确认（最内层 = 列表末尾）。

```
orchestrator 链:  [PrimaryAgentMiddleware → ResponseStabilization → dynamic_prompt → RetryMiddleware]
子 Agent 链:      [ToolValidationMiddleware → ResponseStabilization → dynamic_prompt → RetryMiddleware]
                                                                                  ↑ 最内层
```

### 4.2 组件

| 组件 | 职责 |
|------|------|
| `RetryMiddleware.awrap_model_call` | LLM 调用重试 + 耗尽降级 |
| `RetryMiddleware.awrap_tool_call` | 工具调用重试 + 耗尽转 ToolMessage |
| `RETRYABLE_LLM_ERRORS`（异常类型元组）| LLM 可重试异常集合，构造时按 `retry_on_parse_error` 动态含/不含 `ValidationError` |
| `RetryConfig`（`infrastructure/config.py`）| 读取 retry 配置 |
| tenacity `AsyncRetrying` | 退避/停止/重试条件引擎 |

> 工具层无需 `_is_retryable_tool_outcome` 函数——tenacity 直接 `retry_if_exception_type(asyncio.TimeoutError)`。

### 4.3 数据流

```
awrap_model_call → optional_span("llm.retry") → tenacity.AsyncRetrying → handler(真实 model 调用)
   └ 成功 → 返回 ModelResponse
   └ 可重试异常(429/超时/5xx/连接) → before_sleep 埋点 → 退避后重试
   └ 不可重试 / 重试耗尽 → 降级：改写 ModelResponse 为 LLM_UNAVAILABLE（复用 task_loop_guard 模式）

awrap_tool_call → optional_span("tool.retry") → tenacity.AsyncRetrying → handler(真实工具函数)
   └ 成功 / success=false → 原样返回（success=false 给 LLM 消化）
   └ asyncio.TimeoutError → before_sleep 埋点 → 退避后重试
   └ TimeoutError 重试耗尽 → 转 ToolMessage(success=false, 超时提示) 给 LLM
   └ 其他异常(WebSocketDisconnect 等) → tenacity 不重试，原样 reraise 冒泡
```

---

## 5. LLM 重试机制

### 5.1 可重试错误白名单

| 可重试（退避后重试） | 不可重试（直接降级） |
|------|------|
| `openai.APITimeoutError` | `openai.BadRequestError`（400，含非法 JSON）|
| `openai.RateLimitError`（429）| `openai.AuthenticationError`（401）|
| `openai.APIConnectionError` | `openai.PermissionDeniedError`（403）|
| `openai.InternalServerError`（5xx）| `openai.NotFoundError`（404）|
| | `pydantic.ValidationError`（ToolStrategy 解析失败，默认不重试）|

`ValidationError` 是否重试由 `retry.llm.retry_on_parse_error` 配置控制（默认 `false`）。`true` 时动态加入可重试白名单。

### 5.2 tenacity 配置（LLM 层，纯异常驱动）

```python
AsyncRetrying(
    stop=stop_after_attempt(cfg.llm.max_attempts),           # 默认 3
    wait=wait_exponential_jitter(initial=cfg.llm.base_delay,  # 默认 1.0
                                 max=cfg.llm.max_delay),      # 默认 10.0
    retry=retry_if_exception_type(RETRYABLE_LLM_ERRORS),     # 异常类型元组，按 retry_on_parse_error 动态含/不含 ValidationError
    before_sleep=_log_retry,
    # 不设 reraise：耗尽后抛 RetryError，由 awrap_model_call catch 走 exhausted 降级；
    # 不可重试异常 tenacity 不重试、直接抛（不包装成 RetryError），由 catch 走 non_retryable 降级
)
```

### 5.3 降级触发与 catch 范围

```python
async def awrap_model_call(self, request, handler):
    with optional_span("llm.retry") as span:
        try:
            return await self._retrying_llm(handler, request)
        except RetryError:                      # 重试耗尽
            span.set_attribute("retry.outcome", "exhausted")
            return self._degrade_llm()
        except (openai.APIError, ValidationError) as e:   # 不可重试
            span.set_attribute("retry.outcome", "non_retryable")
            span.set_attribute("retry.error", type(e).__name__)
            return self._degrade_llm()
        # 其余异常（CancelledError 等）不 catch，原样抛出
```

catch 集合 = `{openai.APIError, pydantic.ValidationError, tenacity.RetryError}`，精确不误伤。

---

## 6. 工具重试机制

### 6.1 bridge 改动（`bridge/ws_bridge.py:send_tool_call`）

```python
# 修订前：超时被 catch 成 dict
try:
    result = await asyncio.wait_for(future, timeout=timeout)
    return result
except TimeoutError:
    return {"success": False, "message": f"工具调用超时: {tool_func}"}

# 修订后：超时直接抛 asyncio.TimeoutError，由 RetryMiddleware 捕获重试
result = await asyncio.wait_for(future, timeout=timeout)
return result
```

副作用：工具函数不再需要处理「超时 dict」。

### 6.2 tenacity 配置（工具层，只重试 TimeoutError）

```python
AsyncRetrying(
    stop=stop_after_attempt(cfg.tool.max_attempts),
    wait=wait_exponential_jitter(initial=cfg.tool.base_delay, max=cfg.tool.max_delay),
    retry=retry_if_exception_type(asyncio.TimeoutError),   # 唯一
    before_sleep=_log_retry,
)
```

`reraise` 不设 `True`（默认抛 `RetryError`），由 RetryMiddleware catch 后转 ToolMessage。

### 6.3 超时耗尽转 ToolMessage

```python
async def awrap_tool_call(self, request, handler):
    with optional_span("tool.retry") as span:
        try:
            return await self._retrying_tool(handler, request)
        except RetryError:    # TimeoutError 重试耗尽
            span.set_attribute("retry.outcome", "exhausted")
            tool_call_id = request.tool_call.get("id", "")
            return ToolMessage(
                content=json.dumps({"success": False, "message": "工具调用超时，请稍后重试"}),
                tool_call_id=tool_call_id,
            )
        # TimeoutError 以外的异常 tenacity 不重试、原样 reraise，不在此 catch，冒泡到 websocket handler
```

---

## 7. 降级出口

### 7.1 新增 ResponseCode（唯一）

```python
class ResponseCode(StrEnum):
    ...
    LLM_UNAVAILABLE = auto()   # LLM 调用重试耗尽 / 不可重试失败
```

工具层**不新增 code**。

### 7.2 SHORTCUT_RESPONSES 新增

```python
"llm_unavailable": AgentResponse(
    status="error",
    code=ResponseCode.LLM_UNAVAILABLE,
    summary="AI 服务暂时不可用，请稍后重试",   # 面向用户，不带 429/超时等技术细节
    suggestions=[],
),
```

### 7.3 LLM 降级路径（复用 task_loop_guard 改写模式）

```python
def _degrade_llm(self) -> ModelResponse:
    shortcut = SHORTCUT_RESPONSES["llm_unavailable"]
    display = response_util.render(shortcut)
    return message_util.build_primary_agent_response(display, shortcut, "call_llm_unavailable")
    # 构造 AIMessage(content=display, structured_response=shortcut, tool_calls=[AgentResponse tool_call])
    # → ToolStrategy 解析 → on_chain_end render 发前端，与 task_loop_guard 同一条出口路径
```

### 7.4 一致性保证

- **orchestrator 与子 Agent 行为一致**：都是改写 `ModelResponse` → ToolStrategy 解析 → `on_chain_end` render。子 Agent LLM 耗尽 → `LLM_UNAVAILABLE` 的 structured_response 经 task 回传 orchestrator → orchestrator 直接作为最终响应发用户。
- **不进意图集合**：`LLM_UNAVAILABLE` 不加入 `INTENTION_TO_CATCH_CODES` / `INTENTION_RESUME_TRIGGER_CODES`——失败时用户意图仍在 messages 历史里，下一轮 LLM 自然可见，不需要中间件续接。

---

## 8. 埋点

```python
def _log_retry(retry_state):
    span = trace.get_current_span()       # 处于 with optional_span(...) 内，自动挂到正确 span 树
    span.set_attribute("retry.attempt", retry_state.attempt_number)
    span.set_attribute("retry.next_delay", retry_state.next_action.sleep)
    span.set_attribute("retry.last_error", str(retry_state.outcome.exception()))
    logger.warning("重试 attempt=%d ...", retry_state.attempt_number)
```

- span 命名：`llm.retry` / `tool.retry`，是现有 `orchestrator.llm` / `tool.<name>` 的子 span
- `observability.enabled=false` 时 `optional_span` 是 no-op，`get_current_span()` 返回 INVALID_SPAN，`set_attribute` 无副作用——**retry 仍正常工作，埋点零开销**

---

## 9. 配置（`application.yaml` 新增 `retry` 段）

```yaml
retry:
  enabled: true          # false 时 RetryMiddleware 透传，零开销
  llm:
    max_attempts: 3
    base_delay: 1.0
    max_delay: 10.0
    retry_on_parse_error: false   # ToolStrategy 解析失败是否重试，默认 false
  tool:
    max_attempts: 3
    base_delay: 1.0
    max_delay: 10.0
```

`RetryConfig` 在 `infrastructure/config.py` 新增，`RetryMiddleware` 构造时读取。

---

## 10. 边界与零依赖

| 边界 | 处理 |
|------|------|
| `retry.enabled=false` | RetryMiddleware 透传 `return await handler(request)`，不开 span 不重试 |
| `retry.enabled` 与 `observability.enabled` 独立 | retry 是业务恢复、observability 是观测，互不依赖 |
| 与现有 middleware 交互 | RetryMiddleware 最内层；NO_SCENE 短路在更外层（不进 handler 不触发重试）；task loop guard / 意图捕获在 model 返回后跑，重试时 model 未返回，状态不受影响 |
| RetryMiddleware 自身兜底 | catch 集合精确，其余异常原样抛出（不吞 CancelledError）；不额外加 try/except 兜底 |

---

## 11. 测试策略

| 类别 | 用例 |
|------|------|
| 单元 | `RETRYABLE_LLM_ERRORS` 元组构造（`retry_on_parse_error` 开关下含/不含 `ValidationError`）；`RetryConfig` yaml 加载；降级 `LLM_UNAVAILABLE` shortcut 构造 + render |
| 集成-LLM | 前两次 `RateLimitError` 第三次成功 → 重试 2 次成功、span 有 `retry.attempt`；始终 `RateLimitError` → 重试 3 次耗尽降级 `LLM_UNAVAILABLE`；`BadRequestError` → 不重试直接降级 |
| 集成-工具 | 前两次 `TimeoutError` 第三次成功 → 重试成功；始终 `TimeoutError` → 耗尽转 `ToolMessage(success=false, 超时)`；`WebSocketDisconnect` → 不重试原样抛出 |
| 集成-bridge | `bridge.send_tool_call` 超时抛 `asyncio.TimeoutError`（不再返回 dict）|
| 配置 | `retry.enabled=false` → 透传不重试 |

---

## 12. 不在本次范围

- **状态回滚**：部分成功时撤销已生效操作。延后，真要做时重新定义成「保留成功部分 + 重试失败部分」语义。
- **WebSocket 重连机制**：连接断开类异常当前原样冒泡，不重试。若未来要支持断连重试，需先加重连机制（独立话题）。
- **`tool_result.retriable` 协议字段**：已否决（YAGNI）。需重试的个别工具失败作为业务需求扩展，不进基础架构。
- **业务级指标埋点**（Skill 使用次数、工具调用分布）：属 Phase 1A-2，不在本次。
