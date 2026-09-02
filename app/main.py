from __future__ import annotations

# FastAPI应用入口：装配数据库、Provider、中间件、异常处理器和业务路由。
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.bootstrap.config import Settings, get_settings
from app.bootstrap.providers import create_provider_bundle
from app.modules.handover.api.router import router as handover_router
from app.modules.inspection.api.router import router as inspection_router
from app.modules.maintenance_order.api.router import router as maintenance_router
from app.modules.operation_event.api.router import router as event_router
from app.modules.report.api.router import router as report_router
from app.shared.db import Database
from app.shared.errors import install_error_handlers
from app.shared.media.router import router as storage_transfer_router
from app.shared.platform.idempotency import IdempotencyMiddleware
from app.shared.platform.router import admin_router, jobs_router
from app.shared.security.audit.context import request_id_context, reset_request_id
from app.shared.security.authorization.repository import sync_builtin_roles
from app.shared.security.authorization.router import router as access_control_router
from app.shared.security.router import router as auth_router


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = configured
        app.state.database = Database(configured.database_url)
        app.state.providers = create_provider_bundle(configured)
        if configured.auto_create_schema:
            await app.state.database.create_all()
        if configured.auto_seed:
            async with app.state.database.session_factory() as session:
                await sync_builtin_roles(session)
                await session.commit()
        yield
        await app.state.providers.close()
        await app.state.database.dispose()

    app = FastAPI(
        title=configured.app_name,
        version=__version__,
        description="模块化单体后端；AI 结果默认是候选，正式业务动作需要确定性校验与人工授权。",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "Idempotency-Key", "Idempotency-Replayed"],
        max_age=600,
    )
    app.add_middleware(IdempotencyMiddleware)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = request_id_context(request.state.request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            reset_request_id(token)

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready", tags=["health"])
    async def health_ready(request: Request):
        try:
            async with request.app.state.database.session_factory() as session:
                await session.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})

    for router in (
        auth_router,
        access_control_router,
        jobs_router,
        admin_router,
        storage_transfer_router,
        handover_router,
        event_router,
        maintenance_router,
        inspection_router,
        report_router,
    ):
        app.include_router(router, prefix=configured.api_prefix)

    return app


app = create_app()
