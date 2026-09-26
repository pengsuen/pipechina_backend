from pathlib import Path

import yaml

from app.bootstrap.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_compose_only_manages_optional_container_services() -> None:
    compose = yaml.safe_load((ROOT / "infra/compose/project.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"asr", "vision", "object-storage-init"}
    assert services["asr"]["profiles"] == ["local-models"]
    assert services["vision"]["profiles"] == ["local-models"]
    assert services["object-storage-init"]["profiles"] == ["s3-local"]
    assert compose["networks"]["shared-infra"]["external"] is True


def test_dockerfile_references_existing_build_inputs() -> None:
    dockerfile = (ROOT / "infra/docker/backend.Dockerfile").read_text(encoding="utf-8")
    assert (ROOT / "alembic.ini").is_file()
    assert (ROOT / "alembic" / "env.py").is_file()
    assert (ROOT / "alembic" / "versions" / "20260926_0001_initial_schema.py").is_file()
    assert "COPY alembic ./alembic" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
    assert "python:3.14.7-slim-trixie" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "install -d -o app -g app /var/lib/pipechina /var/lib/pipechina/objects" in dockerfile
    assert "python:3.13.15-slim-trixie" in (ROOT / "services" / "asr" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    vision_dockerfile = (ROOT / "services" / "vision" / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.13.15-slim-trixie" in vision_dockerfile
    assert "https://download.pytorch.org/whl/cpu" in vision_dockerfile


def test_compose_build_paths_and_persistent_project_names_survive_move() -> None:
    for filename, name in (
        ("shared.yml", "shared-infra"),
        ("project.yml", "pipechina"),
        ("knowledge.yml", "pipechina-knowledge-infra"),
    ):
        path = ROOT / "infra/compose" / filename
        compose = yaml.safe_load(path.read_text())
        assert compose["name"] == name
        for service in compose["services"].values():
            build = service.get("build")
            if build:
                context = (path.parent / build["context"]).resolve()
                assert context.is_dir()
                assert (context / build.get("dockerfile", "Dockerfile")).is_file()
    for filename in (
        "docker-compose.yml",
        "shared-infra.compose.yml",
        "knowledge-infra.compose.yml",
    ):
        assert not (ROOT / filename).exists()


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

    assert "compose-smoke" not in workflow["jobs"]


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
        ROOT / "infra/compose/project.yml",
        ROOT / ".github" / "workflows" / "ci.yml",
    )
    for path in deployment_files:
        content = path.read_text(encoding="utf-8")
        assert "png_ai" not in content
        assert "POSTGRES_USER: png" not in content
        assert "postgresql+asyncpg://png:" not in content
