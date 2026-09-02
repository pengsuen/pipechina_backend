from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 集中定义应用配置；默认从项目根目录的.env读取，环境变量可以覆盖同名配置。


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "国家油气管网智能生产运营辅助系统"
    api_prefix: str = "/api/v1"
    cors_allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
    database_url: str = "sqlite+aiosqlite:///./data/development.db"

    jwt_issuer: str = "http://mock-idp:9001"
    jwt_audience: str = "pipechina-backend"
    jwt_algorithm: Literal["RS256", "ES256"] = "RS256"
    jwt_jwks_url: str = "http://127.0.0.1:9001/.well-known/jwks.json"
    jwt_jwks_cache_seconds: int = Field(default=300, ge=30, le=3600)
    jwt_jwks_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    auto_create_schema: bool = False
    auto_seed: bool = False
    run_tasks_inline: bool = False

    text_provider: Literal["fake", "qwen", "openai", "custom_http"] = "qwen"
    asr_provider: Literal["fake", "local_http", "openai", "custom_http"] = "local_http"
    vision_provider: Literal["fake", "local_http", "custom_http"] = "local_http"
    storage_provider: Literal["memory", "local_filesystem", "s3"] = "local_filesystem"

    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/1"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = "pipechina-private"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = True
    s3_addressing_style: Literal["auto", "path", "virtual"] = "auto"

    local_storage_root: Path = Path("./data/objects")
    local_storage_public_base_url: str = "http://localhost:8000"
    local_storage_signing_secret: SecretStr = SecretStr(
        "development-local-storage-secret-change-me"
    )

    dashscope_api_key: SecretStr | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_text_model: str = "qwen3.7-plus"

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_text_model: str = "gpt-5.5"
    openai_asr_model: str = "gpt-4o-transcribe"

    custom_text_base_url: str = "http://127.0.0.1:8201/v1"
    custom_text_model: str = "custom-text-model"
    custom_text_api_key: SecretStr | None = None

    asr_base_url: str = "http://127.0.0.1:8101"
    asr_model: str = "large-v3"
    asr_api_key: SecretStr | None = None
    vision_base_url: str = "http://127.0.0.1:8102"
    vision_model: str = "openbmb/MiniCPM-V-4.6"
    vision_api_key: SecretStr | None = None
    provider_timeout_seconds: float = 60.0
    media_provider_timeout_seconds: float = 900.0
    provider_max_retries: int = Field(default=2, ge=0, le=5)
    provider_trust_env: bool = False
    job_heartbeat_timeout_seconds: int = Field(default=1800, ge=60, le=86400)
    outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    outbox_base_retry_seconds: int = Field(default=5, ge=1, le=3600)

    max_audio_bytes: int = 500 * 1024 * 1024
    max_image_bytes: int = 20 * 1024 * 1024
    allowed_image_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.app_env != "production":
            return self
        if not self.jwt_issuer.startswith("https://"):
            raise ValueError("production JWT_ISSUER must use HTTPS")
        if not self.jwt_jwks_url.startswith("https://"):
            raise ValueError("production JWT_JWKS_URL must use HTTPS")
        if "*" in self.cors_allowed_origins:
            raise ValueError("production CORS_ALLOWED_ORIGINS must not contain a wildcard")
        if self.auto_create_schema:
            raise ValueError("AUTO_CREATE_SCHEMA must be false in production; use Alembic")
        if not self.database_url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
            raise ValueError("production DATABASE_URL must use PostgreSQL")
        if self.text_provider == "fake":
            raise ValueError("production TEXT_PROVIDER must not be fake")
        if self.text_provider == "qwen":
            if self.dashscope_api_key is None or not self.dashscope_api_key.get_secret_value():
                raise ValueError("DASHSCOPE_API_KEY is required for the Qwen text provider")
            if not self.qwen_base_url.startswith("https://"):
                raise ValueError("production QWEN_BASE_URL must use HTTPS")
        if self.text_provider == "openai":
            if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
                raise ValueError("OPENAI_API_KEY is required for the OpenAI text provider")
            if not self.openai_base_url.startswith("https://"):
                raise ValueError("production OPENAI_BASE_URL must use HTTPS")
        if self.asr_provider == "fake" or self.vision_provider == "fake":
            raise ValueError("production media providers must not be fake")
        if self.storage_provider == "memory":
            raise ValueError("production STORAGE_PROVIDER must not be memory")
        if self.storage_provider == "local_filesystem":
            if len(self.local_storage_signing_secret.get_secret_value()) < 32:
                raise ValueError("production LOCAL_STORAGE_SIGNING_SECRET is too short")
            if not self.local_storage_public_base_url.startswith("https://"):
                raise ValueError("production LOCAL_STORAGE_PUBLIC_BASE_URL must use HTTPS")
        if self.s3_endpoint_url and not self.s3_use_ssl:
            raise ValueError("production custom S3 endpoints must enable TLS")
        if self.s3_public_endpoint_url and not self.s3_public_endpoint_url.startswith("https://"):
            raise ValueError("production public S3 endpoints must use HTTPS")
        return self


@lru_cache(maxsize=1)  # 如果之前有创建过settings对象就缓存并使用它，没有就建立
def get_settings() -> Settings:
    return Settings()
