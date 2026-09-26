from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.helpers import create_confirmed_event


def test_inspection_image_analysis_review_and_link(client: TestClient) -> None:
    inspection = client.post(
        "/api/v1/inspections",
        json={
            "station_name": "北京输气站",
            "pipeline_name": "一号干线",
            "equipment_name": "二号阀门",
            "inspected_at": datetime.now(UTC).isoformat(),
            "notes": "例行巡检",
        },
    )
    assert inspection.status_code == 201, inspection.text
    inspection_id = inspection.json()["id"]

    image = client.post(
        f"/api/v1/inspections/{inspection_id}/images",
        json={"filename": "valve.jpg", "mime_type": "image/jpeg", "size_bytes": 2048},
    )
    assert image.status_code == 201, image.text
    image_id = image.json()["image_id"]
    completed = client.post(
        f"/api/v1/inspections/{inspection_id}/images/{image_id}:complete",
        json={},
    )
    assert completed.status_code == 200, completed.text

    analyzed = client.post(f"/api/v1/inspections/{inspection_id}:analyze")
    assert analyzed.status_code == 202, analyzed.text
    finding_id = analyzed.json()["finding_ids"][0]
    confirmed = client.post(
        f"/api/v1/findings/{finding_id}:confirm", json={"reason": "现场复核确认存在痕迹"}
    )
    assert confirmed.json()["review_status"] == "confirmed"

    event_id = create_confirmed_event(client)["event_id"]
    linked = client.post(f"/api/v1/findings/{finding_id}:link-event", json={"event_id": event_id})
    assert linked.status_code == 201, linked.text
    links = client.get(f"/api/v1/findings/{finding_id}/links").json()
    assert len(links) == 1
    assert links[0]["target_id"] == event_id

    protected = client.delete(f"/api/v1/inspections/{inspection_id}/images/{image_id}")
    assert protected.status_code == 409
