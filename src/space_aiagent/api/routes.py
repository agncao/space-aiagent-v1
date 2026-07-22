"""
REST API 路由

提供 HTTP 端点用于简单的同步调用和状态查询。

端点:
- POST /api/v1/space/invoke: 同步调用 Agent
- GET /api/v1/space/get_state/{thread_id}: 获取会话状态
- GET /api/v1/space/health: 健康检查
"""

from fastapi import APIRouter

from space_aiagent.models.schemas import InvokeRequest, InvokeResponse

router = APIRouter(prefix="/api/v1/space", tags=["space"])


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    """
    同步调用 Agent

    适用于简单场景，复杂交互请使用 SSE（POST /api/v1/space/chat）。
    """
    from langchain_core.messages import HumanMessage

    from space_aiagent.agents.orchestrator import create_orchestrator
    from space_aiagent.agents.subagents import load_subagents

    subagents = load_subagents()
    agent = create_orchestrator(subagents, checkpointer=None)

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=request.content)]},
        config={"configurable": {"thread_id": request.thread_id}},
    )

    # 提取最终 AI 回复
    output_text = ""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.type == "ai":
            output_text = msg.content
            break

    return InvokeResponse(output={"content": output_text}, thread_id=request.thread_id)


@router.get("/get_state/{thread_id}")
async def get_state(thread_id: str) -> dict:
    """获取指定会话的当前状态"""
    return {"thread_id": thread_id, "state": {}}


@router.get("/health")
async def health_check() -> dict:
    """健康检查"""
    return {"status": "ok", "service": "space-aiagent"}
