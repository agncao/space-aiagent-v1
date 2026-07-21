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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from space_aiagent.agents.orchestrator import create_orchestrator
from space_aiagent.agents.subagents import load_subagents
from space_aiagent.bridge import SessionManager, bridge_var
from space_aiagent.infrastructure.database import get_db
from space_aiagent.infrastructure.logging import get_logger
from space_aiagent.infrastructure.observability import optional_span, set_span_io
from space_aiagent.middleware.primary_agent_middleware import orchestrator_task_streak_var
from space_aiagent.models.messages import ToolResultMessage
from space_aiagent.models.response_schema import response_util
from space_aiagent.models.sse_events import TERMINAL_EVENTS, SSEEventType, format_sse_frame

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/space", tags=["space"])

session_manager = SessionManager()


class ChatRequest(BaseModel):
    """POST /chat 请求体

    与 WS 时代的 UserInputMessage 等价但剥离 WS 专用字段（type），是纯 HTTP 入参。
    """

    content: str = Field(description="用户输入的文本")
    thread_id: str = Field(description="会话 thread_id（用于 checkpointer 持久化）")
    message_id: str = Field(default="", description="消息唯一ID（前端生成）")
    current_scene_name: str | None = Field(
        default=None,
        description="当前已打开的场景名（注入 SpaceAgentState 初值）",
    )


class ToolResultRequest(BaseModel):
    """POST /tool-result 请求体

    与 WS 时代的 ToolResultMessage 等价但剥离 WS 专用字段（type），
    并显式携带 thread_id（原 ToolResultMessage 通过 WSMessage 基类携带 thread_id，
    HTTP 入参需独立字段）。字段名/类型/默认值与 ToolResultMessage 对齐。
    """

    tool_func: str = Field(description="工具函数名")
    args: dict = Field(default_factory=dict, description="工具参数")
    tool_call_id: str = Field(description="工具调用ID（与 tool_start 帧一致）")
    thread_id: str = Field(description="会话 thread_id（用于定位 StreamBridge）")
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(default="", description="结果消息")
    data: dict | list | None = Field(default=None, description="返回数据")
    code: str = Field(default="", description="消息码")


# ── Agent 实例缓存 + checkpointer（迁移自 websocket.py）───────────────────
_agent_cache: dict[str, object] = {}

# 数据库 checkpointer（全局共享，SQLite 持久化）
_checkpointer: Any = None


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
    MemoryMiddleware/SubagentsMiddleware 改写后的结构）。基本版兜底，
    T5 会精细化 source 抽取。
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


async def run_agent(bridge: Any, chat_request: ChatRequest) -> None:
    """后台执行 agent（流式），把事件 emit 到 bridge._queue

    本函数在 agent_task（独立 asyncio.Task）中运行，task 创建时拷贝当前 context，
    故能通过 bridge_var.get() 拿到 bridge。ContextVar 的 set/reset 由 handler /
    event_generator 负责，本函数内不重复 set/reset。
    """
    thread_id = chat_request.thread_id
    # agent.session 是 trace root（main.py 用 excluded_urls 排除 /api/v1/space/chat
    # 的 FastAPI 自动 server span，让本手动 span 当 root，input/output 自动成 trace IO）
    with optional_span(
        "agent.session",
        **{
            "agent.thread_id": thread_id,
            "agent.scene_name": chat_request.current_scene_name or "",
        },
    ) as span:
        try:
            agent = await _get_or_create_agent(thread_id)
            # 记录输入 IO：放在 try 内部，避免 set_span_io 自身异常（如 OTel 误配置）
            # 时绕过下方 except，导致 SSE 流静默截断（无 error 帧）
            set_span_io(span, input=chat_request.content)

            async for event in agent.astream_events(
                {
                    "messages": [HumanMessage(content=chat_request.content)],
                    # 注入 SpaceAgentState.current_scene_name 初值，
                    # 后续工具返回 Command 更新该字段，跨 task 边界自动同步
                    "current_scene_name": chat_request.current_scene_name,
                    # 查询结果只属于当前轮次，避免历史数据影响后续响应渲染
                    "scenario_query_results": None,
                },
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 100,
                },
                version="v2",
            ):
                kind = event["event"]
                name = event.get("name", "")
                data = event.get("data", {})
                try:
                    if kind == "on_chat_model_stream":
                        chunk = data.get("chunk")
                        if chunk is None:
                            continue
                        text = _extract_chunk_text(chunk)
                        if not text:
                            continue
                        # source 暂留空字符串（T5 精细化 metadata 解析）
                        await bridge._emit(
                            SSEEventType.TOKEN,
                            {"content": text, "source": ""},
                        )
                    elif kind == "on_tool_start":
                        # AgentResponse 是结构化输出占位工具，不向前端发事件
                        if name == "AgentResponse":
                            continue
                        # StreamBridge.send_tool_call 已负责 emit tool_start/tool_args，
                        # 此处不再额外发进度消息
                    elif kind == "on_chain_end":
                        output = data.get("output")
                        if output is None or not isinstance(output, dict):
                            continue
                        agent_response = output.get("structured_response")
                        if agent_response is None:
                            continue
                        rendered = response_util.render(
                            agent_response,
                            scenario_infos=output.get("scenario_query_results"),
                        )
                        set_span_io(span, output=rendered)
                        await bridge._emit(
                            SSEEventType.DONE,
                            {"content": rendered},
                        )
                        return
                except Exception as ex:
                    logger.exception(
                        "Agent 事件处理出错",
                        event_kind=kind,
                        event_name=name,
                        thread_id=thread_id,
                    )
                    raise ex

            # 流正常结束但未收到 AgentResponse on_chain_end：兜底发 done
            logger.warning("流结束未收到 AgentResponse on_chain_end 事件", thread_id=thread_id)
            set_span_io(span, output="处理完成。")
            await bridge._emit(SSEEventType.DONE, {"content": "处理完成。"})

        except Exception as e:
            logger.exception("Agent 执行出错", thread_id=thread_id)
            with contextlib.suppress(Exception):
                await bridge._emit(SSEEventType.ERROR, {"message": str(e)})


async def event_generator(
    bridge: Any,
    chat_request: ChatRequest,
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
    thread_id = chat_request.thread_id
    # 在 generator 当前 context 注入 ContextVar（同 context 内后续 reset）
    bridge_token = bridge_var.set(bridge)
    task_streak_token = orchestrator_task_streak_var.set(0)
    # create_task 拷贝当前 context → agent_task 内 bridge_var.get() 可见
    agent_task = asyncio.create_task(run_agent(bridge, chat_request))

    try:
        while True:
            item = await bridge._queue.get()
            event_name = item["event"]
            yield format_sse_frame(event_name, item["data"])
            if event_name in TERMINAL_EVENTS:
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
        event_generator(bridge, chat_request),
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
