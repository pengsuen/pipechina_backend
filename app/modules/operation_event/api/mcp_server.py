"""MCP Streamable HTTP adapter for the existing read-only investigation queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.bootstrap.config import Settings
from app.modules.operation_event.application.investigation_tools import _read_business_tool
from app.modules.operation_event.domain.models import ProductionEvent
from app.shared.db import Database
from app.shared.errors import AppError, NotFoundError
from app.shared.security.authentication.dependencies import verify_bearer_token
from app.shared.security.authorization.dependencies import require_data_scope
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.repository import load_current_user
from app.shared.security.identity.repository import resolve_identity


def _bearer(headers: dict[str, str]) -> str:
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError("AUTHENTICATION_REQUIRED", "bearer token is required", 401)
    return token.strip()


class AuthenticatedMCP:
    """Reject unauthenticated protocol requests before the MCP dispatcher sees them."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        try:
            await verify_bearer_token(self.settings, _bearer(headers))
        except AppError as exc:
            response = JSONResponse(
                {"code": exc.code, "message": exc.message},
                status_code=exc.status_code,
                headers={"WWW-Authenticate": 'Bearer realm="pipechina-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_mcp_server(
    settings: Settings, database: Callable[[], Database]
) -> tuple[MCPServer, ASGIApp]:
    """Publish only the three existing business searches as MCP tools."""
    server = MCPServer(name="pipechina-investigation", version="1.0.0")

    async def search(action: str, event_id: UUID, query: str, ctx: Context) -> list[dict[str, Any]]:
        if not query.strip() or len(query) > 100:
            raise ValueError("query must contain 1 to 100 characters")
        request = ctx.request_context.request
        if request is None:
            raise AppError("AUTHENTICATION_REQUIRED", "HTTP request is required", 401)
        headers = dict(request.headers)
        token = await verify_bearer_token(settings, _bearer(headers))
        async with database().session_factory() as session:
            identity = await resolve_identity(session, token)
            user = await load_current_user(session, identity)
            event = await session.get(ProductionEvent, event_id)
            if event is None:
                raise NotFoundError("event", event_id)
            require_data_scope(
                user,
                event.organization_unit_id,
                Permissions.EVENT_READ,
                owner_id=event.created_by,
            )
            return await _read_business_tool(session, user, event, action, query.strip())

    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

    @server.tool(
        description="Search confirmed historical events visible to the caller.",
        annotations=read_only,
    )
    async def search_events(event_id: UUID, query: str, ctx: Context) -> list[dict[str, Any]]:
        return await search("search_events", event_id, query, ctx)

    @server.tool(
        description="Search maintenance orders visible to the caller.", annotations=read_only
    )
    async def search_work_orders(event_id: UUID, query: str, ctx: Context) -> list[dict[str, Any]]:
        return await search("search_work_orders", event_id, query, ctx)

    @server.tool(
        description="Search handover records visible to the caller.", annotations=read_only
    )
    async def search_handover(event_id: UUID, query: str, ctx: Context) -> list[dict[str, Any]]:
        return await search("search_handover", event_id, query, ctx)

    transport = TransportSecuritySettings(
        allowed_hosts=list(settings.mcp_allowed_hosts),
        allowed_origins=list(settings.mcp_allowed_origins),
    )
    asgi = server.streamable_http_app(
        streamable_http_path="/",
        transport_security=transport,
        stateless_http=True,
    )
    return server, AuthenticatedMCP(asgi, settings)
