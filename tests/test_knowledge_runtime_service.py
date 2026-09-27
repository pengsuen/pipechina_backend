import importlib.util
import time
from pathlib import Path

from fastapi.testclient import TestClient


class Vector(list):
    def tolist(self):
        return list(self)


class EmbeddingModel:
    def encode(self, texts, **_kwargs):
        return [Vector([float(len(text)), 1.0]) for text in texts]


class RerankerModel:
    def predict(self, pairs, **_kwargs):
        return [len(query) / len(document) for query, document in pairs]


def test_runtime_serves_parser_ocr_embedding_and_reranker(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-service-key")
    monkeypatch.setenv("PARSER_DATA", str(tmp_path))
    monkeypatch.setenv("KNOWLEDGE_RUNTIME_LOAD_MODELS", "false")
    path = Path(__file__).parents[1] / "services/knowledge_runtime/main.py"
    spec = importlib.util.spec_from_file_location("parser_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/health/live").status_code == 401
        client.headers["Authorization"] = "Bearer test-service-key"
        module.app.state.embedding_model = EmbeddingModel()
        module.app.state.reranker_model = RerankerModel()
        health = client.get("/health/live")
        assert health.status_code == 200
        assert health.json()["components"] == {
            "parser": True,
            "ocr": True,
            "embedding": True,
            "reranker": True,
        }
        embedding = client.post(
            "/v1/embeddings",
            json={"model": module.EMBEDDING_MODEL, "input": ["管道", "知识库"]},
        )
        assert embedding.status_code == 200
        assert embedding.json()["data"] == [
            {"index": 0, "embedding": [2.0, 1.0]},
            {"index": 1, "embedding": [3.0, 1.0]},
        ]
        rerank = client.post(
            "/v1/rerank",
            json={
                "model": module.RERANKER_MODEL,
                "query": "管道",
                "documents": ["管道巡检", "知识库文档"],
            },
        )
        assert rerank.status_code == 200
        assert [row["index"] for row in rerank.json()["results"]] == [0, 1]
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
