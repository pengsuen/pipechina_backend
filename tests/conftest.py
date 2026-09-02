import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.request import urlopen

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.bootstrap.config import Settings
from app.main import create_app
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.scopes import ScopeType
from dev.mock_idp.main import MockIdPSettings, create_mock_idp_app
from tests.security_helpers import authenticated_client


@pytest.fixture(scope="session")
def mock_idp_url() -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    app = create_mock_idp_app(MockIdPSettings(issuer=base_url, audience="pipechina-backend"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while True:
        try:
            with urlopen(f"{base_url}/health/live", timeout=0.2) as response:  # noqa: S310
                if response.status == 200:
                    break
        except OSError:
            if time.monotonic() >= deadline:
                server.should_exit = True
                thread.join(timeout=2)
                raise RuntimeError("Mock IdP failed to start") from None
            time.sleep(0.02)
    yield base_url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def settings(tmp_path: Path, mock_idp_url: str) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        jwt_issuer=mock_idp_url,
        jwt_audience="pipechina-backend",
        jwt_algorithm="RS256",
        jwt_jwks_url=f"{mock_idp_url}/.well-known/jwks.json",
        auto_create_schema=True,
        run_tasks_inline=True,
        text_provider="fake",
        asr_provider="fake",
        vision_provider="fake",
        storage_provider="memory",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with authenticated_client(
        settings,
        username="admin",
        permissions={Permissions.ALL},
        scope_type=ScopeType.GLOBAL,
    ) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
