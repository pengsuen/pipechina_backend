"""Exercise the real MCP wire endpoint and its business authorization boundary."""

from __future__ import annotations

import json
from functools import partial
from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap.config import Settings
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.scopes import ScopeType
from tests.helpers import create_confirmed_event
from tests.security_helpers import provision_test_identity, request_mock_token


def _mcp(client: TestClient, method: str, params: dict | None = None) -> dict:
    response = client.post(
        "/mcp/",
        headers={
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
            **({"Mcp-Name": str(params["name"])} if method == "tools/call" and params else {}),
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                **(params or {}),
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mcp_requires_bearer_before_discovery(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        "/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_mcp_lists_only_read_tools_and_calls_with_scope(client: TestClient) -> None:
    first = create_confirmed_event(client)["event_id"]
    second = create_confirmed_event(client)["event_id"]
    title = client.get(f"/api/v1/events/{first}").json()["title"]

    listed = _mcp(client, "tools/list")
    tools = listed["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "search_events",
        "search_work_orders",
        "search_handover",
    }
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    assert all("event_id" in tool["inputSchema"]["required"] for tool in tools)

    result = _mcp(
        client,
        "tools/call",
        {"name": "search_events", "arguments": {"event_id": second, "query": title}},
    )
    assert "error" not in result, result
    assert result["result"]["isError"] is False
    assert first in json.dumps(result["result"])
    assert second not in json.dumps(result["result"])

    denied = _mcp(
        client,
        "tools/call",
        {"name": "search_events", "arguments": {"event_id": str(uuid4()), "query": first}},
    )
    assert denied["result"]["isError"] is True


def test_mcp_rechecks_database_permissions_for_each_call(
    client: TestClient, settings: Settings
) -> None:
    event_id = create_confirmed_event(client)["event_id"]
    assert client.portal is not None
    client.portal.call(
        partial(
            provision_test_identity,
            client.app,
            settings,
            username="peter",
            permissions={Permissions.MAINTENANCE_READ},
            scope_type=ScopeType.OWN_ORG,
        )
    )
    client.headers["Authorization"] = f"Bearer {request_mock_token(settings, username='peter')}"
    result = _mcp(
        client,
        "tools/call",
        {"name": "search_work_orders", "arguments": {"event_id": event_id, "query": "维检"}},
    )
    assert result["result"]["isError"] is True
