from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.modules.handover.application.service import (
    complete_upload,
    create_audio,
    create_manual_record,
    get_audio,
    summarize_audio,
    transcribe_audio,
    update_manual_record,
    update_summary,
    update_transcript,
)
from app.modules.handover.domain.models import (
    AudioTranscriptSegment,
    AudioTranscriptVersion,
    HandoverSummaryVersion,
)
from app.modules.handover.domain.schemas import (
    AudioCreateResponse,
    AudioRecordCreate,
    AudioRecordView,
    ManualOperationCreate,
    ManualOperationUpdate,
    SummaryUpdate,
    TranscriptUpdate,
    UploadComplete,
)
from app.shared.db import SessionDep
from app.shared.errors import ConflictError
from app.shared.platform.service import add_audit
from app.shared.security.authorization.dependencies import require_data_scope, require_permission
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser

router = APIRouter(tags=["handover"])


@router.post("/audio-records", response_model=AudioCreateResponse, status_code=201)
async def post_audio_record(
    payload: AudioRecordCreate,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_CREATE))],
) -> AudioCreateResponse:
    return await create_audio(
        session,
        payload=payload,
        user=user,
        storage=request.app.state.providers.storage,
        settings=request.app.state.settings,
    )


@router.post("/audio-records/{audio_id}/uploads:complete", status_code=202)
async def post_audio_upload_complete(
    audio_id: UUID,
    payload: UploadComplete,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_PROCESS))],
) -> dict:
    audio = await get_audio(session, audio_id)
    await complete_upload(
        session,
        audio=audio,
        user=user,
        storage=request.app.state.providers.storage,
        server_sha256=payload.server_sha256,
    )
    await session.commit()
    return {"id": str(audio.id), "upload_status": audio.upload_status}


@router.post("/audio-records/{audio_id}:transcribe", status_code=202)
async def post_transcribe(
    audio_id: UUID,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_PROCESS))],
) -> dict:
    audio = await get_audio(session, audio_id)
    job_id = await transcribe_audio(
        session,
        audio=audio,
        user=user,
        provider=request.app.state.providers.asr,
        inline=request.app.state.settings.run_tasks_inline,
    )
    return {"job_id": str(job_id)}


@router.get("/audio-records/{audio_id}", response_model=AudioRecordView)
async def read_audio(
    audio_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_READ))],
) -> AudioRecordView:
    audio = await get_audio(session, audio_id)
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_READ,
        owner_id=audio.created_by,
    )
    return AudioRecordView.model_validate(audio)


@router.get("/audio-records/{audio_id}/segments")
async def read_segments(
    audio_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_READ))],
) -> list[dict]:
    audio = await get_audio(session, audio_id)
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_READ,
        owner_id=audio.created_by,
    )
    if audio.current_transcript_version_id is None:
        return []
    rows = await session.scalars(
        select(AudioTranscriptSegment)
        .where(AudioTranscriptSegment.transcript_version_id == audio.current_transcript_version_id)
        .order_by(AudioTranscriptSegment.segment_index)
    )
    return [
        {
            "id": str(row.id),
            "index": row.segment_index,
            "start_ms": row.start_ms,
            "end_ms": row.end_ms,
            "text": row.text,
            "speaker_label": row.speaker_label,
        }
        for row in rows
    ]


@router.get("/audio-records/{audio_id}/versions")
async def read_versions(
    audio_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_READ))],
) -> dict:
    audio = await get_audio(session, audio_id)
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_READ,
        owner_id=audio.created_by,
    )
    transcripts = await session.scalars(
        select(AudioTranscriptVersion)
        .where(AudioTranscriptVersion.audio_record_id == audio.id)
        .order_by(AudioTranscriptVersion.version)
    )
    summaries = await session.scalars(
        select(HandoverSummaryVersion)
        .where(HandoverSummaryVersion.audio_record_id == audio.id)
        .order_by(HandoverSummaryVersion.version)
    )
    return {
        "transcripts": [
            {"id": str(row.id), "version": row.version, "source": row.source} for row in transcripts
        ],
        "summaries": [
            {"id": str(row.id), "version": row.version, "source": row.source} for row in summaries
        ],
    }


@router.put("/audio-records/{audio_id}/transcript")
async def put_transcript(
    audio_id: UUID,
    payload: TranscriptUpdate,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_EDIT))],
) -> dict:
    version = await update_transcript(
        session, audio=await get_audio(session, audio_id), payload=payload, user=user
    )
    return {"version_id": str(version.id), "version": version.version}


@router.put("/audio-records/{audio_id}/summary")
async def put_summary(
    audio_id: UUID,
    payload: SummaryUpdate,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_EDIT))],
) -> dict:
    version = await update_summary(
        session, audio=await get_audio(session, audio_id), payload=payload, user=user
    )
    return {"version_id": str(version.id), "version": version.version}


@router.post("/audio-records/{audio_id}:summarize", status_code=202)
async def post_summarize(
    audio_id: UUID,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_PROCESS))],
) -> dict:
    job_id = await summarize_audio(
        session,
        audio=await get_audio(session, audio_id),
        user=user,
        provider=request.app.state.providers.text,
        inline=request.app.state.settings.run_tasks_inline,
    )
    return {"job_id": str(job_id)}


@router.post("/audio-records/{audio_id}:confirm")
async def post_confirm(
    audio_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_CONFIRM))],
) -> dict:
    audio = await get_audio(session, audio_id)
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_CONFIRM,
        owner_id=audio.created_by,
    )
    if audio.current_transcript_version_id is None or audio.current_summary_version_id is None:
        raise ConflictError("transcript and summary are required before confirmation")
    audio.business_status = "confirmed"
    audio.confirmed_by = user.user_id
    await add_audit(
        session,
        user=user,
        action="audio_record.confirm",
        resource_type="audio_record",
        resource_id=audio.id,
    )
    await session.commit()
    return {"id": str(audio.id), "business_status": audio.business_status}


@router.delete("/audio-records/{audio_id}", status_code=204)
async def delete_audio(
    audio_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_DELETE))],
) -> Response:
    audio = await get_audio(session, audio_id)
    require_data_scope(
        user,
        audio.organization_unit_id,
        Permissions.HANDOVER_DELETE,
        owner_id=audio.created_by,
    )
    if audio.business_status == "confirmed":
        raise ConflictError("confirmed audio record is protected from direct deletion")
    audio.deleted = True
    await add_audit(
        session,
        user=user,
        action="audio_record.soft_delete",
        resource_type="audio_record",
        resource_id=audio.id,
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/manual-operation-records", status_code=201)
async def post_manual_record(
    payload: ManualOperationCreate,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_CREATE))],
) -> dict:
    row = await create_manual_record(session, payload, user)
    return {"id": str(row.id), "current_version_id": str(row.current_version_id)}


@router.put("/manual-operation-records/{record_id}")
async def put_manual_record(
    record_id: UUID,
    payload: ManualOperationUpdate,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.HANDOVER_EDIT))],
) -> dict:
    row = await update_manual_record(session, record_id, payload, user)
    return {"id": str(row.id), "current_version_id": str(row.current_version_id)}
