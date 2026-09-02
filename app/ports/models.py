from __future__ import annotations

# 定义Provider之间传递的标准数据模型，避免业务代码依赖厂商响应格式。
from pydantic import BaseModel, ConfigDict, Field


class MediaRef(BaseModel):
    """业务代码传给媒体Provider的存储无关引用。"""

    model_config = ConfigDict(frozen=True)

    object_key: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    checksum: str | None = None
    filename: str | None = None


class StrictProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ASRSegment(BaseModel):
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    speaker_label: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class ASRResult(BaseModel):
    language: str | None = None
    duration_ms: int = Field(gt=0)
    segments: list[ASRSegment]
    full_text: str = Field(min_length=1)
    provider_request_id: str | None = None


class HandoverSummary(StrictProviderOutput):
    operating_status: list[str]
    pending_items: list[str]
    risks: list[str]
    attention_items: list[str]


class EventCandidate(StrictProviderOutput):
    title: str
    event_type: str
    occurred_at_text: str | None
    description: str
    severity: str
    confidence: float = Field(ge=0, le=1)
    evidence_text: str


class EventCandidateList(StrictProviderOutput):
    events: list[EventCandidate]


class AssessmentCandidate(StrictProviderOutput):
    category: str
    risk_level: str
    rationale: str
    recommended_action: str


class FindingCandidate(StrictProviderOutput):
    title: str
    category: str
    severity: str
    description: str
    evidence: str
    confidence: float = Field(ge=0, le=1)


class FindingCandidateList(StrictProviderOutput):
    findings: list[FindingCandidate]


class ReportDraft(StrictProviderOutput):
    title: str
    sections: dict[str, str]
    source_ids: list[str]
    pending_facts: list[str]
