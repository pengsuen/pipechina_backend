"""基于专家标注的多智能体离线评测；不调用模型或更改调查结论。"""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operation_event.domain.agent_models import (
    AgentEvidence,
    AgentOfflineEvaluation,
    AgentProposal,
    AgentRun,
)
from app.modules.operation_event.domain.evaluation_schemas import AgentEvaluationCreate
from app.shared.errors import ConflictError, NotFoundError
from app.shared.security.authorization.dependencies import require_data_scope
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def score_case(
    *,
    actual_category: str,
    actual_risk_level: str,
    cited_evidence_ids: set[str],
    expected_category: str,
    expected_risk_level: str,
    relevant_evidence_ids: set[str],
    action_acceptable: bool,
    unsafe_action: bool,
) -> dict:
    """计算单样本指标；空引用和空标注同时出现时证据分为 1。"""
    true_positives = len(cited_evidence_ids & relevant_evidence_ids)
    precision = (
        true_positives / len(cited_evidence_ids)
        if cited_evidence_ids
        else float(not relevant_evidence_ids)
    )
    recall = (
        true_positives / len(relevant_evidence_ids)
        if relevant_evidence_ids
        else float(not cited_evidence_ids)
    )
    evidence_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "category_correct": actual_category == expected_category,
        "risk_correct": actual_risk_level == expected_risk_level,
        "risk_underestimated": _RISK_ORDER[actual_risk_level] < _RISK_ORDER[expected_risk_level],
        "evidence_precision": precision,
        "evidence_recall": recall,
        "evidence_f1": evidence_f1,
        "action_acceptable": action_acceptable,
        "unsafe_action": unsafe_action,
    }


def aggregate_scores(cases: list[dict]) -> dict:
    count = len(cases)
    dimensions = {
        "category_accuracy": "category_correct",
        "risk_accuracy": "risk_correct",
        "risk_underestimation_rate": "risk_underestimated",
        "evidence_precision": "evidence_precision",
        "evidence_recall": "evidence_recall",
        "evidence_f1": "evidence_f1",
        "action_acceptance_rate": "action_acceptable",
        "unsafe_action_rate": "unsafe_action",
    }
    return {
        "case_count": count,
        **{
            name: round(sum(float(case["metrics"][key]) for case in cases) / count, 4)
            for name, key in dimensions.items()
        },
    }


async def create_evaluation(
    session: AsyncSession, payload: AgentEvaluationCreate, user: CurrentUser
) -> AgentOfflineEvaluation:
    run_ids = [case.run_id for case in payload.cases]
    runs = {
        run.id: run
        for run in await session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))
    }
    for case in payload.cases:
        run = runs.get(case.run_id)
        if run is None:
            raise NotFoundError("agent_run", case.run_id)
        require_data_scope(
            user, run.organization_unit_id, Permissions.EVENT_CLASSIFY, owner_id=run.created_by
        )
        if run.status != "succeeded":
            raise ConflictError("agent run must have succeeded", run_id=str(case.run_id))

    proposals = {
        proposal.run_id: proposal
        for proposal in await session.scalars(
            select(AgentProposal).where(AgentProposal.run_id.in_(run_ids))
        )
    }
    evidence_by_run: dict[UUID, set[str]] = defaultdict(set)
    for evidence in await session.scalars(
        select(AgentEvidence).where(AgentEvidence.run_id.in_(run_ids))
    ):
        evidence_by_run[evidence.run_id].add(str(evidence.id))

    case_results: list[dict] = []
    for case in payload.cases:
        proposal = proposals.get(case.run_id)
        if proposal is None:
            raise ConflictError("agent run has no proposal", run_id=str(case.run_id))
        if proposal.risk_level not in _RISK_ORDER:
            raise ConflictError("agent proposal has invalid risk level", run_id=str(case.run_id))
        relevant_ids = {str(value) for value in case.relevant_evidence_ids}
        if not relevant_ids <= evidence_by_run[case.run_id]:
            raise ConflictError("expert evidence must belong to the run", run_id=str(case.run_id))
        cited_ids = {str(value) for value in proposal.evidence_ids}
        metrics = score_case(
            actual_category=proposal.category,
            actual_risk_level=proposal.risk_level,
            cited_evidence_ids=cited_ids,
            expected_category=case.expected_category,
            expected_risk_level=case.expected_risk_level,
            relevant_evidence_ids=relevant_ids,
            action_acceptable=case.action_acceptable,
            unsafe_action=case.unsafe_action,
        )
        case_results.append(
            {
                "run_id": str(case.run_id),
                "event_version_id": str(runs[case.run_id].event_version_id),
                "actual": {
                    "category": proposal.category,
                    "risk_level": proposal.risk_level,
                    "recommended_action": proposal.recommended_action,
                    "rationale": proposal.rationale,
                    "evidence_ids": sorted(cited_ids),
                },
                "expected": case.model_dump(mode="json", exclude={"run_id"}),
                "metrics": metrics,
            }
        )

    evaluation = AgentOfflineEvaluation(
        requested_by=user.user_id,
        name=payload.name,
        split=payload.split,
        dataset=payload.model_dump(mode="json"),
        results={"summary": aggregate_scores(case_results), "cases": case_results},
    )
    session.add(evaluation)
    await session.commit()
    await session.refresh(evaluation)
    return evaluation


async def get_evaluation(
    session: AsyncSession, evaluation_id: UUID, user: CurrentUser
) -> AgentOfflineEvaluation:
    evaluation = await session.get(AgentOfflineEvaluation, evaluation_id)
    if evaluation is None:
        raise NotFoundError("agent_offline_evaluation", evaluation_id)
    run_ids = [UUID(case["run_id"]) for case in evaluation.dataset["cases"]]
    runs = {
        run.id: run
        for run in await session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))
    }
    for run_id in run_ids:
        run = runs.get(run_id)
        if run is None:
            raise NotFoundError("agent_run", run_id)
        require_data_scope(
            user, run.organization_unit_id, Permissions.EVENT_CLASSIFY, owner_id=run.created_by
        )
    return evaluation
