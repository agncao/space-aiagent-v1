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

    步骤:
    1. 接收用户输入和 thread_id
    2. 获取或创建对应 thread 的 Agent 实例
    3. 调用 Agent 的 invoke 方法
    4. 返回结果

    TODO: 实现
    """
    return InvokeResponse(output={}, thread_id=request.thread_id)


@router.get("/get_state/{thread_id}")
async def get_state(thread_id: str) -> dict:
    """
    获取指定会话的当前状态

    步骤:
    1. 根据 thread_id 查找 Agent 的 checkpoint
    2. 返回当前状态

    TODO: 实现
    """
    return {"thread_id": thread_id, "state": {}}


@router.get("/health")
async def health_check() -> dict:
    """健康检查"""
    return {"status": "ok", "service": "space-aiagent"}
