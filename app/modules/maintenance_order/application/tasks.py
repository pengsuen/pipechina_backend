import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.bootstrap.celery_app import celery_app
from app.modules.maintenance_order.domain.models import WorkOrder, WorkOrderReminder
from app.shared.worker_runtime import worker_resources


async def _check_reminders() -> int:
    now = datetime.now(UTC)
    async with worker_resources() as resources:
        async with resources.database.session_factory() as session:
            orders = list(
                await session.scalars(
                    select(WorkOrder).where(
                        WorkOrder.due_at.is_not(None),
                        WorkOrder.due_at <= now,
                        WorkOrder.status.not_in(("closed", "cancelled")),
                    )
                )
            )
            created = 0
            for order in orders:
                existing = await session.scalar(
                    select(WorkOrderReminder).where(
                        WorkOrderReminder.work_order_id == order.id,
                        WorkOrderReminder.reminder_type == "overdue",
                        WorkOrderReminder.scheduled_at == order.due_at,
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    WorkOrderReminder(
                        work_order_id=order.id,
                        reminder_type="overdue",
                        scheduled_at=order.due_at,
                        status="pending",
                        payload={
                            "order_no": order.order_no,
                            "assignee_id": str(order.assignee_id) if order.assignee_id else None,
                        },
                    )
                )
                created += 1
            await session.commit()
            return created


@celery_app.task(name="app.modules.maintenance_order.check_reminders", acks_late=True)
def check_reminders_task() -> dict[str, str]:
    return {"status": "ok", "created": str(asyncio.run(_check_reminders()))}
