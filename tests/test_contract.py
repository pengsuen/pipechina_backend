import re

from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.dialects import postgresql, sqlite

from app.shared.db import Base


def normalize(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


def test_contract_has_documented_access_control_operations_and_39_tables(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    operations = {
        (method.upper(), normalize(path))
        for path, item in schema["paths"].items()
        for method in item
        if method in http_methods and method not in {"head", "options"}
    }
    assert len(operations) == 98
    assert len(Base.metadata.tables) == 39

    required = {
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/audio-records/{id}:transcribe"),
        ("POST", "/api/v1/events/{id}:classify"),
        ("POST", "/api/v1/workflows/{id}:review"),
        ("POST", "/api/v1/inspections/{id}:analyze"),
        ("POST", "/api/v1/reports/{id}:publish"),
        ("GET", "/api/v1/reports/{id}/exports/{id}"),
        ("GET", "/api/v1/admin/access/permissions"),
        ("POST", "/api/v1/admin/access/role-assignments"),
        ("GET", "/api/v1/admin/access/users/{id}/effective-access"),
    }
    assert required <= operations


def test_openapi_contains_business_tags(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/audio-records" in paths
    assert "/api/v1/work-orders/{order_id}:dispatch" in paths
    assert "/api/v1/reports/{report_id}:withdraw" in paths


def test_json_documents_use_jsonb_on_postgres_and_json_on_sqlite() -> None:
    columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSON)
    ]
    assert len(columns) == 22
    assert {column.type.compile(dialect=postgresql.dialect()) for column in columns} == {"JSONB"}
    assert {column.type.compile(dialect=sqlite.dialect()) for column in columns} == {"JSON"}
