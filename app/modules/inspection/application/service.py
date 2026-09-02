from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inspection.domain.models import (
    InspectionFinding,
    InspectionFindingLink,
    InspectionImage,
    InspectionRecord,
)
from app.modules.inspection.domain.schemas import InspectionCreate, InspectionImageCreate
from app.modules.inspection.infrastructure.repository import InspectionRepository
from app.modules.operation_event.application.service import classify_event
from app.modules.operation_event.domain.models import ProductionEvent, ProductionEventVersion
from app.ports.models import MediaRef
from app.ports.storage import StorageProvider, UploadGrant
from app.ports.text import TextLLMProvider
from app.ports.vision import VisionProvider
from app.shared.errors import AppError, ConflictError, NotFoundError
from app.shared.media.integrity import normalize_sha256
from app.shared.media.names import safe_object_filename
from app.shared.platform.models import AIJob, UploadSession
from app.shared.platform.runtime import (
    build_runtime_snapshot,
    render_user_prompt,
    vision_provider_for_job,
)
from app.shared.platform.service import add_audit, create_job, update_job
from app.shared.security.authorization.dependencies import require_data_scope
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser


async def get_inspection(session: AsyncSession, inspection_id: UUID) -> InspectionRecord:
    row = await InspectionRepository(session).get_inspection(inspection_id)
    if row is None:
        raise NotFoundError("inspection", inspection_id)
    return row


async def create_inspection(
    session: AsyncSession, payload: InspectionCreate, user: CurrentUser
) -> InspectionRecord:
    require_data_scope(
        user,
        user.organization_unit_id,
        Permissions.INSPECTION_CREATE,
        owner_id=user.user_id,
    )
    row = InspectionRecord(
        organization_unit_id=user.organization_unit_id,
        station_name=payload.station_name,
        pipeline_name=payload.pipeline_name,
        equipment_name=payload.equipment_name,
        inspected_at=payload.inspected_at,
        notes=payload.notes,
        status="draft",
        created_by=user.user_id,
    )
    session.add(row)
    await session.flush()
    await add_audit(
        session,
        user=user,
        action="inspection.create",
        resource_type="inspection",
        resource_id=row.id,
    )
    await session.commit()
    return row


async def create_image(
    session: AsyncSession,
    *,
    inspection: InspectionRecord,
    payload: InspectionImageCreate,
    user: CurrentUser,
    storage: StorageProvider,
    max_bytes: int,
    allowed_types: tuple[str, ...],
) -> tuple[InspectionImage, UploadGrant]:
    require_data_scope(
        user,
        inspection.organization_unit_id,
        Permissions.INSPECTION_UPLOAD,
        owner_id=inspection.created_by,
    )
    if payload.size_bytes > max_bytes:
        raise AppError("FILE_TOO_LARGE", "image exceeds configured size limit", 413)
    if payload.mime_type not in allowed_types:
        raise AppError("UNSUPPORTED_MEDIA_TYPE", "image type is not allowed", 415)
    image = InspectionImage(
        inspection_id=inspection.id,
        object_key="pending",
        filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        status="pending",
    )
    session.add(image)
    await session.flush()
    image.object_key = (
        f"inspections/{inspection.id}/{image.id}/{safe_object_filename(payload.filename)}"
    )
    upload = UploadSession(
        resource_type="inspection_image",
        resource_id=image.id,
        organization_unit_id=inspection.organization_unit_id,
        object_key=image.object_key,
        filename=image.filename,
        mime_type=image.mime_type,
        size_bytes=image.size_bytes,
        client_sha256=payload.sha256,
        status="pending",
    )
    session.add(upload)
    grant = await storage.create_upload(
        object_key=image.object_key, mime_type=image.mime_type, size_bytes=image.size_bytes
    )
    upload.expires_at = datetime.now(UTC) + timedelta(seconds=grant.expires_in)
    await session.commit()
    return image, grant


async def complete_image(
    session: AsyncSession,
    *,
    inspection: InspectionRecord,
    image: InspectionImage,
    user: CurrentUser,
    storage: StorageProvider,
    checksum: str | None,
) -> None:
    require_data_scope(
        user,
        inspection.organization_unit_id,
        Permissions.INSPECTION_UPLOAD,
        owner_id=inspection.created_by,
    )
    metadata = await storage.head(image.object_key)
    if metadata.size_bytes != image.size_bytes:
        raise ConflictError("uploaded image size mismatch")
    upload = await session.scalar(
        select(UploadSession).where(
            UploadSession.resource_type == "inspection_image",
            UploadSession.resource_id == image.id,
        )
    )
    if upload is None:
        raise NotFoundError("upload_session", image.id)
    expires_at = upload.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            upload.status = "expired"
            raise ConflictError("upload session has expired")
    if upload.status != "pending":
        raise ConflictError("upload session is not pending", status=upload.status)
    if metadata.mime_type != upload.mime_type:
        raise ConflictError("uploaded image MIME type differs from declared type")
    trusted_digest = normalize_sha256(metadata.checksum)
    reported_digest = normalize_sha256(checksum)
    if trusted_digest and reported_digest and trusted_digest != reported_digest:
        raise ConflictError("reported checksum differs from object storage checksum")
    if upload.client_sha256:
        if trusted_digest is None or upload.client_sha256.lower() != trusted_digest:
            raise ConflictError("uploaded image checksum differs from declared checksum")
    image.status = "verified"
    upload.status = "verified"
    upload.server_sha256 = trusted_digest
    await session.commit()


async def analyze_inspection(
    session: AsyncSession,
    *,
    inspection: InspectionRecord,
    user: CurrentUser,
    provider: VisionProvider,
    inline: bool,
) -> tuple[UUID, list[InspectionFinding]]:
    require_data_scope(
        user,
        inspection.organization_unit_id,
        Permissions.INSPECTION_ANALYZE,
        owner_id=inspection.created_by,
    )
    images = list(
        await session.scalars(
            select(InspectionImage).where(
                InspectionImage.inspection_id == inspection.id,
                InspectionImage.status == "verified",
                InspectionImage.deleted.is_(False),
            )
        )
    )
    if not images:
        raise ConflictError("at least one verified image is required")
    job = await create_job(
        session,
        user=user,
        job_type="inspection_image_analysis",
        resource_type="inspection",
        resource_id=inspection.id,
        task_name="app.modules.inspection.analyze_inspection",
        queue="vision",
        config_snapshot=await build_runtime_snapshot(
            session,
            job_type="inspection_image_analysis",
            provider=provider,
            base={"image_count": len(images)},
        ),
        enqueue=not inline,
    )
    if not inline:
        await session.commit()
        return job.id, []
    findings = await execute_inspection_analysis(
        session,
        job=job,
        inspection=inspection,
        images=images,
        user=user,
        provider=provider,
    )
    return job.id, findings


async def execute_inspection_analysis(
    session: AsyncSession,
    *,
    job: AIJob,
    inspection: InspectionRecord,
    images: list[InspectionImage],
    user: CurrentUser,
    provider: VisionProvider,
) -> list[InspectionFinding]:
    if job.status in {"succeeded", "failed", "cancelled"}:
        return []
    if job.cancel_requested:
        await update_job(session, job, status="cancelled", progress=job.progress)
        await session.commit()
        return []
    await update_job(session, job, status="running", progress=10, message="analyzing images")
    provider = vision_provider_for_job(session, job, provider)
    findings: list[InspectionFinding] = []
    context = " / ".join(
        item
        for item in [inspection.station_name, inspection.pipeline_name, inspection.equipment_name]
        if item
    )
    for image in images:
        image.status = "analyzing"
        candidates = await provider.inspect(
            MediaRef(
                object_key=image.object_key,
                mime_type=image.mime_type,
                size_bytes=image.size_bytes,
                filename=image.filename,
            ),
            context=render_user_prompt(job.config_snapshot, context),
        )
        for candidate in candidates:
            row = InspectionFinding(
                inspection_id=inspection.id,
                image_id=image.id,
                title=candidate.title,
                category=candidate.category,
                severity=candidate.severity,
                description=candidate.description,
                evidence=candidate.evidence,
                confidence=candidate.confidence,
                review_status="pending",
            )
            session.add(row)
            findings.append(row)
        image.status = "analyzed"
    inspection.status = "review_pending"
    await update_job(session, job, status="succeeded", progress=100, message="findings persisted")
    await session.commit()
    return findings


async def get_finding(session: AsyncSession, finding_id: UUID) -> InspectionFinding:
    finding = await InspectionRepository(session).get_finding(finding_id)
    if finding is None:
        raise NotFoundError("inspection_finding", finding_id)
    return finding


async def link_finding_to_event(
    session: AsyncSession, finding: InspectionFinding, event_id: UUID, user: CurrentUser
) -> InspectionFindingLink:
    inspection = await get_inspection(session, finding.inspection_id)
    require_data_scope(
        user,
        inspection.organization_unit_id,
        Permissions.INSPECTION_LINK,
        owner_id=inspection.created_by,
    )
    if finding.review_status != "confirmed":
        raise ConflictError("only confirmed finding can be linked")
    event = await session.get(ProductionEvent, event_id)
    if event is None:
        raise NotFoundError("production_event", event_id)
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.INSPECTION_LINK,
        owner_id=event.created_by,
    )
    link = InspectionFindingLink(
        finding_id=finding.id,
        target_type="production_event",
        target_id=event.id,
        active=True,
        created_by=user.user_id,
    )
    session.add(link)
    await session.commit()
    return link


async def start_finding_workflow(
    session: AsyncSession,
    *,
    finding: InspectionFinding,
    user: CurrentUser,
    provider: TextLLMProvider,
    inline: bool,
) -> tuple[UUID, UUID | None]:
    inspection = await get_inspection(session, finding.inspection_id)
    require_data_scope(
        user,
        inspection.organization_unit_id,
        Permissions.INSPECTION_WORKFLOW,
        owner_id=inspection.created_by,
    )
    if finding.review_status != "confirmed":
        raise ConflictError("finding must be confirmed before workflow")
    event = ProductionEvent(
        organization_unit_id=inspection.organization_unit_id,
        title=finding.title,
        event_type=finding.category,
        severity=finding.severity,
        occurred_at=inspection.inspected_at,
        business_status="confirmed",
        created_by=user.user_id,
        confirmed_by=user.user_id,
    )
    session.add(event)
    await session.flush()
    version = ProductionEventVersion(
        event_id=event.id,
        version=1,
        source="inspection",
        description=finding.description,
        structured_data={"finding_id": str(finding.id), "evidence": finding.evidence},
        confidence=finding.confidence,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    event.current_version_id = version.id
    link = InspectionFindingLink(
        finding_id=finding.id,
        target_type="production_event",
        target_id=event.id,
        active=True,
        created_by=user.user_id,
    )
    session.add(link)
    job_id, workflow, _ = await classify_event(
        session, event=event, user=user, provider=provider, inline=inline
    )
    return job_id, workflow.id if workflow else None
