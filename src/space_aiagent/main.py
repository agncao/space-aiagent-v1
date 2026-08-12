"""
space-aiagent FastAPI 应用入口

启动方式:
    # 开发环境
    python -m space_aiagent.main

    # 或使用 uvicorn
    uvicorn space_aiagent.main:app --reload --host 0.0.0.0 --port 8028
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from space_aiagent.api.routes import router
from space_aiagent.infrastructure.config import get_settings
from space_aiagent.infrastructure.logging import setup_logging
from space_aiagent.infrastructure.observability import (
    setup_telemetry,
    shutdown_telemetry,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动期初始化可观测性，关闭期 flush 探针。"""
    settings = get_settings()
    setup_telemetry(settings)
    try:
        yield
    finally:
        shutdown_telemetry()


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例

    步骤:
    1. 创建 FastAPI 实例
    2. 配置 CORS 中间件
    3. 注册 REST API 路由
    4. 注册 SSE/REST 路由
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
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # 启用 FastAPI OTel 自动 instrumentation（仅当 observability.enabled=true）
    # 排除 chat，让 workflow 手动 span 成为 trace root。
    if settings.observability.enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/v2/space/health,/api/v2/space/chat")

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
