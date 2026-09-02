from __future__ import annotations

# 测试用确定性Provider，不访问外部模型服务。
from typing import cast

from pydantic import BaseModel

from app.ports.models import (
    ASRResult,
    ASRSegment,
    AssessmentCandidate,
    EventCandidate,
    EventCandidateList,
    FindingCandidate,
    HandoverSummary,
    MediaRef,
    ReportDraft,
)
from app.ports.text import T


class FakeTextLLMProvider:
    """仅供测试和本地演示使用的确定性文本模型实现。"""

    name = "fake"
    model = "fake-text-v1"

    async def generate_structured(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        del system_prompt
        if operation == "handover_summary":
            has_leak = "渗漏" in user_prompt or "泄漏" in user_prompt
            result: BaseModel = HandoverSummary(
                operating_status=["夜班运行总体平稳"],
                pending_items=["复查二号阀门", "跟进维检工单"] if has_leak else ["按计划巡检"],
                risks=["二号阀门存在轻微渗漏"] if has_leak else [],
                attention_items=["交接后两小时内反馈复查结果"],
            )
        elif operation == "event_extraction":
            result = EventCandidateList(
                events=[
                    EventCandidate(
                        title="二号阀门轻微渗漏",
                        event_type="equipment_leak",
                        occurred_at_text="凌晨两点",
                        description="巡查发现二号阀门轻微渗漏，已设置警戒并通知维检。",
                        severity="medium",
                        confidence=0.92,
                        evidence_text="发现二号阀门轻微渗漏",
                    )
                ]
            )
        elif operation == "event_classification":
            level = "high" if "大量" in user_prompt or "火灾" in user_prompt else "medium"
            result = AssessmentCandidate(
                category="equipment_leak",
                risk_level=level,
                rationale="存在可见渗漏，需要人工确认影响范围。",
                recommended_action="设置警戒、复核压力并安排维检。",
            )
        elif operation == "report_generation":
            import json

            payload = json.loads(user_prompt)
            sources = payload.get("sources", [])
            source_ids = [str(item.get("id")) for item in sources if item.get("id")]
            report_type = payload.get("report_type")
            result = ReportDraft(
                title="生产运行日报" if report_type == "daily" else "生产事件复盘",
                sections={
                    "运行概况": "当日生产运行总体平稳。",
                    "重点事件": f"共纳入 {len(sources)} 条已确认来源记录。",
                    "异常及处置": "二号阀门渗漏已进入人工审核和维检流程。",
                    "未完成事项": "跟进现场复查与工单闭环。",
                    "风险提示": "模型内容为草稿，发布前必须人工核验。",
                    "次日关注": "关注阀门复查结果和工单时效。",
                },
                source_ids=source_ids,
                pending_facts=[],
            )
        else:
            raise ValueError(f"unsupported fake text operation: {operation}")
        return cast(T, response_model.model_validate(result.model_dump()))


class FakeSpeechToTextProvider:
    name = "fake"
    model = "fake-asr-v1"

    async def transcribe(
        self,
        media: MediaRef,
        *,
        hotwords: list[str],
        language: str | None = "zh",
    ) -> ASRResult:
        terms = "、".join(hotwords[:2]) if hotwords else "一号压缩机"
        texts = [
            f"夜班运行总体平稳，{terms}参数正常。",
            "凌晨两点发现二号阀门轻微渗漏，已设置警戒并通知维检。",
            "下一班需要复查阀门并跟进工单。",
        ]
        segments = [
            ASRSegment(index=i, start_ms=i * 8000, end_ms=(i + 1) * 8000, text=text)
            for i, text in enumerate(texts)
        ]
        return ASRResult(
            language=language or "zh",
            duration_ms=24000,
            segments=segments,
            full_text="".join(texts),
            provider_request_id=f"fake-asr:{media.object_key.rsplit('/', 1)[-1]}",
        )


class FakeVisionProvider:
    name = "fake"
    model = "fake-vision-v1"

    async def inspect(
        self,
        media: MediaRef,
        *,
        context: str,
    ) -> list[FindingCandidate]:
        del media, context
        return [
            FindingCandidate(
                title="阀体表面疑似油气痕迹",
                category="visible_leak_trace",
                severity="medium",
                description="图像中阀体连接处存在颜色异常，需要现场复核。",
                evidence="阀体法兰下方可见深色痕迹",
                confidence=0.86,
            )
        ]
