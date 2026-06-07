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
from space_aiagent.infrastructure.config import get_settings
from space_aiagent.infrastructure.logging import setup_logging


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例

    步骤:
    1. 创建 FastAPI 实例
    2. 配置 CORS 中间件
    3. 注册 REST API 路由
    4. 注册 WebSocket 路由
    5. 注册生命周期事件（startup/shutdown）
    """
    settings = get_settings()

    # 初始化日志
    setup_logging(
        level=settings.logging.level,
        fmt=settings.logging.format,
        console=settings.logging.console,
        file_enabled=settings.logging.file_enabled,
        file_dir=settings.logging.file_dir,
        file_max_bytes=settings.logging.file_max_bytes,
        file_backup_count=settings.logging.file_backup_count,
        loggers=settings.logging.loggers,
    )

    app = FastAPI(
        title="Space AIAgent",
        description="航天分析平台智能助手",
        version=settings.app_version,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(api_router)
    app.include_router(ws_router)

    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "space_aiagent.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=True,
    )
