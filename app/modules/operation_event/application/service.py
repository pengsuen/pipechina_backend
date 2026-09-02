from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.handover.domain.models import (
    AudioRecord,
    AudioTranscriptVersion,
    ManualOperationRecord,
    ManualOperationRecordVersion,
)
from app.modules.maintenance_order.domain.models import AbnormalityAssessment
from app.modules.operation_event.domain.models import (
    EventSourceLink,
    ProductionEvent,
    ProductionEventVersion,
)
from app.modules.operation_event.domain.schemas import (
    EventExtractionCreate,
    EventUpdate,
    MergeEventsInput,
    SplitEventInput,
)
from app.modules.operation_event.infrastructure.repository import OperationEventRepository
from app.ports.models import AssessmentCandidate, EventCandidate, EventCandidateList
from app.ports.text import TextLLMProvider
from app.shared.errors import ConflictError, NotFoundError
from app.shared.platform.models import AIJob, WorkflowRun
from app.shared.platform.runtime import (
    build_runtime_snapshot,
    business_payload,
    render_user_prompt,
    system_prompt,
    text_provider_for_job,
)
from app.shared.platform.service import create_job, update_job
from app.shared.security.authorization.dependencies import require_data_scope
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser


async def get_event(session: AsyncSession, event_id: UUID) -> ProductionEvent:
    event = await OperationEventRepository(session).get_event(event_id)
    if event is None:
        raise NotFoundError("production_event", event_id)
    return event


async def _source_text(
    session: AsyncSession, payload: EventExtractionCreate, user: CurrentUser
) -> tuple[str, UUID, UUID, str]:
    if payload.source_type == "raw_text":
        if not payload.raw_text:
            raise ConflictError("raw_text is required for raw_text source")
        record = ManualOperationRecord(
            organization_unit_id=user.organization_unit_id,
            occurred_at=datetime.now(UTC).isoformat(),
            record_type="event_extraction_input",
            business_status="confirmed",
            created_by=user.user_id,
        )
        session.add(record)
        await session.flush()
        raw_version = ManualOperationRecordVersion(
            record_id=record.id,
            version=1,
            content=payload.raw_text,
            structured_data={"source_type": "raw_text"},
            created_by=user.user_id,
        )
        session.add(raw_version)
        await session.flush()
        record.current_version_id = raw_version.id
        return payload.raw_text, record.id, raw_version.id, "manual_operation"
    if payload.source_type == "audio_transcript":
        transcript_version = (
            await session.get(AudioTranscriptVersion, payload.source_version_id)
            if payload.source_version_id
            else None
        )
        if transcript_version is None:
            raise NotFoundError("audio_transcript_version", payload.source_version_id)
        audio = await session.get(AudioRecord, transcript_version.audio_record_id)
        if audio is None:
            raise NotFoundError("audio_record", transcript_version.audio_record_id)
        require_data_scope(
            user,
            audio.organization_unit_id,
            Permissions.EVENT_EXTRACT,
            owner_id=audio.created_by,
        )
        return (
            transcript_version.full_text,
            transcript_version.audio_record_id,
            transcript_version.id,
            payload.source_type,
        )
    manual_record = await session.get(ManualOperationRecord, payload.source_id)
    if manual_record is None:
        raise NotFoundError("manual_operation_record", payload.source_id)
    require_data_scope(
        user,
        manual_record.organization_unit_id,
        Permissions.EVENT_EXTRACT,
        owner_id=manual_record.created_by,
    )
    version_id = payload.source_version_id or manual_record.current_version_id
    manual_version = await session.get(ManualOperationRecordVersion, version_id)
    if manual_version is None:
        raise NotFoundError("manual_operation_record_version", version_id)
    return manual_version.content, manual_record.id, manual_version.id, payload.source_type


async def load_persisted_source(
    session: AsyncSession, *, source_type: str, source_version_id: UUID
) -> str:
    if source_type == "audio_transcript":
        transcript_version = await session.get(AudioTranscriptVersion, source_version_id)
        if transcript_version is None:
            raise NotFoundError("audio_transcript_version", source_version_id)
        return transcript_version.full_text
    manual_version = await session.get(ManualOperationRecordVersion, source_version_id)
    if manual_version is None:
        raise NotFoundError("manual_operation_record_version", source_version_id)
    return manual_version.content


async def _persist_candidate(
    session: AsyncSession,
    *,
    candidate: EventCandidate,
    user: CurrentUser,
    source_type: str,
    source_id: UUID | None,
    source_version_id: UUID | None,
) -> ProductionEvent:
    event = ProductionEvent(
        organization_unit_id=user.organization_unit_id,
        title=candidate.title,
        event_type=candidate.event_type,
        severity=candidate.severity,
        occurred_at=None,
        business_status="candidate",
        created_by=user.user_id,
    )
    session.add(event)
    await session.flush()
    version = ProductionEventVersion(
        event_id=event.id,
        version=1,
        source="ai",
        description=candidate.description,
        structured_data={"occurred_at_text": candidate.occurred_at_text},
        confidence=candidate.confidence,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    event.current_version_id = version.id
    if source_id:
        session.add(
            EventSourceLink(
                event_id=event.id,
                source_type=source_type,
                source_id=source_id,
                source_version_id=source_version_id,
                evidence_text=candidate.evidence_text,
                evidence_locator={},
            )
        )
    return event


async def extract_events(
    session: AsyncSession,
    *,
    payload: EventExtractionCreate,
    user: CurrentUser,
    provider: TextLLMProvider,
    inline: bool,
) -> tuple[UUID, list[ProductionEvent]]:
    text, source_id, version_id, source_type = await _source_text(session, payload, user)
    job = await create_job(
        session,
        user=user,
        job_type="event_extraction",
        resource_type=source_type,
        resource_id=source_id,
        task_name="app.modules.operation_event.extract_events",
        queue="ai_text",
        config_snapshot=await build_runtime_snapshot(
            session,
            job_type="event_extraction",
            provider=provider,
            base={"source_type": source_type, "source_version_id": str(version_id)},
        ),
        enqueue=not inline,
    )
    if not inline:
        await session.commit()
        return job.id, []
    events = await execute_event_extraction(
        session,
        job=job,
        text=text,
        source_type=source_type,
        source_id=source_id,
        source_version_id=version_id,
        user=user,
        provider=provider,
    )
    return job.id, events


async def execute_event_extraction(
    session: AsyncSession,
    *,
    job: AIJob,
    text: str,
    source_type: str,
    source_id: UUID,
    source_version_id: UUID,
    user: CurrentUser,
    provider: TextLLMProvider,
) -> list[ProductionEvent]:
    if job.status in {"succeeded", "failed", "cancelled"}:
        return []
    if job.cancel_requested:
        await update_job(session, job, status="cancelled", progress=job.progress)
        await session.commit()
        return []
    await update_job(session, job, status="running", progress=20, message="extracting events")
    provider = text_provider_for_job(session, job, provider)
    result = await provider.generate_structured(
        operation="event_extraction",
        system_prompt=system_prompt(job.config_snapshot),
        user_prompt=render_user_prompt(job.config_snapshot, text),
        response_model=EventCandidateList,
    )
    candidates = result.events
    events = [
        await _persist_candidate(
            session,
            candidate=candidate,
            user=user,
            source_type=source_type,
            source_id=source_id,
            source_version_id=source_version_id,
        )
        for candidate in candidates
    ]
    await update_job(session, job, status="succeeded", progress=100, message="events persisted")
    await session.commit()
    return events


async def update_event(
    session: AsyncSession, event: ProductionEvent, payload: EventUpdate, user: CurrentUser
) -> ProductionEventVersion:
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_EDIT,
        owner_id=event.created_by,
    )
    if event.business_status in {"rejected", "superseded"}:
        raise ConflictError("rejected or superseded event cannot be edited")
    version_no = (
        await session.scalar(
            select(func.max(ProductionEventVersion.version)).where(
                ProductionEventVersion.event_id == event.id
            )
        )
        or 0
    ) + 1
    version = ProductionEventVersion(
        event_id=event.id,
        version=version_no,
        source="manual",
        description=payload.description,
        structured_data=payload.structured_data,
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    event.title = payload.title
    event.event_type = payload.event_type
    event.severity = payload.severity
    event.occurred_at = payload.occurred_at
    event.current_version_id = version.id
    await session.commit()
    return version


async def classify_event(
    session: AsyncSession,
    *,
    event: ProductionEvent,
    user: CurrentUser,
    provider: TextLLMProvider,
    inline: bool,
) -> tuple[UUID, WorkflowRun | None, AbnormalityAssessment | None]:
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_CLASSIFY,
        owner_id=event.created_by,
    )
    if event.business_status != "confirmed":
        raise ConflictError("event must be confirmed before classification")
    version = await session.get(ProductionEventVersion, event.current_version_id)
    if version is None:
        raise ConflictError("event has no current version")
    job = await create_job(
        session,
        user=user,
        job_type="event_classification",
        resource_type="production_event",
        resource_id=event.id,
        task_name="app.modules.operation_event.classify_event",
        queue="maintenance",
        config_snapshot=await build_runtime_snapshot(
            session,
            job_type="event_classification",
            provider=provider,
            base={"event_version_id": str(version.id)},
        ),
        enqueue=not inline,
    )
    if not inline:
        await session.commit()
        return job.id, None, None
    workflow, assessment = await execute_event_classification(
        session,
        job=job,
        event=event,
        version=version,
        user=user,
        provider=provider,
    )
    return job.id, workflow, assessment


async def execute_event_classification(
    session: AsyncSession,
    *,
    job: AIJob,
    event: ProductionEvent,
    version: ProductionEventVersion,
    user: CurrentUser,
    provider: TextLLMProvider,
) -> tuple[WorkflowRun | None, AbnormalityAssessment | None]:
    if job.status in {"succeeded", "failed", "cancelled"}:
        return None, None
    if job.cancel_requested:
        await update_job(session, job, status="cancelled", progress=job.progress)
        await session.commit()
        return None, None
    await update_job(session, job, status="running", progress=30, message="classifying event")
    provider = text_provider_for_job(session, job, provider)
    rules = business_payload(job.config_snapshot, "event.classification")
    input_text = version.description
    if rules:
        input_text += "\n\n已发布分类规则：" + json.dumps(rules, ensure_ascii=False)
    result = await provider.generate_structured(
        operation="event_classification",
        system_prompt=system_prompt(job.config_snapshot),
        user_prompt=render_user_prompt(job.config_snapshot, input_text),
        response_model=AssessmentCandidate,
    )
    assessment = AbnormalityAssessment(
        event_id=event.id,
        organization_unit_id=event.organization_unit_id,
        category=result.category,
        risk_level=result.risk_level,
        rationale=result.rationale,
        recommended_action=result.recommended_action,
        review_status="pending",
    )
    session.add(assessment)
    await session.flush()
    workflow = WorkflowRun(
        workflow_type="maintenance_order",
        resource_type="production_event",
        resource_id=event.id,
        organization_unit_id=event.organization_unit_id,
        status="awaiting_review",
        current_node="human_review",
        state_snapshot={
            "event_id": str(event.id),
            "assessment_id": str(assessment.id),
            "category": assessment.category,
            "risk_level": assessment.risk_level,
            "node": "human_review",
        },
        thread_id=f"maintenance:{event.id}:{assessment.id}",
    )
    session.add(workflow)
    await update_job(
        session, job, status="succeeded", progress=100, message="awaiting human review"
    )
    await session.commit()
    return workflow, assessment


async def merge_events(
    session: AsyncSession, payload: MergeEventsInput, user: CurrentUser
) -> ProductionEvent:
    events = [await get_event(session, item) for item in payload.event_ids]
    for event in events:
        require_data_scope(
            user,
            event.organization_unit_id,
            Permissions.EVENT_TRANSFORM,
            owner_id=event.created_by,
        )
        if event.business_status not in {"candidate", "confirmed"}:
            raise ConflictError("only candidate or confirmed events can be merged")
    organization_ids = {event.organization_unit_id for event in events}
    if len(organization_ids) != 1:
        raise ConflictError("events from different organizations cannot be merged")
    merged = ProductionEvent(
        organization_unit_id=events[0].organization_unit_id,
        title=payload.title,
        event_type=events[0].event_type,
        severity=max(
            (event.severity for event in events), key=["low", "medium", "high", "critical"].index
        ),
        occurred_at=min((event.occurred_at for event in events if event.occurred_at), default=None),
        business_status="candidate",
        created_by=user.user_id,
    )
    session.add(merged)
    await session.flush()
    version = ProductionEventVersion(
        event_id=merged.id,
        version=1,
        source="merge",
        description="\n".join(event.title for event in events),
        structured_data={"merged_event_ids": [str(event.id) for event in events]},
        created_by=user.user_id,
    )
    session.add(version)
    await session.flush()
    merged.current_version_id = version.id
    for event in events:
        event.business_status = "superseded"
    await session.commit()
    return merged


async def split_event(
    session: AsyncSession, event: ProductionEvent, payload: SplitEventInput, user: CurrentUser
) -> list[ProductionEvent]:
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_TRANSFORM,
        owner_id=event.created_by,
    )
    if event.business_status not in {"candidate", "confirmed"}:
        raise ConflictError("event cannot be split in current status")
    results = []
    for item in payload.items:
        candidate = EventCandidate(
            title=item.title,
            event_type=item.event_type,
            occurred_at_text=None,
            description=item.description,
            severity=item.severity,
            confidence=1.0,
            evidence_text=f"split from {event.id}",
        )
        results.append(
            await _persist_candidate(
                session,
                candidate=candidate,
                user=user,
                source_type="production_event",
                source_id=event.id,
                source_version_id=event.current_version_id,
            )
        )
    event.business_status = "superseded"
    await session.commit()
    return results
