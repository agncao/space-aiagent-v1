"""
pytest 配置和共享 fixtures

提供测试中常用的 fixtures:
- 测试用的 Settings 配置
- 测试用的 FastAPI 客户端
- 测试用的 mock bridge
"""

import pytest
from httpx import ASGITransport, AsyncClient

from space_aiagent.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """异步 HTTP 测试客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# TODO: 添加更多 fixtures
# - mock_settings: 测试用的配置
# - mock_bridge: 模拟远程工具桥接
# - mock_websocket: 模拟 WebSocket 连接
# - sample_scenario_config: 示例场景配置
# - sample_entity_config: 示例实体配置
