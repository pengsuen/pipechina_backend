from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import (
    Boolean,
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


class AudioRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audio_records"
    __table_args__ = (Index("ix_audio_records_org_shift", "organization_unit_id", "shift_date"),)

    organization_unit_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    shift_date: Mapped[date] = mapped_column(nullable=False)
    shift_code: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    upload_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    business_status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    current_transcript_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    current_summary_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    confirmed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class AudioTranscriptVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audio_transcript_versions"
    __table_args__ = (
        UniqueConstraint("audio_record_id", "version", name="uq_audio_transcript_version"),
    )

    audio_record_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("audio_records.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(20))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class AudioTranscriptSegment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audio_transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_version_id", "segment_index", name="uq_transcript_segment"),
        Index("ix_transcript_segments_time", "transcript_version_id", "start_ms", "end_ms"),
    )

    transcript_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("audio_transcript_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[float | None] = mapped_column()


class HandoverSummaryVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "handover_summary_versions"
    __table_args__ = (
        UniqueConstraint("audio_record_id", "version", name="uq_handover_summary_version"),
    )

    audio_record_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("audio_records.id", ondelete="RESTRICT"), index=True
    )
    transcript_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("audio_transcript_versions.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    source_segment_ids: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class ManualOperationRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "manual_operation_records"
    __table_args__ = (Index("ix_manual_operation_org_time", "organization_unit_id", "occurred_at"),)

    organization_unit_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False)
    record_type: Mapped[str] = mapped_column(String(60), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    business_status: Mapped[str] = mapped_column(String(32), default="confirmed", nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class ManualOperationRecordVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "manual_operation_record_versions"
    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_manual_operation_record_version"),
    )

    record_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("manual_operation_records.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
