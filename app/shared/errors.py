from dataclasses import dataclass

# 定义统一业务异常及FastAPI异常响应，避免各路由重复拼装错误格式。
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__(
            code="RESOURCE_NOT_FOUND",
            message=f"{resource} not found",
            status_code=404,
            details={"id": str(resource_id)},
        )


class ConflictError(AppError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__("STATE_CONFLICT", message, 409, details or None)


class PermissionDeniedError(AppError):
    def __init__(self, permission: str) -> None:
        super().__init__(
            "PERMISSION_DENIED",
            "current user does not have the required permission",
            403,
            {"permission": permission},
        )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        body = ErrorBody(
            code=exc.code,
            message=exc.message,
            request_id=getattr(request.state, "request_id", None),
            details=exc.details,
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())
