from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.maintenance_order.application.workflow import resolve_maintenance_review
from app.modules.maintenance_order.domain.models import (
    AbnormalityAssessment,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderTransition,
)
from app.modules.maintenance_order.domain.schemas import AttachmentCreate
from app.modules.maintenance_order.infrastructure.repository import MaintenanceOrderRepository
from app.modules.operation_event.domain.models import ProductionEvent, ProductionEventVersion
from app.ports.storage import StorageProvider, UploadGrant
from app.shared.errors import ConflictError, NotFoundError
from app.shared.media.names import safe_object_filename
from app.shared.platform.models import WorkflowRun
from app.shared.platform.service import add_audit
from app.shared.security.authorization.dependencies import require_data_scope
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_review", "cancelled"},
    "pending_review": {"approved", "draft", "cancelled"},
    "approved": {"dispatched", "cancelled"},
    "dispatched": {"in_progress", "cancelled"},
    "in_progress": {"resolved", "cancelled"},
    "resolved": {"closed", "in_progress", "cancelled"},
    "closed": set(),
    "cancelled": set(),
}


async def get_workflow(session: AsyncSession, workflow_id: UUID) -> WorkflowRun:
    workflow = await MaintenanceOrderRepository(session).get_workflow(workflow_id)
    if workflow is None:
        raise NotFoundError("workflow", workflow_id)
    return workflow


async def review_workflow(
    session: AsyncSession,
    *,
    workflow: WorkflowRun,
    approved: bool,
    reason: str,
    user: CurrentUser,
) -> WorkOrder | None:
    locked = await session.scalar(
        select(WorkflowRun).where(WorkflowRun.id == workflow.id).with_for_update()
    )
    if locked is None:
        raise NotFoundError("workflow", workflow.id)
    workflow = locked
    event = await session.get(ProductionEvent, workflow.resource_id)
    if event is None:
        raise NotFoundError("production_event", workflow.resource_id)
    require_data_scope(
        user,
        workflow.organization_unit_id,
        Permissions.MAINTENANCE_REVIEW,
        owner_id=event.created_by,
    )
    if workflow.status != "awaiting_review":
        raise ConflictError("workflow is not awaiting review", status=workflow.status)
    assessment_id = UUID(workflow.state_snapshot["assessment_id"])
    assessment = await session.get(AbnormalityAssessment, assessment_id)
    if assessment is None:
        raise NotFoundError("abnormality_assessment", assessment_id)
    assessment.reviewed_by = user.user_id
    assessment.review_reason = reason
    decision = await resolve_maintenance_review(approved=approved, reason=reason)
    workflow.state_snapshot = {
        **workflow.state_snapshot,
        "node": decision,
        "review": {"approved": approved, "reason": reason, "reviewed_by": str(user.user_id)},
    }
    workflow.lock_version += 1
    if decision == "rejected":
        assessment.review_status = "rejected"
        workflow.status = "rejected"
        workflow.current_node = "rejected"
        await add_audit(
            session,
            user=user,
            action="maintenance_assessment.reject",
            resource_type="workflow",
            resource_id=workflow.id,
            reason=reason,
        )
        await session.commit()
        return None
    assessment.review_status = "approved"
    workflow.status = "completed"
    workflow.current_node = "approved"
    existing = await session.scalar(
        select(WorkOrder).where(WorkOrder.source_workflow_run_id == workflow.id)
    )
    if existing:
        return existing
    version = await session.get(ProductionEventVersion, event.current_version_id)
    description = version.description if version else event.title
    order = WorkOrder(
        organization_unit_id=workflow.organization_unit_id,
        event_id=assessment.event_id,
        source_workflow_run_id=workflow.id,
        order_no=f"WO-{datetime.now(UTC):%Y%m%d}-{str(workflow.id)[:8].upper()}",
        title=event.title,
        description=f"{description}\n建议：{assessment.recommended_action}",
        risk_level=assessment.risk_level,
        status="draft",
        version=1,
        created_by=user.user_id,
    )
    session.add(order)
    await session.flush()
    session.add(
        WorkOrderTransition(
            work_order_id=order.id,
            from_status="none",
            to_status="draft",
            actor_id=user.user_id,
            reason="approved assessment created work order",
            occurred_at=datetime.now(UTC),
            version_after=order.version,
        )
    )
    await add_audit(
        session,
        user=user,
        action="work_order.create",
        resource_type="work_order",
        resource_id=order.id,
        reason=reason,
    )
    await session.commit()
    return order


async def get_work_order(session: AsyncSession, order_id: UUID) -> WorkOrder:
    order = await MaintenanceOrderRepository(session).get_work_order(order_id)
    if order is None:
        raise NotFoundError("work_order", order_id)
    return order


async def transition_work_order(
    session: AsyncSession,
    *,
    order: WorkOrder,
    target: str,
    reason: str,
    expected_version: int,
    user: CurrentUser,
    permission: str,
    assignee_id: UUID | None = None,
    due_at: datetime | None = None,
) -> WorkOrder:
    require_data_scope(
        user,
        order.organization_unit_id,
        permission,
        owner_id=order.created_by,
        assignee_id=order.assignee_id,
    )
    if order.version != expected_version:
        raise ConflictError(
            "work order version mismatch", expected=expected_version, actual=order.version
        )
    if target not in ALLOWED_TRANSITIONS.get(order.status, set()):
        raise ConflictError(
            "illegal work order transition", from_status=order.status, to_status=target
        )
    before = order.status
    order.status = target
    order.version += 1
    if assignee_id is not None:
        order.assignee_id = assignee_id
    if due_at is not None:
        order.due_at = due_at
    session.add(
        WorkOrderTransition(
            work_order_id=order.id,
            from_status=before,
            to_status=target,
            actor_id=user.user_id,
            reason=reason,
            occurred_at=datetime.now(UTC),
            version_after=order.version,
        )
    )
    await add_audit(
        session,
        user=user,
        action=f"work_order.{target}",
        resource_type="work_order",
        resource_id=order.id,
        before={"status": before},
        after={"status": target, "version": order.version},
        reason=reason,
    )
    await session.commit()
    return order


async def create_attachment(
    session: AsyncSession,
    *,
    order: WorkOrder,
    payload: AttachmentCreate,
    user: CurrentUser,
    storage: StorageProvider,
) -> tuple[WorkOrderAttachment, UploadGrant]:
    require_data_scope(
        user,
        order.organization_unit_id,
        Permissions.MAINTENANCE_ATTACHMENT,
        owner_id=order.created_by,
        assignee_id=order.assignee_id,
    )
    attachment = WorkOrderAttachment(
        work_order_id=order.id,
        object_key="pending",
        filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        uploaded_by=user.user_id,
    )
    session.add(attachment)
    await session.flush()
    attachment.object_key = (
        f"work-orders/{order.id}/{attachment.id}/{safe_object_filename(payload.filename)}"
    )
    grant = await storage.create_upload(
        object_key=attachment.object_key,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    await session.commit()
    return attachment, grant
