from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EventExtractionCreate(BaseModel):
    source_type: str = Field(pattern=r"^(audio_transcript|manual_operation|raw_text)$")
    source_id: UUID | None = None
    source_version_id: UUID | None = None
    raw_text: str | None = None


class EventUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    event_type: str = Field(min_length=2, max_length=80)
    severity: str = Field(pattern=r"^(low|medium|high|critical)$")
    occurred_at: datetime | None = None
    description: str = Field(min_length=2)
    structured_data: dict[str, Any] = Field(default_factory=dict)


class EventView(BaseModel):
    id: UUID
    organization_unit_id: UUID
    title: str
    event_type: str
    severity: str
    occurred_at: datetime | None
    business_status: str
    current_version_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MergeEventsInput(BaseModel):
    event_ids: list[UUID] = Field(min_length=2)
    title: str = Field(min_length=2, max_length=300)


class SplitEventItem(BaseModel):
    title: str
    event_type: str
    severity: str = "medium"
    description: str


class SplitEventInput(BaseModel):
    items: list[SplitEventItem] = Field(min_length=2)


class RejectInput(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)
