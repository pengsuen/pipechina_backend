from fastapi.testclient import TestClient

from tests.helpers import create_confirmed_handover


def test_handover_end_to_end(client: TestClient) -> None:
    result = create_confirmed_handover(client)
    audio_id = result["audio_id"]

    record = client.get(f"/api/v1/audio-records/{audio_id}")
    assert record.status_code == 200
    assert record.json()["business_status"] == "confirmed"
    assert record.json()["current_transcript_version_id"]
    assert record.json()["current_summary_version_id"]

    segments = client.get(f"/api/v1/audio-records/{audio_id}/segments")
    assert segments.status_code == 200
    assert len(segments.json()) == 3
    assert segments.json()[1]["start_ms"] == 8000

    versions = client.get(f"/api/v1/audio-records/{audio_id}/versions").json()
    assert len(versions["transcripts"]) == 1
    assert len(versions["summaries"]) == 1

    job = client.get(f"/api/v1/jobs/{result['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert job.json()["progress"] == 100


def test_confirmed_audio_is_delete_protected(client: TestClient) -> None:
    audio_id = create_confirmed_handover(client)["audio_id"]
    response = client.delete(f"/api/v1/audio-records/{audio_id}")
    assert response.status_code == 409
    assert response.json()["code"] == "STATE_CONFLICT"
