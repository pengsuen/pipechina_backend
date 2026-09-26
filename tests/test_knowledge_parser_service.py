import importlib.util
import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_parser_durable_job_and_idempotency(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-service-key")
    monkeypatch.setenv("PARSER_DATA", str(tmp_path))
    path = Path(__file__).parents[1] / "services/knowledge_parser/main.py"
    spec = importlib.util.spec_from_file_location("parser_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/health/live").status_code == 401
        client.headers["Authorization"] = "Bearer test-service-key"
        response = client.post(
            "/v1/parse-jobs",
            headers={"Idempotency-Key": "version-1"},
            files={"file": ("document.txt", "# 标题\n\n演示设备资料。".encode(), "text/plain")},
            data={"max_pages": "10"},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        for _ in range(50):
            result = client.get(f"/v1/parse-jobs/{job_id}").json()
            if result["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert result["status"] == "succeeded", result
        assert result["blocks"][0]["kind"] == "heading"
        response = client.post(
            "/v1/parse-jobs",
            headers={"Idempotency-Key": "version-1"},
            files={"file": ("changed.txt", b"changed", "text/plain")},
            data={"max_pages": "10"},
        )
        assert response.status_code == 409
    with TestClient(module.app, headers={"Authorization": "Bearer test-service-key"}) as client:
        assert client.get(f"/v1/parse-jobs/{job_id}").json()["status"] == "succeeded"
