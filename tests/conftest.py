"""测试公共 fixtures。"""

import httpx
import pytest

from space_aiagent.main import app


@pytest.fixture
async def client():
    """不启动真实网络端口的 FastAPI 异步客户端。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
