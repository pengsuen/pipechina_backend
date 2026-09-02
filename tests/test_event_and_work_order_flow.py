from uuid import uuid4

from fastapi.testclient import TestClient

from tests.helpers import create_confirmed_event


def test_candidate_event_cannot_be_classified(client: TestClient) -> None:
    extracted = client.post(
        "/api/v1/event-extractions",
        json={
            "source_type": "raw_text",
            "raw_text": "凌晨两点发现二号阀门轻微渗漏，等待人工复核。",
        },
    )
    assert extracted.status_code == 202, extracted.text
    event_id = extracted.json()["event_ids"][0]

    classified = client.post(f"/api/v1/events/{event_id}:classify")

    assert classified.status_code == 409
    assert classified.json()["code"] == "STATE_CONFLICT"


def test_event_classification_human_review_and_work_order_state_machine(
    client: TestClient,
) -> None:
    event_id = create_confirmed_event(client)["event_id"]
    classified = client.post(f"/api/v1/events/{event_id}:classify")
    assert classified.status_code == 202, classified.text
    workflow_id = classified.json()["workflow_id"]
    assert workflow_id

    workflow = client.get(f"/api/v1/workflows/{workflow_id}")
    assert workflow.json()["status"] == "awaiting_review"
    assert workflow.json()["current_node"] == "human_review"

    reviewed = client.post(
        f"/api/v1/workflows/{workflow_id}:review",
        json={"approved": True, "reason": "现场信息已复核，同意创建维检工单"},
    )
    assert reviewed.status_code == 200, reviewed.text
    order_id = reviewed.json()["work_order_id"]
    order = client.get(f"/api/v1/work-orders/{order_id}").json()
    assert order["status"] == "draft"
    assert order["version"] == 1

    response = client.post(
        f"/api/v1/work-orders/{order_id}:start",
        json={"reason": "非法跳过审批", "expected_version": 1},
    )
    assert response.status_code == 409

    transitions = [
        ("submit-review", {"reason": "提交值班长审批", "expected_version": 1}),
        (
            "review",
            {"approved": True, "reason": "风险与措施匹配", "expected_version": 2},
        ),
        (
            "dispatch",
            {
                "reason": "派发维检班组",
                "expected_version": 3,
                "assignee_id": str(uuid4()),
            },
        ),
        ("start", {"reason": "现场开始处置", "expected_version": 4}),
        ("resolve", {"reason": "渗漏已消除并复测", "expected_version": 5}),
        ("close", {"reason": "验收通过并关闭", "expected_version": 6}),
    ]
    for action, payload in transitions:
        response = client.post(f"/api/v1/work-orders/{order_id}:{action}", json=payload)
        assert response.status_code == 200, response.text

    closed = client.get(f"/api/v1/work-orders/{order_id}").json()
    assert closed["status"] == "closed"
    assert closed["version"] == 7
    timeline = client.get(f"/api/v1/work-orders/{order_id}/timeline").json()
    assert len(timeline) == 7
    assert timeline[-1]["to_status"] == "closed"
