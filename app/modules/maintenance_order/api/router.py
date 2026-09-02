import csv
import io
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.modules.maintenance_order.application.service import (
    create_attachment,
    get_work_order,
    get_workflow,
    review_workflow,
    transition_work_order,
)
from app.modules.maintenance_order.domain.models import WorkOrder, WorkOrderTransition
from app.modules.maintenance_order.domain.schemas import (
    AttachmentCreate,
    DispatchInput,
    WorkOrderReviewInput,
    WorkOrderTransitionInput,
    WorkOrderView,
)
from app.modules.operation_event.domain.models import ProductionEvent
from app.shared.db import SessionDep
from app.shared.platform.schemas import ReviewInput
from app.shared.security.authorization.dependencies import (
    data_scope_clause,
    require_data_scope,
    require_permission,
)
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser

router = APIRouter(tags=["maintenance-order"])


@router.get("/workflows/{workflow_id}")
async def read_workflow(
    workflow_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_READ))],
) -> dict:
    row = await get_workflow(session, workflow_id)
    event = await session.get(ProductionEvent, row.resource_id)
    require_data_scope(
        user,
        row.organization_unit_id,
        Permissions.MAINTENANCE_READ,
        owner_id=event.created_by if event else None,
    )
    return {
        "id": str(row.id),
        "workflow_type": row.workflow_type,
        "status": row.status,
        "current_node": row.current_node,
        "state": row.state_snapshot,
    }


@router.post("/workflows/{workflow_id}:review")
async def post_workflow_review(
    workflow_id: UUID,
    payload: ReviewInput,
    session: SessionDep,
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.MAINTENANCE_REVIEW)),
    ],
) -> dict:
    order = await review_workflow(
        session,
        workflow=await get_workflow(session, workflow_id),
        approved=payload.approved,
        reason=payload.reason,
        user=user,
    )
    return {"approved": payload.approved, "work_order_id": str(order.id) if order else None}


@router.get("/work-orders", response_model=list[WorkOrderView])
async def list_work_orders(
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_READ))],
) -> list[WorkOrderView]:
    statement = select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(100)
    statement = statement.where(
        data_scope_clause(
            user,
            Permissions.MAINTENANCE_READ,
            WorkOrder.organization_unit_id,
            owner_column=WorkOrder.created_by,
            assignee_column=WorkOrder.assignee_id,
        )
    )
    rows = await session.scalars(statement)
    return [WorkOrderView.model_validate(row) for row in rows]


@router.get("/work-orders/{order_id}", response_model=WorkOrderView)
async def read_work_order(
    order_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_READ))],
) -> WorkOrderView:
    order = await get_work_order(session, order_id)
    require_data_scope(
        user,
        order.organization_unit_id,
        Permissions.MAINTENANCE_READ,
        owner_id=order.created_by,
        assignee_id=order.assignee_id,
    )
    return WorkOrderView.model_validate(order)


@router.get("/work-orders/{order_id}/timeline")
async def read_timeline(
    order_id: UUID,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_READ))],
    format: Literal["json", "csv"] = Query(default="json"),
):
    order = await get_work_order(session, order_id)
    require_data_scope(
        user,
        order.organization_unit_id,
        Permissions.MAINTENANCE_READ,
        owner_id=order.created_by,
        assignee_id=order.assignee_id,
    )
    rows = list(
        await session.scalars(
            select(WorkOrderTransition)
            .where(WorkOrderTransition.work_order_id == order.id)
            .order_by(WorkOrderTransition.occurred_at)
        )
    )
    data = [
        {
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor_id": str(row.actor_id),
            "reason": row.reason,
            "occurred_at": row.occurred_at.isoformat(),
            "version_after": row.version_after,
        }
        for row in rows
    ]
    if format == "json":
        return data
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(data[0]) if data else ["from_status"])
    writer.writeheader()
    writer.writerows(data)
    return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8")


async def _transition(
    order_id: UUID,
    target: str,
    payload: WorkOrderTransitionInput,
    session: SessionDep,
    user: CurrentUser,
    permission: str,
) -> WorkOrderView:
    order = await transition_work_order(
        session,
        order=await get_work_order(session, order_id),
        target=target,
        reason=payload.reason,
        expected_version=payload.expected_version,
        user=user,
        permission=permission,
    )
    return WorkOrderView.model_validate(order)


@router.post("/work-orders/{order_id}:submit-review", response_model=WorkOrderView)
async def submit_review(
    order_id: UUID,
    payload: WorkOrderTransitionInput,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_WRITE))],
) -> WorkOrderView:
    return await _transition(
        order_id, "pending_review", payload, session, user, Permissions.MAINTENANCE_WRITE
    )


@router.post("/work-orders/{order_id}:review", response_model=WorkOrderView)
async def review_order(
    order_id: UUID,
    payload: WorkOrderReviewInput,
    session: SessionDep,
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.MAINTENANCE_APPROVE)),
    ],
) -> WorkOrderView:
    target = "approved" if payload.approved else "draft"
    order = await transition_work_order(
        session,
        order=await get_work_order(session, order_id),
        target=target,
        reason=payload.reason,
        expected_version=payload.expected_version,
        user=user,
        permission=Permissions.MAINTENANCE_APPROVE,
    )
    return WorkOrderView.model_validate(order)


@router.post("/work-orders/{order_id}:dispatch", response_model=WorkOrderView)
async def dispatch_order(
    order_id: UUID,
    payload: DispatchInput,
    session: SessionDep,
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.MAINTENANCE_DISPATCH)),
    ],
) -> WorkOrderView:
    order = await transition_work_order(
        session,
        order=await get_work_order(session, order_id),
        target="dispatched",
        reason=payload.reason,
        expected_version=payload.expected_version,
        user=user,
        permission=Permissions.MAINTENANCE_DISPATCH,
        assignee_id=payload.assignee_id,
        due_at=payload.due_at,
    )
    return WorkOrderView.model_validate(order)


@router.post("/work-orders/{order_id}:start", response_model=WorkOrderView)
async def start_order(
    order_id: UUID,
    payload: WorkOrderTransitionInput,
    session: SessionDep,
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.MAINTENANCE_EXECUTE)),
    ],
) -> WorkOrderView:
    return await _transition(
        order_id, "in_progress", payload, session, user, Permissions.MAINTENANCE_EXECUTE
    )


@router.post("/work-orders/{order_id}:resolve", response_model=WorkOrderView)
async def resolve_order(
    order_id: UUID,
    payload: WorkOrderTransitionInput,
    session: SessionDep,
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.MAINTENANCE_EXECUTE)),
    ],
) -> WorkOrderView:
    return await _transition(
        order_id, "resolved", payload, session, user, Permissions.MAINTENANCE_EXECUTE
    )


@router.post("/work-orders/{order_id}:close", response_model=WorkOrderView)
async def close_order(
    order_id: UUID,
    payload: WorkOrderTransitionInput,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_CLOSE))],
) -> WorkOrderView:
    return await _transition(
        order_id, "closed", payload, session, user, Permissions.MAINTENANCE_CLOSE
    )


@router.post("/work-orders/{order_id}:cancel", response_model=WorkOrderView)
async def cancel_order(
    order_id: UUID,
    payload: WorkOrderTransitionInput,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(require_permission(Permissions.MAINTENANCE_CLOSE))],
) -> WorkOrderView:
    return await _transition(
        order_id, "cancelled", payload, session, user, Permissions.MAINTENANCE_CLOSE
    )


@router.post("/work-orders/{order_id}/attachments", status_code=201)
async def post_attachment(
    order_id: UUID,
    payload: AttachmentCreate,
    request: Request,
    session: SessionDep,
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.MAINTENANCE_ATTACHMENT)),
    ],
) -> dict:
    attachment, grant = await create_attachment(
        session,
        order=await get_work_order(session, order_id),
        payload=payload,
        user=user,
        storage=request.app.state.providers.storage,
    )
    return {
        "attachment_id": str(attachment.id),
        "object_key": grant.object_key,
        "upload_url": grant.upload_url,
        "headers": grant.headers,
    }
