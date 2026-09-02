from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base
from app.shared.model_mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.shared.types import JSON_DOCUMENT


class ProductionEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "production_events"
    __table_args__ = (
        Index("ix_production_events_org_time", "organization_unit_id", "occurred_at"),
        Index("ix_production_events_status", "business_status", "updated_at"),
    )

    organization_unit_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    business_status: Mapped[str] = mapped_column(String(32), default="candidate", nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class ProductionEventVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "production_event_versions"
    __table_args__ = (UniqueConstraint("event_id", "version", name="uq_production_event_version"),)

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("production_events.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class EventSourceLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_source_links"
    __table_args__ = (
        UniqueConstraint("event_id", "source_type", "source_id", name="uq_event_source_link"),
    )

    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("production_events.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    evidence_text: Mapped[str | None] = mapped_column(Text)
    evidence_locator: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
