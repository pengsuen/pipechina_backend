from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.modules.operation_event.application.service import (
    classify_event,
    extract_events,
    get_event,
    merge_events,
    split_event,
    update_event,
)
from app.modules.operation_event.domain.models import ProductionEvent, ProductionEventVersion
from app.modules.operation_event.domain.schemas import (
    EventExtractionCreate,
    EventUpdate,
    EventView,
    MergeEventsInput,
    RejectInput,
    SplitEventInput,
)
from app.shared.db import SessionDep
from app.shared.errors import ConflictError
from app.shared.platform.service import add_audit
from app.shared.security.authorization.dependencies import (
    data_scope_clause,
    require_data_scope,
    require_permission,
)
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser

router = APIRouter(tags=["operation-event"])


@router.post("/event-extractions", status_code=202)
async def post_event_extraction(
    payload: EventExtractionCreate,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_EXTRACT))],
) -> dict:
    job_id, events = await extract_events(
        session,
        payload=payload,
        user=user,
        provider=request.app.state.providers.text,
        inline=request.app.state.settings.run_tasks_inline,
    )
    return {"job_id": str(job_id), "event_ids": [str(event.id) for event in events]}


@router.get("/events", response_model=list[EventView])
async def list_events(
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_READ))],
) -> list[EventView]:
    statement = select(ProductionEvent).order_by(ProductionEvent.created_at.desc()).limit(100)
    statement = statement.where(
        data_scope_clause(
            user,
            Permissions.EVENT_READ,
            ProductionEvent.organization_unit_id,
            owner_column=ProductionEvent.created_by,
        )
    )
    rows = await session.scalars(statement)
    return [EventView.model_validate(row) for row in rows]


@router.get("/events/{event_id}", response_model=EventView)
async def read_event(
    event_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_READ))],
) -> EventView:
    event = await get_event(session, event_id)
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_READ,
        owner_id=event.created_by,
    )
    return EventView.model_validate(event)


@router.get("/events/{event_id}/versions")
async def read_event_versions(
    event_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_READ))],
) -> list[dict]:
    event = await get_event(session, event_id)
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_READ,
        owner_id=event.created_by,
    )
    rows = await session.scalars(
        select(ProductionEventVersion)
        .where(ProductionEventVersion.event_id == event.id)
        .order_by(ProductionEventVersion.version)
    )
    return [
        {
            "id": str(row.id),
            "version": row.version,
            "source": row.source,
            "description": row.description,
            "structured_data": row.structured_data,
        }
        for row in rows
    ]


@router.put("/events/{event_id}")
async def put_event(
    event_id: UUID,
    payload: EventUpdate,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_EDIT))],
) -> dict:
    version = await update_event(session, await get_event(session, event_id), payload, user)
    return {"version_id": str(version.id), "version": version.version}


@router.post("/events/{event_id}:confirm")
async def confirm_event(
    event_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_REVIEW))],
) -> dict:
    event = await get_event(session, event_id)
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_REVIEW,
        owner_id=event.created_by,
    )
    if event.business_status != "candidate":
        raise ConflictError("only candidate event can be confirmed")
    event.business_status = "confirmed"
    event.confirmed_by = user.user_id
    await add_audit(
        session,
        user=user,
        action="production_event.confirm",
        resource_type="production_event",
        resource_id=event.id,
    )
    await session.commit()
    return {"id": str(event.id), "business_status": event.business_status}


@router.post("/events/{event_id}:reject")
async def reject_event(
    event_id: UUID,
    payload: RejectInput,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_REVIEW))],
) -> dict:
    event = await get_event(session, event_id)
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_REVIEW,
        owner_id=event.created_by,
    )
    if event.business_status != "candidate":
        raise ConflictError("only candidate event can be rejected")
    event.business_status = "rejected"
    await add_audit(
        session,
        user=user,
        action="production_event.reject",
        resource_type="production_event",
        resource_id=event.id,
        reason=payload.reason,
    )
    await session.commit()
    return {"id": str(event.id), "business_status": event.business_status}


@router.post("/events/{event_id}:reextract", status_code=202)
async def reextract_event(
    event_id: UUID,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_TRANSFORM))],
) -> dict:
    event = await get_event(session, event_id)
    require_data_scope(
        user,
        event.organization_unit_id,
        Permissions.EVENT_TRANSFORM,
        owner_id=event.created_by,
    )
    version = await session.get(ProductionEventVersion, event.current_version_id)
    if version is None:
        raise ConflictError("event current version is missing")
    payload = EventExtractionCreate(source_type="raw_text", raw_text=version.description)
    job_id, events = await extract_events(
        session,
        payload=payload,
        user=user,
        provider=request.app.state.providers.text,
        inline=request.app.state.settings.run_tasks_inline,
    )
    return {"job_id": str(job_id), "event_ids": [str(item.id) for item in events]}


@router.post("/events:merge", status_code=201)
async def post_merge(
    payload: MergeEventsInput,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_TRANSFORM))],
) -> dict:
    event = await merge_events(session, payload, user)
    return {"id": str(event.id)}


@router.post("/events/{event_id}:split", status_code=201)
async def post_split(
    event_id: UUID,
    payload: SplitEventInput,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_TRANSFORM))],
) -> dict:
    events = await split_event(session, await get_event(session, event_id), payload, user)
    return {"event_ids": [str(item.id) for item in events]}


@router.post("/events/{event_id}:classify", status_code=202)
async def post_classify(
    event_id: UUID,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.EVENT_CLASSIFY))],
) -> dict:
    job_id, workflow, assessment = await classify_event(
        session,
        event=await get_event(session, event_id),
        user=user,
        provider=request.app.state.providers.text,
        inline=request.app.state.settings.run_tasks_inline,
    )
    return {
        "job_id": str(job_id),
        "workflow_id": str(workflow.id) if workflow else None,
        "assessment_id": str(assessment.id) if assessment else None,
    }
