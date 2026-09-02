from __future__ import annotations

# 本地文件存储实现，通过签名URL提供受控的上传和下载能力。
import asyncio
import base64
import hashlib
import hmac
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from app.ports.storage import ObjectMetadata, UploadGrant
from app.shared.errors import AppError


class LocalFilesystemStorageProvider:
    """通过签名地址提供浏览器上传和下载的私有本地存储。"""

    name = "local_filesystem"

    def __init__(
        self,
        *,
        root: Path,
        public_base_url: str,
        signing_secret: str,
        upload_path: str = "/api/v1/storage/uploads",
        download_path: str = "/api/v1/storage/downloads",
    ) -> None:
        if len(signing_secret) < 32:
            raise ValueError("LOCAL_STORAGE_SIGNING_SECRET must contain at least 32 characters")
        self.root = root.resolve()
        self.public_base_url = public_base_url.rstrip("/")
        self.signing_secret = signing_secret.encode("utf-8")
        self.upload_path = upload_path
        self.download_path = download_path
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str) -> Path:
        if not object_key or object_key.startswith("/"):
            raise AppError("INVALID_OBJECT_KEY", "object key is invalid", 400)
        candidate = (self.root / object_key).resolve()
        if candidate == self.root or self.root not in candidate.parents:
            raise AppError("INVALID_OBJECT_KEY", "object key escapes storage root", 400)
        return candidate

    @staticmethod
    def _encode_key(object_key: str) -> str:
        return base64.urlsafe_b64encode(object_key.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_key(encoded_key: str) -> str:
        try:
            padding = "=" * (-len(encoded_key) % 4)
            return base64.urlsafe_b64decode(encoded_key + padding).decode()
        except (ValueError, UnicodeDecodeError) as exc:
            raise AppError("INVALID_STORAGE_SIGNATURE", "invalid object-key token", 403) from exc

    def _sign(self, payload: str) -> str:
        return hmac.new(self.signing_secret, payload.encode(), hashlib.sha256).hexdigest()

    def _verify(self, payload: str, signature: str, expires: int) -> None:
        if expires < int(time.time()):
            raise AppError("STORAGE_URL_EXPIRED", "signed storage URL has expired", 403)
        if not hmac.compare_digest(self._sign(payload), signature):
            raise AppError("INVALID_STORAGE_SIGNATURE", "invalid storage signature", 403)

    async def create_upload(
        self, *, object_key: str, mime_type: str, size_bytes: int
    ) -> UploadGrant:
        self._path(object_key)
        encoded_key = self._encode_key(object_key)
        expires = int(time.time()) + 900
        payload = f"upload\n{object_key}\n{mime_type}\n{size_bytes}\n{expires}"
        query = urlencode(
            {
                "expires": expires,
                "size_bytes": size_bytes,
                "mime_type": mime_type,
                "signature": self._sign(payload),
            }
        )
        return UploadGrant(
            object_key=object_key,
            upload_url=f"{self.public_base_url}{self.upload_path}/{encoded_key}?{query}",
            headers={"Content-Type": mime_type},
        )

    async def head(self, object_key: str) -> ObjectMetadata:
        path = self._path(object_key)
        if not path.is_file():
            raise AppError("OBJECT_NOT_FOUND", "stored object does not exist", 404)
        size_bytes, checksum = await asyncio.gather(
            asyncio.to_thread(lambda: path.stat().st_size),
            asyncio.to_thread(self._sha256, path),
        )
        mime_path = path.with_suffix(path.suffix + ".mime")
        mime_type = (
            await asyncio.to_thread(mime_path.read_text, encoding="utf-8")
            if mime_path.is_file()
            else "application/octet-stream"
        )
        return ObjectMetadata(
            object_key=object_key,
            size_bytes=size_bytes,
            mime_type=mime_type,
            checksum=checksum,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def signed_download_url(self, object_key: str, expires_in: int = 900) -> str:
        self._path(object_key)
        encoded_key = self._encode_key(object_key)
        expires = int(time.time()) + expires_in
        payload = f"download\n{object_key}\n{expires}"
        query = urlencode({"expires": expires, "signature": self._sign(payload)})
        return f"{self.public_base_url}{self.download_path}/{encoded_key}?{query}"

    async def put_bytes(self, object_key: str, data: bytes, mime_type: str) -> None:
        path = self._path(object_key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
        await asyncio.to_thread(temporary.write_bytes, data)
        await asyncio.to_thread(os.replace, temporary, path)
        await asyncio.to_thread(
            path.with_suffix(path.suffix + ".mime").write_text,
            mime_type,
            encoding="utf-8",
        )

    async def delete(self, object_key: str) -> None:
        path = self._path(object_key)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        await asyncio.to_thread(path.with_suffix(path.suffix + ".mime").unlink, missing_ok=True)

    @asynccontextmanager
    async def materialize(self, object_key: str) -> AsyncIterator[Path]:
        path = self._path(object_key)
        if not path.is_file():
            raise AppError("OBJECT_NOT_FOUND", "stored object does not exist", 404)
        yield path

    async def accept_signed_upload(
        self,
        *,
        encoded_key: str,
        expires: int,
        size_bytes: int,
        mime_type: str,
        signature: str,
        chunks: AsyncIterator[bytes],
    ) -> ObjectMetadata:
        object_key = self._decode_key(encoded_key)
        payload = f"upload\n{object_key}\n{mime_type}\n{size_bytes}\n{expires}"
        self._verify(payload, signature, expires)
        path = self._path(object_key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
        written = 0
        handle = await asyncio.to_thread(temporary.open, "xb")
        try:
            async for chunk in chunks:
                written += len(chunk)
                if written > size_bytes:
                    raise AppError("UPLOAD_SIZE_MISMATCH", "upload exceeds declared size", 409)
                await asyncio.to_thread(handle.write, chunk)
            await asyncio.to_thread(handle.flush)
            await asyncio.to_thread(os.fsync, handle.fileno())
        except Exception:
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            raise
        await asyncio.to_thread(handle.close)
        if written != size_bytes:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            raise AppError(
                "UPLOAD_SIZE_MISMATCH",
                "uploaded object size differs from declared size",
                409,
                {"expected": size_bytes, "actual": written},
            )
        await asyncio.to_thread(os.replace, temporary, path)
        await asyncio.to_thread(
            path.with_suffix(path.suffix + ".mime").write_text,
            mime_type,
            encoding="utf-8",
        )
        return await self.head(object_key)

    async def authorize_signed_download(
        self,
        *,
        encoded_key: str,
        expires: int,
        signature: str,
    ) -> tuple[Path, ObjectMetadata]:
        object_key = self._decode_key(encoded_key)
        self._verify(f"download\n{object_key}\n{expires}", signature, expires)
        return self._path(object_key), await self.head(object_key)
