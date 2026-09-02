from __future__ import annotations

# 为Celery任务创建独立数据库会话、Provider和最小权限执行身份。
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.config import Settings, get_settings
from app.bootstrap.providers import create_provider_bundle
from app.ports.speech import SpeechToTextProvider
from app.ports.storage import StorageProvider
from app.ports.text import TextLLMProvider
from app.ports.vision import VisionProvider
from app.shared.db import Database
from app.shared.platform.models import AIJob
from app.shared.platform.service import update_job
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser


class WorkerResources:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_url)
        self.providers = create_provider_bundle(settings)

    @property
    def text(self) -> TextLLMProvider:
        return self.providers.text

    @property
    def asr(self) -> SpeechToTextProvider:
        return self.providers.asr

    @property
    def vision(self) -> VisionProvider:
        return self.providers.vision

    @property
    def storage(self) -> StorageProvider:
        return self.providers.storage

    async def close(self) -> None:
        await self.providers.close()
        await self.database.dispose()


@asynccontextmanager
async def worker_resources(settings: Settings | None = None) -> AsyncIterator[WorkerResources]:
    resources = WorkerResources(settings or get_settings())
    try:
        yield resources
    finally:
        await resources.close()


def job_user(job: AIJob) -> CurrentUser:
    permission_by_job = {
        "audio_transcription": Permissions.HANDOVER_PROCESS,
        "handover_summary": Permissions.HANDOVER_PROCESS,
        "event_extraction": Permissions.EVENT_EXTRACT,
        "event_classification": Permissions.EVENT_CLASSIFY,
        "inspection_image_analysis": Permissions.INSPECTION_ANALYZE,
        "report_generation": Permissions.REPORT_GENERATE,
        "report_export": Permissions.REPORT_EXPORT,
    }
    permission = permission_by_job.get(job.job_type)
    if permission is None:
        raise ValueError(f"no worker permission mapping for job type {job.job_type}")
    return CurrentUser(
        user_id=job.requested_by,
        subject=f"worker:{job.requested_by}",
        username="celery-worker",
        display_name="异步任务执行器",
        organization_unit_id=job.organization_unit_id,
        roles={"system_worker"},
        permissions={permission},
        organization_scope={job.organization_unit_id},
    )


async def mark_job_failed(session: AsyncSession, job: AIJob, exc: Exception) -> None:
    """在Celery重投前保存经过脱敏的失败状态。"""
    if job.status in {"succeeded", "failed", "cancelled"}:
        return
    await update_job(
        session,
        job,
        status="failed",
        progress=job.progress,
        message="worker execution failed",
        error_code=type(exc).__name__,
        error_detail=str(exc)[:2000],
    )
    await session.commit()
