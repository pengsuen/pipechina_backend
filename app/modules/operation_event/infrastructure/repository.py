from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operation_event.domain.models import ProductionEvent


class OperationEventRepository:
    """SQLAlchemy persistence adapter used by production-event services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_event(self, event_id: UUID) -> ProductionEvent | None:
        return await self.session.get(ProductionEvent, event_id)
