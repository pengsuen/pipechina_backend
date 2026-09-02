import asyncio
from uuid import UUID

from app.bootstrap.celery_app import celery_app
from app.modules.operation_event.application.service import (
    execute_event_classification,
    execute_event_extraction,
    get_event,
    load_persisted_source,
)
from app.modules.operation_event.domain.models import ProductionEventVersion
from app.shared.platform.service import get_job
from app.shared.worker_runtime import job_user, mark_job_failed, worker_resources


async def _extract(job_id: str) -> str:
    async with worker_resources() as resources:
        async with resources.database.session_factory() as session:
            job = await get_job(session, UUID(job_id))
            try:
                source_type = str(job.config_snapshot["source_type"])
                version_id = UUID(job.config_snapshot["source_version_id"])
                text = await load_persisted_source(
                    session, source_type=source_type, source_version_id=version_id
                )
                await execute_event_extraction(
                    session,
                    job=job,
                    text=text,
                    source_type=source_type,
                    source_id=job.resource_id,
                    source_version_id=version_id,
                    user=job_user(job),
                    provider=resources.text,
                )
            except Exception as exc:
                await mark_job_failed(session, job, exc)
                raise
            return job.status


async def _classify(job_id: str) -> str:
    async with worker_resources() as resources:
        async with resources.database.session_factory() as session:
            job = await get_job(session, UUID(job_id))
            try:
                event = await get_event(session, job.resource_id)
                version_id = UUID(job.config_snapshot["event_version_id"])
                version = await session.get(ProductionEventVersion, version_id)
                if version is None:
                    raise ValueError(f"event version {version_id} does not exist")
                await execute_event_classification(
                    session,
                    job=job,
                    event=event,
                    version=version,
                    user=job_user(job),
                    provider=resources.text,
                )
            except Exception as exc:
                await mark_job_failed(session, job, exc)
                raise
            return job.status


@celery_app.task(
    name="app.modules.operation_event.extract_events",
    acks_late=True,
)
def extract_events_task(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": asyncio.run(_extract(job_id))}


@celery_app.task(
    name="app.modules.operation_event.classify_event",
    acks_late=True,
)
def classify_event_task(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": asyncio.run(_classify(job_id))}
