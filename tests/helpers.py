from datetime import date

from fastapi.testclient import TestClient


def create_confirmed_handover(client: TestClient) -> dict:
    created = client.post(
        "/api/v1/audio-records",
        json={
            "shift_date": str(date.today()),
            "shift_code": "night",
            "filename": "night-shift.m4a",
            "size_bytes": 1024,
            "mime_type": "audio/mp4",
        },
    )
    assert created.status_code == 201, created.text
    audio_id = created.json()["audio"]["id"]
    completed = client.post(f"/api/v1/audio-records/{audio_id}/uploads:complete", json={})
    assert completed.status_code == 202, completed.text
    transcribed = client.post(f"/api/v1/audio-records/{audio_id}:transcribe")
    assert transcribed.status_code == 202, transcribed.text
    summarized = client.post(f"/api/v1/audio-records/{audio_id}:summarize")
    assert summarized.status_code == 202, summarized.text
    confirmed = client.post(f"/api/v1/audio-records/{audio_id}:confirm")
    assert confirmed.status_code == 200, confirmed.text
    return {"audio_id": audio_id, "job_id": transcribed.json()["job_id"]}


def create_confirmed_event(client: TestClient) -> dict:
    extracted = client.post(
        "/api/v1/event-extractions",
        json={
            "source_type": "raw_text",
            "raw_text": "凌晨两点发现二号阀门轻微渗漏，已通知维检。",
        },
    )
    assert extracted.status_code == 202, extracted.text
    event_id = extracted.json()["event_ids"][0]
    confirmed = client.post(f"/api/v1/events/{event_id}:confirm")
    assert confirmed.status_code == 200, confirmed.text
    return {"event_id": event_id, "job_id": extracted.json()["job_id"]}
