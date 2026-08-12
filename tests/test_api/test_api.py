"""API 层测试"""


async def test_health_check(client):
    """测试健康检查端点"""
    response = await client.get("/api/v2/space/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


async def test_openapi_exposes_only_v2_space_routes(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == "2.0.0"
    space_paths = [path for path in schema["paths"] if "/space/" in path]
    assert space_paths
    assert all(path.startswith("/api/v2/space/") for path in space_paths)


async def test_removed_legacy_route_is_not_registered(client):
    response = await client.get("/api/v1/space/health")
    assert response.status_code == 404
