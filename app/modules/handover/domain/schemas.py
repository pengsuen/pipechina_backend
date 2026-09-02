from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AudioRecordCreate(BaseModel):
    shift_date: date
    shift_code: str = Field(min_length=1, max_length=40)
    organization_unit_id: UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    mime_type: str = Field(min_length=3, max_length=120)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class UploadComplete(BaseModel):
    server_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class TranscriptSegmentInput(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    speaker_label: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class TranscriptUpdate(BaseModel):
    full_text: str = Field(min_length=1)
    segments: list[TranscriptSegmentInput] = Field(default_factory=list)


class SummaryUpdate(BaseModel):
    content: dict[str, Any]
    transcript_version_id: UUID | None = None


class AudioRecordView(BaseModel):
    id: UUID
    organization_unit_id: UUID
    shift_date: date
    shift_code: str
    filename: str
    upload_status: str
    business_status: str
    current_transcript_version_id: UUID | None
    current_summary_version_id: UUID | None
    deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AudioCreateResponse(BaseModel):
    audio: AudioRecordView
    upload_session_id: UUID
    object_key: str
    upload_url: str
    upload_headers: dict[str, str]


class ManualOperationCreate(BaseModel):
    occurred_at: str
    record_type: str
    content: str = Field(min_length=1)
    structured_data: dict[str, Any] = Field(default_factory=dict)


class ManualOperationUpdate(BaseModel):
    content: str = Field(min_length=1)
    structured_data: dict[str, Any] = Field(default_factory=dict)
