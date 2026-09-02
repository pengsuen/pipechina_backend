from __future__ import annotations

# FastAPI认证依赖：校验JWT签名、标准声明和IdP身份映射。
import asyncio
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError

from app.shared.db import SessionDep
from app.shared.errors import AppError
from app.shared.security.authentication.schemas import AuthenticatedIdentity, VerifiedToken
from app.shared.security.identity.repository import resolve_identity

bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=16)
def _get_jwk_client(url: str, cache_seconds: int, timeout_seconds: float) -> PyJWKClient:
    """创建并缓存用于获取 JWT 签名密钥的客户端。"""
    return PyJWKClient(
        url,
        cache_keys=True,
        lifespan=cache_seconds,
        timeout=timeout_seconds,
    )


async def _verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> VerifiedToken:
    """校验 bearer token，并将声明转换为已验证令牌。"""
    if credentials is None:
        raise AppError("AUTHENTICATION_REQUIRED", "bearer token is required", 401)
    settings = request.app.state.settings  # 读取应用级认证配置
    try:
        signing_key = await asyncio.to_thread(
            _get_jwk_client(
                settings.jwt_jwks_url,
                settings.jwt_jwks_cache_seconds,
                settings.jwt_jwks_timeout_seconds,
            ).get_signing_key_from_jwt,
            credentials.credentials,
        )  # 在线程中获取匹配的签名密钥

        claims: dict[str, Any] = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )  # 校验签名及必需声明
        issuer = str(claims["iss"])
        subject = str(claims["sub"])
        org_claim = claims.get("org")
        org_id = UUID(str(org_claim)) if org_claim is not None else None
        return VerifiedToken(
            issuer=issuer,
            subject=subject,
            username=str(claims.get("preferred_username", subject)),
            display_name=str(claims.get("name", subject)),
            organization_unit_id=org_id,
            claims=claims,
        )
    except (
        InvalidTokenError,
        PyJWKClientError,
        KeyError,
        ValueError,
        TypeError,
    ) as exc:
        raise AppError(
            "INVALID_TOKEN",
            "bearer token is invalid or expired",
            401,
        ) from exc


async def get_authenticated_identity(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedIdentity:
    """验证请求令牌并解析对应的内部用户身份。"""
    token = await _verify_token(request, credentials)  # 先完成令牌校验
    return await resolve_identity(session, token)


AuthenticatedIdentityDep = Annotated[
    AuthenticatedIdentity,
    Depends(get_authenticated_identity),
]
