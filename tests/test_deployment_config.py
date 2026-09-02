from pathlib import Path

import yaml

from app.bootstrap.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_compose_has_migrations_storage_bootstrap_and_workers() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {
        "postgres",
        "rabbitmq",
        "redis",
        "mock-idp",
        "mock-users",
        "asr",
        "vision",
        "seaweedfs",
        "object-storage-init",
        "migrate",
        "api",
        "worker",
        "beat",
    } <= services.keys()
    assert services["postgres"]["image"] == "postgres:18.6-alpine3.24"
    assert services["postgres"]["volumes"] == ["pg_data:/var/lib/postgresql"]
    assert "ports" not in services["postgres"]
    assert services["rabbitmq"]["image"] == "rabbitmq:4.3.5-management-alpine"
    assert services["rabbitmq"]["volumes"] == ["rabbitmq_data:/var/lib/rabbitmq"]
    assert "ports" not in services["rabbitmq"]
    assert services["redis"]["image"] == "redis:8.10.1-alpine3.23"
    assert "ports" not in services["redis"]
    assert services["asr"]["profiles"] == ["local-models"]
    assert services["vision"]["profiles"] == ["local-models"]
    assert services["seaweedfs"]["profiles"] == ["s3-local"]
    assert services["seaweedfs"]["image"] == "chrislusf/seaweedfs:4.44"
    assert services["seaweedfs"]["healthcheck"]["test"][-1].endswith("/readyz")
    assert services["migrate"]["command"] == "alembic upgrade head"
    assert services["mock-users"]["command"] == "python scripts/bootstrap_mock_idp_users.py"
    assert services["mock-idp"]["environment"]["MOCK_IDP_AUDIENCE"] == "pipechina-backend"
    assert services["api"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["api"]["depends_on"]["mock-idp"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["mock-users"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["api"]["environment"]["JWT_JWKS_URL"] == (
        "http://mock-idp:9001/.well-known/jwks.json"
    )
    assert services["api"]["volumes"] == ["object_data:/var/lib/pipechina/objects"]
    assert services["worker"]["volumes"] == ["object_data:/var/lib/pipechina/objects"]
    assert "audio,ai_text,vision,report,maintenance" in services["worker"]["command"]
    assert "/var/lib/pipechina/celerybeat-schedule" in services["beat"]["command"]
    assert services["beat"]["volumes"] == [
        "beat_data:/var/lib/pipechina",
        "object_data:/var/lib/pipechina/objects",
    ]
    assert "alembic upgrade head" not in services["api"].get("command", "")


def test_dockerfile_references_existing_build_inputs() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (ROOT / "alembic.ini").is_file()
    assert (ROOT / "alembic" / "env.py").is_file()
    assert (ROOT / "alembic" / "versions" / "20260825_0001_initial_v1_3_schema.py").is_file()
    assert "COPY alembic ./alembic" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
    assert "python:3.14.7-slim-trixie" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "install -d -o app -g app /var/lib/pipechina /var/lib/pipechina/objects" in dockerfile
    assert "python:3.13.15-slim-trixie" in (
        ROOT / "inference" / "asr_service" / "Dockerfile"
    ).read_text(encoding="utf-8")
    vision_dockerfile = (ROOT / "inference" / "vision_service" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "python:3.13.15-slim-trixie" in vision_dockerfile
    assert "https://download.pytorch.org/whl/cpu" in vision_dockerfile


def test_ci_uses_python_314_and_live_postgres_18_schema_gate() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["quality-and-postgres"]
    assert job["services"]["postgres"]["image"] == "postgres:18.6-alpine3.24"
    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )
    assert "uv sync --frozen --group dev" in commands
    assert "uv lock --check" in commands
    assert "ruff format --check" in commands
    assert "alembic upgrade head" in commands
    assert "scripts/verify_postgres_schema.py" in commands
    assert "pip-audit --local --skip-editable" in commands

    setup_python = next(
        step for step in job["steps"] if step.get("uses") == "actions/setup-python@v6"
    )
    assert setup_python["with"]["python-version"] == "3.14"

    smoke = workflow["jobs"]["compose-smoke"]
    smoke_commands = "\n".join(
        str(step.get("run", "")) for step in smoke["steps"] if isinstance(step, dict)
    )
    assert "docker compose config --quiet" in smoke_commands
    assert "docker compose up --build --wait" in smoke_commands
    assert "LocalFilesystemStorageProvider" in smoke_commands
    assert "docker compose down --volumes --remove-orphans" in smoke_commands


def test_example_environment_defaults_to_qwen_and_local_adapters() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")
    assert settings.database_url == ("postgresql+asyncpg://peter:123456@127.0.0.1:5432/pipechina")
    assert settings.celery_broker_url == "amqp://peter:123456@127.0.0.1:5672//"
    assert settings.celery_result_backend == "redis://127.0.0.1:6379/1"
    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    assert settings.cors_allowed_origins == ("http://localhost:5173",)
    assert settings.text_provider == "qwen"
    assert settings.asr_provider == "local_http"
    assert settings.vision_provider == "local_http"
    assert settings.storage_provider == "local_filesystem"
    assert settings.qwen_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_deployment_configuration_has_no_legacy_png_database_identity() -> None:
    deployment_files = (
        ROOT / ".env.example",
        ROOT / "docker-compose.yml",
        ROOT / ".github" / "workflows" / "ci.yml",
    )
    for path in deployment_files:
        content = path.read_text(encoding="utf-8")
        assert "png_ai" not in content
        assert "POSTGRES_USER: png" not in content
        assert "postgresql+asyncpg://png:" not in content
