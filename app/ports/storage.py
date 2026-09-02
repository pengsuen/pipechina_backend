from __future__ import annotations

# 存储端口及上传凭证模型，统一本地文件和对象存储的调用方式。
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class UploadGrant(BaseModel):
    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    expires_in: int = 900


class ObjectMetadata(BaseModel):
    object_key: str
    size_bytes: int
    mime_type: str
    checksum: str | None = None


class StorageProvider(Protocol):
    name: str

    async def create_upload(
        self, *, object_key: str, mime_type: str, size_bytes: int
    ) -> UploadGrant: ...

    async def head(self, object_key: str) -> ObjectMetadata: ...

    async def signed_download_url(self, object_key: str, expires_in: int = 900) -> str: ...

    async def put_bytes(self, object_key: str, data: bytes, mime_type: str) -> None: ...

    async def delete(self, object_key: str) -> None: ...

    def materialize(self, object_key: str) -> AbstractAsyncContextManager[Path]: ...


@runtime_checkable
class DirectTransferStorage(Protocol):
    """本地文件存储可选实现的签名HTTP传输接口。"""

    async def accept_signed_upload(
        self,
        *,
        encoded_key: str,
        expires: int,
        size_bytes: int,
        mime_type: str,
        signature: str,
        chunks: AsyncIterator[bytes],
    ) -> ObjectMetadata: ...

    async def authorize_signed_download(
        self,
        *,
        encoded_key: str,
        expires: int,
        signature: str,
    ) -> tuple[Path, ObjectMetadata]: ...
