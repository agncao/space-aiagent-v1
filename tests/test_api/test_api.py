"""API 层测试"""


async def test_health_check(client):
    """测试健康检查端点"""
    response = await client.get("/api/v1/space/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
