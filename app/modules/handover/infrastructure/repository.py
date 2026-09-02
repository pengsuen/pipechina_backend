from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.handover.domain.models import AudioRecord


class HandoverRepository:
    """SQLAlchemy persistence adapter used by handover application services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_audio(self, audio_id: UUID) -> AudioRecord | None:
        return await self.session.get(AudioRecord, audio_id)
