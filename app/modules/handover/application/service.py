from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.config import Settings
from app.modules.handover.domain.models import (
    AudioRecord,
    AudioTranscriptSegment,
    AudioTranscriptVersion,
    HandoverSummaryVersion,
    ManualOperationRecord,
    ManualOperationRecordVersion,
)
from app.modules.handover.domain.schemas import (
    AudioCreateResponse,
    AudioRecordCreate,
    AudioRecordView,
    ManualOperationCreate,
    ManualOperationUpdate,
    SummaryUpdate,
    TranscriptUpdate,
)
from app.modules.handover.infrastructure.repository import HandoverRepository
from app.ports.models import HandoverSummary, MediaRef
from app.ports.speech import SpeechToTextProvider
from app.ports.storage import StorageProvider
from app.ports.text import TextLLMProvider
from app.shared.errors import AppError, ConflictError, NotFoundError
from app.shared.media.integrity import normalize_sha256
from app.shared.media.names import safe_object_filename
from app.shared.platform.models import AIJob, UploadSession
from app.shared.platform.runtime import (
    asr_provider_for_job,
    build_runtime_snapshot,
    business_payload,
    render_user_prompt,
    system_prompt,
    text_provider_for_job,
)
from app.shared.platform.service import add_audit, create_job, update_job
from app.shared.security.authorization.dependencies import require_data_scope
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser


async def get_audio(session: AsyncSession, audio_id: UUID) -> AudioRecord:
    row = await HandoverRepository(session).get_audio(audio_id)
    if row is None or row.deleted:
        raise NotFoundError("audio_record", audio_id)
    return row


async def create_audio(
    session: AsyncSession,
    *,
    payload: AudioRecordCreate,
    user: CurrentUser,
    storage: StorageProvider,
    settings: Settings,
) -> AudioCreateResponse:
    org_id = payload.organization_unit_id or user.organization_unit_id
    require_data_scope(
        user,
        org_id,
        Permissions.HANDOVER_CREATE,
        owner_id=user.user_id,
    )
    if payload.size_bytes > settings.max_audio_bytes:
        raise AppError("FILE_TOO_LARGE", "audio exceeds configured size limit", 413)
    audio = AudioRecord(
        organization_unit_id=org_id,
        shift_date=payload.shift_date,
        shift_code=payload.shift_code,
        filename=payload.filename,
        object_key="pending",
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        upload_status="pending",
        business_status="draft",
        created_by=user.user_id,
    )
    session.add(audio)
    await session.flush()
    object_key = f"handover/{org_id}/{audio.id}/{safe_object_filename(payload.filename)}"
    audio.object_key = object_key
    upload = UploadSession(
        resource_type="audio_record",
        resource_id=audio.id,
        organization_unit_id=org_id,
        object_key=object_key,
        filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        client_sha256=payload.sha256,
        status="pending",
    )
    session.add(upload)
    grant = await storage.create_upload(
        object_key=object_key, mime_type=payload.mime_type, size_bytes=payload.size_bytes
    )
    upload.expires_at = datetime.now(UTC) + timedelta(seconds=grant.expires_in)
    await add_audit(
        session,
        user=user,
        action="audio_record.create",
        resource_type="audio_record",
        resource_id=audio.id,
        after={"shift_date": str(payload.shift_date), "shift_code": payload.shift_code},
    )
    await session.commit()
    return AudioCreateResponse(
        audio=AudioRecordView.model_validate(audio),
        upload_session_id=upload.id,
        object_key=grant.object_key,
        upload_url=grant.upload_url,
        upload_headers=grant.headers,
    )


async def complete_upload(
    session: AsyncSession,
    *,
    audio: AudioRecord,
    user: CurrentUser,
    storage: StorageProvider,
    server_sha256: str | None,
) -> None:
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_PROCESS,
        owner_id=audio.created_by,
    )
    metadata = await storage.head(audio.object_key)
    if metadata.size_bytes != audio.size_bytes:
        raise ConflictError(
            "uploaded object size differs from declared size",
            expected=audio.size_bytes,
            actual=metadata.size_bytes,
        )
    upload = await session.scalar(
        select(UploadSession).where(
            UploadSession.resource_type == "audio_record",
            UploadSession.resource_id == audio.id,
        )
    )
    if upload is None:
        raise NotFoundError("upload_session", audio.id)
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
        raise ConflictError(
            "uploaded object MIME type differs from declared type",
            expected=upload.mime_type,
            actual=metadata.mime_type,
        )
    trusted_digest = normalize_sha256(metadata.checksum)
    reported_digest = normalize_sha256(server_sha256)
    if trusted_digest and reported_digest and trusted_digest != reported_digest:
        raise ConflictError("reported checksum differs from object storage checksum")
    if upload.client_sha256:
        if trusted_digest is None:
            raise ConflictError("object storage did not provide a trusted SHA-256 checksum")
        if upload.client_sha256.lower() != trusted_digest:
            raise ConflictError("uploaded object checksum differs from declared checksum")
    upload.status = "verified"
    upload.server_sha256 = trusted_digest
    audio.upload_status = "verified"
    await add_audit(
        session,
        user=user,
        action="audio_record.upload_verified",
        resource_type="audio_record",
        resource_id=audio.id,
    )


async def transcribe_audio(
    session: AsyncSession,
    *,
    audio: AudioRecord,
    user: CurrentUser,
    provider: SpeechToTextProvider,
    inline: bool,
) -> UUID:
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_PROCESS,
        owner_id=audio.created_by,
    )
    if audio.upload_status != "verified":
        raise ConflictError("audio upload must be verified before transcription")
    job = await create_job(
        session,
        user=user,
        job_type="audio_transcription",
        resource_type="audio_record",
        resource_id=audio.id,
        task_name="app.modules.handover.transcribe_audio",
        queue="audio",
        config_snapshot=await build_runtime_snapshot(
            session, job_type="audio_transcription", provider=provider
        ),
        enqueue=not inline,
    )
    if not inline:
        await session.commit()
        return job.id
    await execute_transcription(
        session,
        job=job,
        audio=audio,
        user=user,
        provider=provider,
    )
    return job.id


async def execute_transcription(
    session: AsyncSession,
    *,
    job: AIJob,
    audio: AudioRecord,
    user: CurrentUser,
    provider: SpeechToTextProvider,
) -> None:
    """Execute one persisted transcription job; safe to redeliver after success."""
    if job.status in {"succeeded", "failed", "cancelled"}:
        return
    if job.cancel_requested:
        await update_job(session, job, status="cancelled", progress=job.progress)
        await session.commit()
        return
    await update_job(session, job, status="running", progress=10, message="calling ASR")
    provider = asr_provider_for_job(session, job, provider)
    hotword_config = business_payload(job.config_snapshot, "asr.hotwords")
    result = await provider.transcribe(
        MediaRef(
            object_key=audio.object_key,
            mime_type=audio.mime_type,
            size_bytes=audio.size_bytes,
            filename=audio.filename,
        ),
        hotwords=[str(item) for item in hotword_config.get("hotwords", [])],
        language=str(hotword_config.get("language", "zh")),
    )
    version_no = (
        await session.scalar(
            select(func.max(AudioTranscriptVersion.version)).where(
                AudioTranscriptVersion.audio_record_id == audio.id
            )
        )
        or 0
    ) + 1
    version = AudioTranscriptVersion(
        audio_record_id=audio.id,
        version=version_no,
        source="ai",
        full_text=result.full_text,
        language=result.language,
        provider_request_id=result.provider_request_id,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    for segment in result.segments:
        session.add(
            AudioTranscriptSegment(
                transcript_version_id=version.id,
                segment_index=segment.index,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                speaker_label=segment.speaker_label,
                confidence=segment.confidence,
            )
        )
    audio.duration_ms = result.duration_ms
    audio.language = result.language
    audio.current_transcript_version_id = version.id
    audio.business_status = "transcribed"
    await update_job(session, job, status="succeeded", progress=100, message="transcript persisted")
    await session.commit()


async def update_transcript(
    session: AsyncSession,
    *,
    audio: AudioRecord,
    payload: TranscriptUpdate,
    user: CurrentUser,
) -> AudioTranscriptVersion:
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_EDIT,
        owner_id=audio.created_by,
    )
    version_no = (
        await session.scalar(
            select(func.max(AudioTranscriptVersion.version)).where(
                AudioTranscriptVersion.audio_record_id == audio.id
            )
        )
        or 0
    ) + 1
    version = AudioTranscriptVersion(
        audio_record_id=audio.id,
        version=version_no,
        source="manual",
        full_text=payload.full_text,
        language=audio.language,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    for index, segment in enumerate(payload.segments):
        session.add(
            AudioTranscriptSegment(
                transcript_version_id=version.id,
                segment_index=index,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                speaker_label=segment.speaker_label,
            )
        )
    audio.current_transcript_version_id = version.id
    audio.business_status = "transcribed"
    await session.commit()
    return version


async def summarize_audio(
    session: AsyncSession,
    *,
    audio: AudioRecord,
    user: CurrentUser,
    provider: TextLLMProvider,
    inline: bool,
) -> UUID:
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_PROCESS,
        owner_id=audio.created_by,
    )
    if audio.current_transcript_version_id is None:
        raise ConflictError("transcript is required before summary")
    transcript = await session.get(AudioTranscriptVersion, audio.current_transcript_version_id)
    if transcript is None:
        raise ConflictError("current transcript version is missing")
    job = await create_job(
        session,
        user=user,
        job_type="handover_summary",
        resource_type="audio_record",
        resource_id=audio.id,
        task_name="app.modules.handover.summarize_audio",
        queue="ai_text",
        config_snapshot=await build_runtime_snapshot(
            session,
            job_type="handover_summary",
            provider=provider,
            base={"transcript_version_id": str(transcript.id)},
        ),
        enqueue=not inline,
    )
    if not inline:
        await session.commit()
        return job.id
    await execute_summary(
        session,
        job=job,
        audio=audio,
        transcript=transcript,
        user=user,
        provider=provider,
    )
    return job.id


async def execute_summary(
    session: AsyncSession,
    *,
    job: AIJob,
    audio: AudioRecord,
    transcript: AudioTranscriptVersion,
    user: CurrentUser,
    provider: TextLLMProvider,
) -> None:
    """Execute one persisted summary job; safe to redeliver after success."""
    if job.status in {"succeeded", "failed", "cancelled"}:
        return
    if job.cancel_requested:
        await update_job(session, job, status="cancelled", progress=job.progress)
        await session.commit()
        return
    await update_job(session, job, status="running", progress=20, message="generating summary")
    provider = text_provider_for_job(session, job, provider)
    summary = await provider.generate_structured(
        operation="handover_summary",
        system_prompt=system_prompt(job.config_snapshot),
        user_prompt=render_user_prompt(job.config_snapshot, transcript.full_text),
        response_model=HandoverSummary,
    )
    await _save_summary(
        session,
        audio=audio,
        transcript_version_id=transcript.id,
        content=summary.model_dump(),
        source="ai",
        user=user,
    )
    await update_job(session, job, status="succeeded", progress=100, message="summary persisted")
    await session.commit()


async def _save_summary(
    session: AsyncSession,
    *,
    audio: AudioRecord,
    transcript_version_id: UUID,
    content: dict,
    source: str,
    user: CurrentUser,
) -> HandoverSummaryVersion:
    version_no = (
        await session.scalar(
            select(func.max(HandoverSummaryVersion.version)).where(
                HandoverSummaryVersion.audio_record_id == audio.id
            )
        )
        or 0
    ) + 1
    summary = HandoverSummaryVersion(
        audio_record_id=audio.id,
        transcript_version_id=transcript_version_id,
        version=version_no,
        source=source,
        content=content,
        source_segment_ids=[],
        created_by=user.user_id,
    )
    session.add(summary)
    await session.flush()
    audio.current_summary_version_id = summary.id
    audio.business_status = "summary_ready"
    return summary


async def update_summary(
    session: AsyncSession,
    *,
    audio: AudioRecord,
    payload: SummaryUpdate,
    user: CurrentUser,
) -> HandoverSummaryVersion:
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_EDIT,
        owner_id=audio.created_by,
    )
    transcript_id = payload.transcript_version_id or audio.current_transcript_version_id
    if transcript_id is None:
        raise ConflictError("summary must reference a transcript version")
    summary = await _save_summary(
        session,
        audio=audio,
        transcript_version_id=transcript_id,
        content=payload.content,
        source="manual",
        user=user,
    )
    await session.commit()
    return summary


async def create_manual_record(
    session: AsyncSession, payload: ManualOperationCreate, user: CurrentUser
) -> ManualOperationRecord:
    require_data_scope(
        user,
        user.organization_unit_id,
        Permissions.HANDOVER_CREATE,
        owner_id=user.user_id,
    )
    record = ManualOperationRecord(
        organization_unit_id=user.organization_unit_id,
        occurred_at=payload.occurred_at,
        record_type=payload.record_type,
        business_status="confirmed",
        created_by=user.user_id,
    )
    session.add(record)
    await session.flush()
    version = ManualOperationRecordVersion(
        record_id=record.id,
        version=1,
        content=payload.content,
        structured_data=payload.structured_data,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    record.current_version_id = version.id
    await session.commit()
    return record


async def update_manual_record(
    session: AsyncSession,
    record_id: UUID,
    payload: ManualOperationUpdate,
    user: CurrentUser,
) -> ManualOperationRecord:
    record = await session.get(ManualOperationRecord, record_id)
    if record is None:
        raise NotFoundError("manual_operation_record", record_id)
    require_data_scope(
        user,
        record.organization_unit_id,
        Permissions.HANDOVER_EDIT,
        owner_id=record.created_by,
    )
    version_no = (
        await session.scalar(
            select(func.max(ManualOperationRecordVersion.version)).where(
                ManualOperationRecordVersion.record_id == record.id
            )
        )
        or 0
    ) + 1
    version = ManualOperationRecordVersion(
        record_id=record.id,
        version=version_no,
        content=payload.content,
        structured_data=payload.structured_data,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    record.current_version_id = version.id
    await session.commit()
    return record
