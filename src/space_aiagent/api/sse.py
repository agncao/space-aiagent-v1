"""
SSE 端点（POST /chat）

核心通信通道，处理前端与 Agent 的实时单向流（SSE）。

消息流:
1. 前端 POST /chat {content, thread_id, ...} → 并发检查（409）→ 创建 StreamBridge
2. bridge_var / orchestrator_task_streak_var 注入到当前请求 context
3. 后台 asyncio.create_task(run_agent) 执行 Agent（task 拷贝当前 context，故能拿到 bridge_var）
4. event_generator 从 bridge._queue 取事件，转 SSE 帧返回；done/error 后关闭流
5. finally：cancel agent_task + bridge.cleanup + session_manager.unregister + 重置 ContextVar

会话持久化:
使用 AsyncSqliteSaver（基于 SQLite）持久化 LangGraph checkpoint，
确保跨轮次会话记忆不丢失。与 MemorySaver 不同，SQLite 持久化不受
进程重启、热重载影响。

ContextVar 注入策略（与 WS 时代的关键差异）:
- bridge_var / orchestrator_task_streak_var 在 handler 中 set（请求 context），
  而非在 run_agent 内 set（run_agent 在 agent_task 上下文）。
- asyncio.create_task 拷贝当前 context，故 agent_task 与工具函数能通过
  bridge_var.get() 拿到 bridge。
- reset 在 event_generator 的 finally 中执行（与 set 同处一个请求 context）。
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from space_aiagent.agents.orchestrator import create_orchestrator
from space_aiagent.agents.subagents import load_subagents
from space_aiagent.bridge import SessionManager, StreamBridge, bridge_var
from space_aiagent.infrastructure.database import get_db
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.middleware.primary_agent_middleware import orchestrator_task_streak_var
from space_aiagent.models.messages import ToolResultMessage
from space_aiagent.models.response_schema import response_util
from space_aiagent.models.sse_schemas import ChatRequest, ToolResultRequest, ResumeRequest,TERMINAL_EVENTS, SSEEventType


logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/space", tags=["space"])

session_manager = SessionManager()



# ── Agent 实例缓存 + checkpointer（迁移自 websocket.py）───────────────────
_agent_cache: dict[str, object] = {}

# 数据库 checkpointer（全局共享，SQLite 持久化）
_checkpointer: Any = None

def _format_sse_frame(event: str, data: dict) -> str:
    """生成标准 SSE 帧

    输出格式（SSE spec）：
        event: <event>\\n
        data: <json>\\n
        \\n

    - 两个字段行（event: / data:），各自以 ``\\n`` 结尾
    - 末尾空行（``\\n``）作为帧分隔符
    - json.dumps 单行输出（无内部换行），故只需一行 ``data:``
    - ensure_ascii=False：保留中文可读性（仓库面向用户的文本为中文）

    Args:
        event: 事件类型（建议传 SSEEventType 成员或其字符串值）
        data: 事件数据，将以 JSON 序列化进 ``data:`` 行

    Returns:
        标准 SSE 帧字符串
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

async def _get_checkpointer() -> Any:
    """获取或初始化全局 AsyncSqliteSaver checkpointer（延迟初始化）"""
    global _checkpointer
    if _checkpointer is None:
        db = await get_db()
        _checkpointer = await db.get_checkpointer()
        logger.info("AsyncSqliteSaver checkpointer 已初始化（SQLite 持久化）")
    return _checkpointer


async def _get_or_create_agent(thread_id: str) -> Any:
    """获取或创建指定 thread 的 Agent 实例

    测试可通过 monkeypatch 替换本函数注入 fake agent。
    """
    if thread_id in _agent_cache:
        return _agent_cache[thread_id]

    subagents = load_subagents()
    checkpointer = await _get_checkpointer()
    agent = create_orchestrator(subagents, checkpointer, thread_id=thread_id)
    _agent_cache[thread_id] = agent
    logger.info("Agent 实例已创建", thread_id=thread_id)
    return agent


def _extract_chunk_text(chunk: Any) -> str:
    """从 on_chat_model_stream 的 chunk 中抽取文本内容

    chunk.content 既可能是 str，也可能是 list[ContentBlock]（如 deepagents
    MemoryMiddleware/SubagentsMiddleware 改写后的结构）。

    spike 结论（2026-07-21，真 LLM：DashScope qwen）：结构化输出的 token
    以 tool_call_chunks 形式流式（content 为空），自由文本以 content 形式
    流式。本函数只取 content 文本，空 content（tool_call JSON 碎片）自然
    返回 "" → 调用方不发 token，从而过滤掉 orchestrator/子 agent 的结构化
    JSON 参数碎片，只把真正的自由文本 token 发给前端。
    """
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 拼接所有 text 类型 block 的 text 字段
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    parts.append(t)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


async def _handle_interrupts(bridge: StreamBridge, interrupts: list) -> None:
    """把 LangGraph interrupt 列表转成 SSE interrupt 帧，随后发 done(interrupted) 收尾。

    emit 顺序：先逐个 interrupt 帧（一次暂停可能含多个），再一个 done（终态）。
    ``bridge._emit`` 自动注入 thread_id，故 payload 不再手传。

    payload 形状判别（三分支，按 ``is_custom`` 顶层判别符区分来源）：
    - ``is_custom`` 为 True：自定义/编程式 interrupt（中间件驱动，如
      SceneAgentHitlMiddleware）。四件套 ``is_custom + interrupt_type + message
      + data`` 透传，前端按 ``interrupt_type`` 分发渲染（scene_select 带候选列表
      走列表选择 UI；save_confirm 仅消息走 Y/N UI）。新增自定义中断点只需在中间件
      里 emit 同样四件套，无需改本函数。
    - 含 ``action_requests``：声明式 ``interrupt_on``（子 Agent 的
      HumanInTheLoopMiddleware）→ ``hitl_approval``，前端据此渲染审批 UI
      （action_requests 携带工具名/参数/description，review_configs 携带允许的决策）。
    - 其它：unknown 透传（截断 2000 字符），保证任何中断都不会让流挂死。

    ⚠️ ``interrupt_type`` 命名空间：``hitl_approval`` 为声明式专用，自定义中断
    用描述性名字（scene_select / save_confirm / ...），两者不重叠，前端可仅凭
    ``interrupt_type`` 区分渲染，``is_custom`` 仅作冗余显式标记。
    """
    for intr in interrupts:
        value = getattr(intr, "value", intr)
        if isinstance(value, dict) and value.get("is_custom",False):
            # 自定义/编程式 interrupt：四件套透传，前端按 interrupt_type 分发
            await bridge._emit(
                SSEEventType.INTERRUPT,
                {
                    "is_custom": True,
                    "interrupt_type": value.get("interrupt_type", "unknown"),
                    "message": value.get("message", ""),
                    "data": value.get("data"),
                },
            )
        elif isinstance(value, dict) and "action_requests" in value:
            # 声明式 interrupt_on：HumanInTheLoopMiddleware 的 HITLRequest
            await bridge._emit(
                SSEEventType.INTERRUPT,
                {
                    "is_custom": False,
                    "interrupt_type": "hitl_approval",
                    "action_requests": value["action_requests"],
                    "review_configs": value.get("review_configs", []),
                },
            )
        else:
            await bridge._emit(
                SSEEventType.INTERRUPT,
                {
                    "is_custom": False,
                    "interrupt_type": "unknown",
                    "interrupt_value": str(value)[:2000],
                },
            )
    await bridge._emit(SSEEventType.DONE, {"content": "", "interrupted": True})


async def run_agent(bridge: StreamBridge, input_data: ChatRequest | Command) -> None:
    """后台执行 agent（流式），把事件 emit 到 bridge._queue。

    本函数在 agent_task（独立 asyncio.Task）中运行，task 创建时拷贝当前 context，
    故能通过 bridge_var.get() 拿到 bridge。ContextVar 的 set/reset 由 handler /
    stream_chat_response 负责，本函数内不重复 set/reset。

    两种输入模式：
    - chat（ChatRequest）：首轮对话，input 为 messages + state 初值。
    - resume（Command(resume=...)）：interrupt 续跑，input 即 Command 本身，
      LangGraph 把其 resume 值送达 ``interrupt()`` 暂停点，图继续。current_scene_name
      等状态由 checkpoint 恢复，不再从请求体注入。
    """
    thread_id = bridge._thread_id
    if isinstance(input_data, Command):
        graph_input: ChatRequest | Command | dict = input_data
        span_event: dict = {"id": "agent.interrupt", "attributes": {"agent.thread_id": thread_id}}
        span_input_repr = f"resume:{input_data.resume!r}"
    else:
        graph_input = {
            "messages": [HumanMessage(content=input_data.content)],
            # 注入 SpaceAgentState.current_scene_name 初值，后续工具返回 Command
            # 更新该字段，跨 task 边界自动同步
            "current_scene_name": input_data.current_scene_name,
            # 查询结果只属于当前轮次，避免历史数据影响后续响应渲染
            "scenario_query_results": None,
        }
        span_event = {"id": "agent.session", "attributes": {"agent.thread_id": thread_id,
                                                            "agent.scene_name": input_data.current_scene_name or ""}}
        span_input_repr = input_data.content

    # agent.session 是 trace root（main.py 用 excluded_urls 排除 /api/v1/space/chat
    # 的 FastAPI 自动 server span，让本手动 span 当 root，input/output 自动成 trace IO）
    with optional_span(
            span_event.get("id", "agent.session"),
            **span_event.get("attributes", {"agent.thread_id": thread_id}),
    ) as span:
        try:
            agent = await _get_or_create_agent(thread_id)
            # 记录输入 IO：放在 try 内部，避免 set_span_io 自身异常（如 OTel 误配置）
            # 时绕过下方 except，导致 SSE 流静默截断（无 error 帧）
            set_span_io(span, input=span_input_repr)

            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
            # 切 astream（spec D2）：astream_events 检测不到 graph interrupt()。
            # subgraphs=True 让子 Agent 的 interrupt 上浮到顶层流；version="v2" 统一
            # chunk 形状（dict：type/data/ns，values 事件额外带 interrupts）。
            async for chunk in agent.astream(
                    graph_input,
                    config=config,
                    stream_mode=["messages", "values"],
                    subgraphs=True,
                    version="v2",
            ):
                chunk_type = chunk.get("type")
                try:
                    # values 流：先判中断（必须在 messages 处理之前）。interrupt 由
                    # 子 Agent 的 HumanInTheLoopMiddleware 触发（声明式 interrupt_on），
                    # payload 形如 {action_requests, review_configs}。_handle_interrupts
                    # 负责 emit interrupt(s) + done(interrupted=True)，发完即结束本流。
                    if chunk_type == "values" and chunk.get("interrupts"):
                        await _handle_interrupts(bridge, chunk["interrupts"])
                        return
                    # messages 流：token。subgraphs=True 下所有消息类型都会上浮，必须
                    # 跳过 ToolMessage（content 是工具结果 JSON，已由 bridge 的
                    # tool_result 帧发出）与空 content（结构化输出的 tool_call 碎片）。
                    if chunk_type == "messages":
                        msg, metadata = chunk["data"]
                        if getattr(msg, "type", None) == "tool":
                            continue
                        text = _extract_chunk_text(msg)
                        if not text:
                            continue
                        # source 从 metadata best-effort 解析。spike 确认 langgraph_node
                        # 实测统一为 "model"，无法区分 orchestrator / 子 agent，故 source
                        # 暂为统一值；未来 metadata 带 agent 标识时在此细化即可。
                        source = (metadata or {}).get("langgraph_node") or "agent"
                        await bridge._emit(
                            SSEEventType.TOKEN,
                            {"content": text, "source": source},
                        )
                except Exception as ex:
                    logger.exception(
                        "Agent 事件处理出错",
                        chunk_type=chunk_type,
                        thread_id=thread_id,
                    )
                    raise ex

            # 流正常结束（图到达 END，未触发 interrupt）：从最终 state 取
            # structured_response 渲染。注意：不能在 values 流里早判 structured_response
            # —— astream(stream_mode="values") 每步吐的是累计 state 快照，且
            # structured_response 跨轮不清空，新一轮的首个 values chunk 仍带着上一轮的
            # structured_response，早判会误触发 done。旧的 on_chain_end 路径安全是因为
            # 其 output 是调用作用域的新值；切 astream 后改用 aget_state 取终态值。
            state = await agent.aget_state(config)
            values: dict = getattr(state, "values", None) or {}
            agent_response = values.get("structured_response")
            if agent_response is not None:
                rendered = response_util.render(
                    agent_response,
                    scenario_infos=values.get("scenario_query_results"),
                )
            else:
                logger.warning("流结束但 state 无 structured_response", thread_id=thread_id)
                rendered = "处理完成。"
            set_span_io(span, output=rendered)
            await bridge._emit(SSEEventType.DONE, {"content": rendered})

        except Exception as e:
            logger.exception("Agent 执行出错", thread_id=thread_id)
            with contextlib.suppress(Exception):
                await bridge._emit(SSEEventType.ERROR, {"message": str(e)})


async def stream_chat_response(
        bridge: StreamBridge,
        input_data: ChatRequest | Command,
) -> AsyncIterator[str]:
    """SSE 事件生成器：注入 ContextVar + 启动 agent_task + 消费 bridge._queue

    ContextVar 注入与 agent_task 启动放在 generator 内部（而非 handler）：
    Starlette 把 StreamingResponse 的 body 迭代放在一个被 copy 出来的 context
    里执行，handler 与 generator 不在同一 context；若在 handler 里 set、在
    generator 里 reset 会抛「Token was created in a different Context」。
    统一在 generator 内 set/reset 同一 context，且 asyncio.create_task 拷贝
    generator 当前 context → agent_task + 工具函数能通过 bridge_var.get() 拿到 bridge。

    finally 兜底清理：
    - cancel agent_task（如还在跑）
    - bridge.cleanup（pending futures 置 ConnectionError）
    - session_manager.unregister
    - 重置 ContextVar（与 set 同 generator context）

    客户端断开：Starlette 在下一次 yield 抛 asyncio.CancelledError，
    finally 同样执行，agent_task 被 cancel，避免流挂死。
    """
    thread_id = bridge._thread_id
    # 在 generator 当前 context 注入 ContextVar（同 context 内后续 reset）。
    # chat 与 resume 都必须注入 bridge_var：resume 续跑时工具函数（delete_scene 等）
    # 仍通过 bridge_var.get() 拿到 bridge 执行前端调用。streak 每流重置为 0。
    bridge_token = bridge_var.set(bridge)
    task_streak_token = orchestrator_task_streak_var.set(0)
    # create_task 拷贝当前 context → run_agent 与工具函数内 bridge_var.get() 可见
    agent_task = asyncio.create_task(run_agent(bridge, input_data))
    token_parts: list[str] = []

    try:
        # 事件透传：bridge._queue 中的任意事件（token/tool_*/done/error，以及
        # 未来 graph interrupt() 落地后由 agent emit 的 interrupt）都经
        # _format_sse_frame 转成 SSE 帧发给前端。interrupt 当前无 emit 源
        # （graph interrupt() 是下一步任务），但事件类型已定义（T2）、
        # 透传路径已就绪，无需此处理特分支。
        while True:
            item = await bridge._queue.get()
            event_name = item["event"]
            if event_name == SSEEventType.TOKEN:
                content = item["data"].get("content", "")
                if content:
                    token_parts.append(str(content))
            yield _format_sse_frame(event_name, item["data"])
            if event_name in TERMINAL_EVENTS:
                if token_parts:
                    logger.info(
                        "SSE token 输出完成",
                        thread_id=thread_id,
                        content="".join(token_parts),
                    )
                break
    finally:
        # cancel 防御性：agent_task 可能已完成（done 后 return），cancel 对已完成的 task 是 no-op
        agent_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await agent_task
        bridge.cleanup()
        session_manager.unregister(thread_id)
        bridge_var.reset(bridge_token)
        orchestrator_task_streak_var.reset(task_streak_token)
        logger.info("SSE 流已关闭", thread_id=thread_id)


@router.post("/chat")
async def chat(chat_request: ChatRequest) -> StreamingResponse:
    """POST /chat — SSE 流式响应

    流程：
    1. 并发检查：若 thread_id 已有活跃 session → 409 Conflict
    2. 创建 StreamBridge（注册到 SessionManager）
    3. 返回 StreamingResponse（event_generator 负责 ContextVar 注入、启动
       agent_task、消费事件、清理）
    """
    thread_id = chat_request.thread_id

    # 并发护栏：同 thread_id 已有活跃 session → 409
    if session_manager.get_bridge(thread_id) is not None:
        logger.warning("拒绝并发请求：thread_id 已有活跃 session", thread_id=thread_id)
        raise HTTPException(status_code=409, detail="该会话已有活跃请求在处理")

    bridge = session_manager.register(thread_id)

    return StreamingResponse(
        stream_chat_response(bridge, chat_request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 透传，禁缓冲
            "Connection": "keep-alive",
        },
    )


@router.post("/tool-result")
async def tool_result(req: ToolResultRequest) -> dict:
    """POST /tool-result — 解析前端返回的工具执行结果

    流程：
    1. 通过 thread_id 定位活跃会话的 StreamBridge；无 bridge → 404
       （会话已结束 / 不存在 / 客户端未先 POST /chat）
    2. 构造 ToolResultMessage，调用 bridge.resolve_tool_result
       （resolve_tool_result 内部按 tool_call_id 找到 Future 并 set_result，
       排除 type/thread_id/tool_call_id/tool_func 字段；未知 id 仅 warning）
    3. 返回 {ok: true}

    本 handler 是短请求，不需 await 重活（resolve_tool_result 是同步操作）。
    """
    bridge = session_manager.get_bridge(req.thread_id)
    if bridge is None:
        # 无活跃会话：会话已结束 / 客户端未先 POST /chat / thread_id 错误
        logger.warning("tool-result 找不到活跃会话", thread_id=req.thread_id)
        raise HTTPException(status_code=404, detail="无活跃会话")

    # 构造 ToolResultMessage 并 resolve 对应 Future
    # resolve_tool_result 会从 _pending 中弹出并 set_result，
    # 触发正在 await 的 send_tool_call 继续执行 → emit tool_result / tool_end
    msg = ToolResultMessage(
        thread_id=req.thread_id,
        tool_func=req.tool_func,
        args=req.args,
        tool_call_id=req.tool_call_id,
        success=req.success,
        message=req.message,
        data=req.data,
        code=req.code,
    )
    bridge.resolve_tool_result(msg)
    logger.info(
        "tool-result 已 resolve",
        thread_id=req.thread_id,
        tool_call_id=req.tool_call_id,
        tool_func=req.tool_func,
        success=req.success,
    )
    return {"ok": True}


@router.post("/chat/{thread_id}/resume")
async def resume(thread_id: str, req: ResumeRequest) -> StreamingResponse:
    """POST /chat/{thread_id}/resume — interrupt 续跑，新开 SSE 流

    前端收到 ``event: interrupt`` 帧并收集用户决策后，POST 此端点恢复 Agent。
    本端点新开一条 SSE 流（spec D1）：interrupt 时首轮 /chat 流已 done+cleanup+
    unregister，故此处 register 全新 StreamBridge，复用同一 thread_id 的 checkpointer
    续跑。``req.resume`` 作为 ``Command(resume=...)`` 的值送达 ``interrupt()`` 暂停点。
    """
    # 并发护栏：同 thread_id 已有活跃 session → 409（与 /chat 同一不变式）
    if session_manager.get_bridge(thread_id) is not None:
        logger.warning("拒绝并发 resume：thread_id 已有活跃 session", thread_id=thread_id)
        raise HTTPException(status_code=409, detail="该会话已有活跃请求在处理")

    bridge = session_manager.register(thread_id)
    return StreamingResponse(
        stream_chat_response(bridge, Command(resume=req.resume)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
