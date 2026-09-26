import math
from time import perf_counter
from uuid import UUID

from app.modules.knowledge.application.answers import grounded_answer
from app.modules.knowledge.application.retrieval import retrieve
from app.modules.knowledge.domain.models import KnowledgeEvaluation
from app.modules.knowledge.domain.schemas import EvaluationCreate, SearchInput
from app.shared.platform.execution import execution_lease, guard_execution
from app.shared.platform.models import AsyncJob
from app.shared.platform.runtime import text_provider_for_job
from app.shared.platform.service import update_job
from app.shared.worker_runtime import job_user


def retrieval_metrics(expected: set[str], actual: list[str]) -> dict:
    matched = expected.intersection(actual)
    reciprocal = next((1 / (i + 1) for i, value in enumerate(actual) if value in expected), 0.0)
    dcg = sum(1 / math.log2(i + 2) for i, value in enumerate(actual) if value in expected)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(expected), len(actual))))
    return {
        "recall": len(matched) / len(expected) if expected else float(not actual),
        "mrr": reciprocal,
        "ndcg": dcg / ideal if ideal else float(not actual),
    }


async def execute_evaluation(factory, knowledge, text, settings, job_id: UUID):
    token = None
    try:
        async with execution_lease(
            factory,
            job_id,
            lease_seconds=settings.job_lease_seconds,
            interval_seconds=settings.job_heartbeat_interval_seconds,
        ) as token:
            async with factory() as session:
                job = await session.get(AsyncJob, job_id)
                from app.modules.knowledge.application.observability import observed_knowledge

                knowledge = observed_knowledge(knowledge, session, job)
                text = text_provider_for_job(session, job, text)
                evaluation = await session.get(KnowledgeEvaluation, job.resource_id)
                payload = EvaluationCreate.model_validate(evaluation.dataset)
                evaluation.status = "running"
                await session.commit()
                results = []
                for mode in payload.modes:
                    for case in payload.cases:
                        user = await job_user(session, job)
                        start = perf_counter()
                        evidence, trace = await retrieve(
                            session,
                            user,
                            SearchInput(
                                question=case.question, base_ids=payload.base_ids, mode=mode
                            ),
                            knowledge,
                            text,
                        )
                        metrics = retrieval_metrics(
                            {str(i) for i in case.relevant_evidence_ids},
                            [e["id"] for e in evidence],
                        )
                        answer = await grounded_answer(text, case.question, evidence)
                        metrics["refusal_accuracy"] = float(answer.refused == case.should_refuse)
                        results.append(
                            {
                                "question": case.question,
                                "mode": mode,
                                **metrics,
                                "elapsed_ms": (perf_counter() - start) * 1000,
                                "generation": trace.get("generations", []),
                                "evidence_ids": [e["id"] for e in evidence],
                            }
                        )
                        job = await guard_execution(session, job_id, token)
                        await session.commit()
                job = await guard_execution(session, job_id, token)
                evaluation.results = {
                    "cases": results,
                    "scope": "retrieval_and_refusal_metrics",
                    "dataset_split": payload.split,
                    "averages": {
                        metric: sum(r[metric] for r in results) / len(results)
                        for metric in ("recall", "mrr", "ndcg", "refusal_accuracy", "elapsed_ms")
                    },
                }
                evaluation.status = "succeeded"
                await update_job(session, job, status="succeeded", progress=100)
                await session.commit()
    except BaseException:
        async with factory() as session:
            job = await session.get(AsyncJob, job_id, with_for_update=True)
            if job and token and job.execution_token == token and job.status == "running":
                evaluation = await session.get(KnowledgeEvaluation, job.resource_id)
                evaluation.status = "cancelled" if job.cancel_requested else "failed"
                await update_job(
                    session,
                    job,
                    status=evaluation.status,
                    progress=job.progress,
                    error_code=None if job.cancel_requested else "KNOWLEDGE_EVALUATION_FAILED",
                )
                await session.commit()
        raise
