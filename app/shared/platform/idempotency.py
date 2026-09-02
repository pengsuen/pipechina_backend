"""HTTP写请求幂等中间件。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.shared.db import Database
from app.shared.platform.models import IdempotencyRecord
from app.shared.security.identity.models import UserAccount

# 只处理带Idempotency-Key的JSON写请求；查询和文件流不进入幂等缓存。


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """对携带Idempotency-Key的JSON写请求进行预约、冲突检测和响应重放。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        key = request.headers.get("Idempotency-Key")
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or not key:
            return await call_next(request)
        if len(key) > 200:
            return _error(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is too long")
        identity = _identity(request.headers.get("Authorization"))
        if identity is None:
            return await call_next(request)  # 认证依赖会返回正式的401。

        body = await request.body()
        operation = f"{request.method} {request.url.path}"
        digest = hashlib.sha256(
            request.method.encode()
            + b"\0"
            + request.url.path.encode()
            + b"\0"
            + request.url.query.encode()
            + b"\0"
            + body
        ).hexdigest()
        database = request.app.state.database
        record: IdempotencyRecord | None = None
        async with database.session_factory() as session:
            issuer, subject = identity
            actor_id = await session.scalar(
                select(UserAccount.id).where(
                    UserAccount.external_issuer == issuer,
                    UserAccount.external_subject == subject,
                    UserAccount.active.is_(True),
                )
            )
            if actor_id is None:
                return await call_next(request)
            record = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.actor_id == actor_id,
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
            if record is not None:
                replay = _replay_or_conflict(record, digest)
                if replay is not None:
                    return replay
            else:
                record = IdempotencyRecord(
                    actor_id=actor_id,
                    operation=operation,
                    idempotency_key=key,
                    request_digest=digest,
                )
                session.add(record)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    record = await session.scalar(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.actor_id == actor_id,
                            IdempotencyRecord.operation == operation,
                            IdempotencyRecord.idempotency_key == key,
                        )
                    )
                    assert record is not None
                    replay = _replay_or_conflict(record, digest)
                    if replay is not None:
                        return replay

        assert record is not None
        try:
            response = await call_next(request)
            response_body = b"".join(
                [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
            )
        except Exception:
            await _delete_reservation(database, record.id)
            raise

        content_type = response.headers.get("content-type", "")
        cacheable = "application/json" in content_type and len(response_body) <= 1_000_000
        if cacheable:
            try:
                content = json.loads(response_body)
            except TypeError, ValueError:
                cacheable = False
        if cacheable:
            async with database.session_factory() as session:
                saved = await session.get(IdempotencyRecord, record.id)
                if saved is not None:
                    saved.response_status = response.status_code
                    saved.response_body = {"content": content}
                    await session.commit()
        else:
            await _delete_reservation(database, record.id)

        headers = dict(response.headers)
        headers["Idempotency-Key"] = key
        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=headers,
            media_type=None,
            background=response.background,
        )


def _identity(authorization: str | None) -> tuple[str, str] | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        claims = jwt.decode(
            authorization.removeprefix("Bearer ").strip(),
            options={"verify_signature": False, "verify_aud": False},
            algorithms=["RS256", "ES256"],
        )
        issuer, subject = str(claims["iss"]), str(claims["sub"])
    except jwt.PyJWTError, KeyError, TypeError:
        return None
    # 这里只提取查找键；请求仍必须通过后续的正式签名、issuer和audience验证。
    return issuer, subject


def _replay_or_conflict(record: IdempotencyRecord, digest: str) -> Response | None:
    if record.request_digest != digest:
        return _error(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "the same Idempotency-Key was used with a different request",
        )
    if record.response_status is None or record.response_body is None:
        updated_at = record.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if updated_at <= datetime.now(UTC) - timedelta(minutes=5):
            return None
        return _error(409, "REQUEST_IN_PROGRESS", "an identical request is still in progress")
    response = JSONResponse(
        status_code=record.response_status,
        content=record.response_body.get("content"),
    )
    response.headers["Idempotency-Replayed"] = "true"
    return response


async def _delete_reservation(database: Database, record_id: UUID) -> None:
    async with database.session_factory() as session:
        row = await session.get(IdempotencyRecord, record_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})
