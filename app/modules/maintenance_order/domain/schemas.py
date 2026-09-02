from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class WorkOrderView(BaseModel):
    id: UUID
    organization_unit_id: UUID
    event_id: UUID | None
    order_no: str
    title: str
    description: str
    risk_level: str
    status: str
    version: int
    assignee_id: UUID | None
    due_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkOrderReviewInput(BaseModel):
    approved: bool
    reason: str = Field(min_length=2, max_length=1000)
    expected_version: int


class WorkOrderTransitionInput(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)
    expected_version: int


class DispatchInput(WorkOrderTransitionInput):
    assignee_id: UUID
    due_at: datetime | None = None


class AttachmentCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=120)
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)
