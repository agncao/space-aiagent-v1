"""
space-aiagent FastAPI 应用入口

启动方式:
    # 开发环境
    python -m space_aiagent.main

    # 或使用 uvicorn
    uvicorn space_aiagent.main:app --reload --host 0.0.0.0 --port 8028
"""
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from space_aiagent.api.routes import router as api_router
from space_aiagent.api.websocket import router as ws_router


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例

    步骤:
    1. 创建 FastAPI 实例
    2. 配置 CORS 中间件
    3. 注册 REST API 路由
    4. 注册 WebSocket 路由
    5. 注册生命周期事件（startup/shutdown）
       - startup: 初始化日志、数据库、Skill 注册表
       - shutdown: 关闭数据库连接
    """
    app = FastAPI(
        title="Space AIAgent",
        description="航天分析平台智能助手",
        version="0.1.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(api_router)
    app.include_router(ws_router)

    # TODO: 注册生命周期事件
    # @app.on_event("startup")
    # async def startup():
    #     setup_logging(...)
    #     await get_db()
    #     SkillRegistry().discover()

    # @app.on_event("shutdown")
    # async def shutdown():
    #     db = await get_db()
    #     await db.close()

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "space_aiagent.main:app",
        host="0.0.0.0",
        port=8028,
        reload=True,
    )
