from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def test_hazard_rectification_rejection_and_acceptance(client: TestClient) -> None:
    user_id = client.get("/api/v1/auth/me").json()["user_id"]
    created = client.post(
        "/api/v1/hazards",
        json={
            "title": "阀门填料函渗漏",
            "description": "巡检发现阀门填料函存在轻微渗漏",
            "location": "北京输气站二号阀室",
            "category": "设备设施",
            "source_type": "manual",
        },
    )
    assert created.status_code == 201, created.text
    hazard = created.json()
    assert hazard["status"] == "pending_assessment"
    assert hazard["version"] == 1

    assessed = client.post(
        f"/api/v1/hazards/{hazard['id']}:assess",
        json={
            "likelihood": 4,
            "consequence": 5,
            "rationale": "泄漏可能扩大并造成重大后果",
            "control_measures": "设置警戒并加强泄漏检测",
            "assignee_id": user_id,
            "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "expected_version": 1,
        },
    )
    assert assessed.status_code == 200, assessed.text
    hazard = assessed.json()
    assert hazard["risk_score"] == 20
    assert hazard["risk_level"] == "major"
    assert hazard["status"] == "pending_rectification"

    stale = client.post(
        f"/api/v1/hazards/{hazard['id']}:start-rectification",
        json={"reason": "开始处理", "expected_version": 1},
    )
    assert stale.status_code == 409

    started = client.post(
        f"/api/v1/hazards/{hazard['id']}:start-rectification",
        json={"reason": "现场具备整改条件", "expected_version": 2},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "rectifying"

    submitted = client.post(
        f"/api/v1/hazards/{hazard['id']}:submit-rectification",
        json={
            "action": "紧固填料压盖并更换密封件",
            "result": "静态检查未发现泄漏",
            "expected_version": 3,
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending_acceptance"

    rejected = client.post(
        f"/api/v1/hazards/{hazard['id']}:reject",
        json={"reason": "还需完成带压复测", "expected_version": 4},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rectifying"

    resubmitted = client.post(
        f"/api/v1/hazards/{hazard['id']}:submit-rectification",
        json={
            "action": "完成带压复测",
            "result": "连续观察两小时无泄漏",
            "expected_version": 5,
        },
    )
    assert resubmitted.status_code == 200, resubmitted.text

    accepted = client.post(
        f"/api/v1/hazards/{hazard['id']}:accept",
        json={"reason": "现场复核合格，同意销项", "expected_version": 6},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "closed"
    assert accepted.json()["closed_at"] is not None

    detail = client.get(f"/api/v1/hazards/{hazard['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert len(body["assessments"]) == 1
    assert len(body["rectifications"]) == 2
    assert [item["decision"] for item in body["acceptances"]] == ["rejected", "accepted"]
    assert len(body["transitions"]) == 6


def test_hazard_rejects_past_rectification_deadline(client: TestClient) -> None:
    user_id = client.get("/api/v1/auth/me").json()["user_id"]
    hazard = client.post(
        "/api/v1/hazards",
        json={
            "title": "围栏破损",
            "description": "站场东侧围栏破损",
            "location": "站场东侧",
            "category": "安防",
            "source_type": "manual",
        },
    ).json()
    response = client.post(
        f"/api/v1/hazards/{hazard['id']}:assess",
        json={
            "likelihood": 2,
            "consequence": 2,
            "rationale": "存在人员误入风险",
            "control_measures": "设置临时警戒",
            "assignee_id": user_id,
            "due_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "expected_version": 1,
        },
    )
    assert response.status_code == 409
