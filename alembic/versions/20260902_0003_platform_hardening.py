"""platform reliability, runtime configuration and integrity hardening

Revision ID: 20260902_0003
Revises: 20260902_0002
Create Date: 2026-09-02 18:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0003"
down_revision: str | Sequence[str] | None = "20260902_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 在建立活动任务唯一索引前，将历史并发重复项安全终止并保留诊断信息。
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY job_type, resource_type, resource_id ORDER BY created_at, id
            ) AS rn
            FROM ai_jobs WHERE status IN ('queued', 'running')
        )
        UPDATE ai_jobs SET status = 'failed', error_code = 'DUPLICATE_ACTIVE_JOB',
            error_detail = 'terminated while adding the active-job uniqueness invariant'
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.execute(
        """
        UPDATE prompt_template_versions AS target SET active = false
        WHERE active = true AND EXISTS (
            SELECT 1 FROM prompt_template_versions AS newer
            WHERE newer.template_code = target.template_code AND newer.active = true
              AND newer.version > target.version
        )
        """
    )
    op.execute(
        """
        UPDATE business_config_versions AS target SET active = false
        WHERE active = true AND EXISTS (
            SELECT 1 FROM business_config_versions AS newer
            WHERE newer.config_code = target.config_code AND newer.active = true
              AND newer.version > target.version
        )
        """
    )

    with op.batch_alter_table("upload_sessions") as batch:
        batch.drop_index("ix_upload_sessions_resource")
        batch.create_unique_constraint(
            "uq_upload_sessions_resource", ["resource_type", "resource_id"]
        )
        batch.create_check_constraint("upload_size_nonnegative", "size_bytes >= 0")
        batch.create_check_constraint(
            "upload_status_valid",
            "status IN ('pending', 'uploaded', 'verified', 'expired', 'failed')",
        )
        batch.create_foreign_key(
            "fk_upload_sessions_organization_unit_id_organization_units",
            "organization_units",
            ["organization_unit_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("idempotency_records") as batch:
        batch.create_check_constraint(
            "idempotency_response_status_valid",
            "response_status IS NULL OR (response_status >= 100 AND response_status <= 599)",
        )
        batch.create_foreign_key(
            "fk_idempotency_records_actor_id_user_accounts",
            "user_accounts",
            ["actor_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("prompt_template_versions") as batch:
        batch.create_check_constraint("prompt_version_positive", "version >= 1")
        batch.create_foreign_key(
            "fk_prompt_template_versions_created_by_user_accounts",
            "user_accounts",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "uq_prompt_template_active",
        "prompt_template_versions",
        ["template_code"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active = 1"),
    )

    with op.batch_alter_table("business_config_versions") as batch:
        batch.create_check_constraint("business_config_version_positive", "version >= 1")
        batch.create_foreign_key(
            "fk_business_config_versions_created_by_user_accounts",
            "user_accounts",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "uq_business_config_active",
        "business_config_versions",
        ["config_code"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active = 1"),
    )

    with op.batch_alter_table("ai_jobs") as batch:
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("worker_id", sa.String(length=200), nullable=True))
        batch.add_column(
            sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False)
        )
        batch.create_check_constraint(
            "ai_job_status_valid",
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        )
        batch.create_check_constraint("ai_job_progress_valid", "progress >= 0 AND progress <= 100")
        batch.create_check_constraint("ai_job_attempt_positive", "attempt >= 1")
        batch.create_check_constraint("ai_job_lock_version_positive", "lock_version >= 1")
        batch.create_foreign_key(
            "fk_ai_jobs_organization_unit_id_organization_units",
            "organization_units",
            ["organization_unit_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_ai_jobs_requested_by_user_accounts",
            "user_accounts",
            ["requested_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "uq_ai_jobs_active_resource",
        "ai_jobs",
        ["job_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )

    with op.batch_alter_table("ai_call_logs") as batch:
        batch.create_check_constraint("ai_call_duration_nonnegative", "duration_ms >= 0")
        batch.create_check_constraint(
            "ai_call_input_nonnegative", "input_units IS NULL OR input_units >= 0"
        )
        batch.create_check_constraint(
            "ai_call_output_nonnegative", "output_units IS NULL OR output_units >= 0"
        )
        batch.create_check_constraint(
            "ai_call_status_valid",
            "status IN ('succeeded', 'failed', 'timeout', 'cancelled')",
        )

    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(
            sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False)
        )
        batch.add_column(
            sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False)
        )
        batch.create_check_constraint("workflow_schema_version_positive", "schema_version >= 1")
        batch.create_check_constraint("workflow_lock_version_positive", "lock_version >= 1")
        batch.create_foreign_key(
            "fk_workflow_runs_organization_unit_id_organization_units",
            "organization_units",
            ["organization_unit_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("audit_logs") as batch:
        batch.add_column(sa.Column("previous_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("entry_hash", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_audit_logs_actor_id_user_accounts",
            "user_accounts",
            ["actor_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_audit_logs_organization_unit_id_organization_units",
            "organization_units",
            ["organization_unit_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    bind = op.get_bind()
    audit = sa.table(
        "audit_logs",
        sa.column("id"),
        sa.column("occurred_at"),
        sa.column("actor_id"),
        sa.column("action"),
        sa.column("resource_type"),
        sa.column("resource_id"),
        sa.column("previous_hash"),
        sa.column("entry_hash"),
    )
    previous: str | None = None
    for row in bind.execute(sa.select(audit).order_by(audit.c.occurred_at, audit.c.id)).mappings():
        canonical = json.dumps(
            {
                key: str(row[key])
                for key in (
                    "id",
                    "occurred_at",
                    "actor_id",
                    "action",
                    "resource_type",
                    "resource_id",
                )
            },
            sort_keys=True,
            separators=(",", ":"),
        ) + (previous or "")
        entry = hashlib.sha256(canonical.encode()).hexdigest()
        bind.execute(
            audit.update()
            .where(audit.c.id == row["id"])
            .values(previous_hash=previous, entry_hash=entry)
        )
        previous = entry
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column("entry_hash", existing_type=sa.String(length=64), nullable=False)
        batch.create_unique_constraint("uq_audit_logs_entry_hash", ["entry_hash"])

    # 旧约束只保证同一 job 内唯一；升级前隔离跨 job 的历史重复操作，
    # 避免它们在新约束建立后被重复发布。
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY operation_key ORDER BY created_at, id
            ) AS rn
            FROM task_outbox
        )
        UPDATE task_outbox
        SET status = 'failed',
            last_error = 'duplicate operation key isolated during platform hardening',
            operation_key = operation_key || ':duplicate:' || CAST(id AS VARCHAR)
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    with op.batch_alter_table("task_outbox") as batch:
        batch.drop_constraint("uq_task_outbox_operation", type_="unique")
        batch.create_unique_constraint("uq_task_outbox_operation", ["operation_key"])
        batch.create_check_constraint(
            "task_outbox_status_valid",
            "status IN ('pending', 'publishing', 'published', 'failed')",
        )
        batch.create_check_constraint("task_outbox_attempts_nonnegative", "publish_attempts >= 0")

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_audit_log_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END; $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER audit_logs_append_only
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation()")
    with op.batch_alter_table("task_outbox") as batch:
        batch.drop_constraint("ck_task_outbox_task_outbox_attempts_nonnegative", type_="check")
        batch.drop_constraint("ck_task_outbox_task_outbox_status_valid", type_="check")
        batch.drop_constraint("uq_task_outbox_operation", type_="unique")
        batch.create_unique_constraint("uq_task_outbox_operation", ["job_id", "operation_key"])
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("uq_audit_logs_entry_hash", type_="unique")
        batch.drop_constraint("fk_audit_logs_actor_id_user_accounts", type_="foreignkey")
        batch.drop_constraint(
            "fk_audit_logs_organization_unit_id_organization_units", type_="foreignkey"
        )
        batch.drop_column("entry_hash")
        batch.drop_column("previous_hash")
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_constraint(
            "fk_workflow_runs_organization_unit_id_organization_units", type_="foreignkey"
        )
        batch.drop_constraint("ck_workflow_runs_workflow_lock_version_positive", type_="check")
        batch.drop_constraint("ck_workflow_runs_workflow_schema_version_positive", type_="check")
        batch.drop_column("lock_version")
        batch.drop_column("schema_version")
    with op.batch_alter_table("ai_call_logs") as batch:
        for name in (
            "ck_ai_call_logs_ai_call_status_valid",
            "ck_ai_call_logs_ai_call_output_nonnegative",
            "ck_ai_call_logs_ai_call_input_nonnegative",
            "ck_ai_call_logs_ai_call_duration_nonnegative",
        ):
            batch.drop_constraint(name, type_="check")
    op.drop_index("uq_ai_jobs_active_resource", table_name="ai_jobs")
    with op.batch_alter_table("ai_jobs") as batch:
        batch.drop_constraint("fk_ai_jobs_requested_by_user_accounts", type_="foreignkey")
        batch.drop_constraint(
            "fk_ai_jobs_organization_unit_id_organization_units", type_="foreignkey"
        )
        for name in (
            "ck_ai_jobs_ai_job_lock_version_positive",
            "ck_ai_jobs_ai_job_attempt_positive",
            "ck_ai_jobs_ai_job_progress_valid",
            "ck_ai_jobs_ai_job_status_valid",
        ):
            batch.drop_constraint(name, type_="check")
        for name in ("lock_version", "worker_id", "heartbeat_at", "completed_at", "started_at"):
            batch.drop_column(name)
    op.drop_index("uq_business_config_active", table_name="business_config_versions")
    with op.batch_alter_table("business_config_versions") as batch:
        batch.drop_constraint(
            "fk_business_config_versions_created_by_user_accounts", type_="foreignkey"
        )
        batch.drop_constraint(
            "ck_business_config_versions_business_config_version_positive", type_="check"
        )
    op.drop_index("uq_prompt_template_active", table_name="prompt_template_versions")
    with op.batch_alter_table("prompt_template_versions") as batch:
        batch.drop_constraint(
            "fk_prompt_template_versions_created_by_user_accounts", type_="foreignkey"
        )
        batch.drop_constraint("ck_prompt_template_versions_prompt_version_positive", type_="check")
    with op.batch_alter_table("idempotency_records") as batch:
        batch.drop_constraint("fk_idempotency_records_actor_id_user_accounts", type_="foreignkey")
        batch.drop_constraint(
            "ck_idempotency_records_idempotency_response_status_valid", type_="check"
        )
    with op.batch_alter_table("upload_sessions") as batch:
        batch.drop_constraint(
            "fk_upload_sessions_organization_unit_id_organization_units", type_="foreignkey"
        )
        batch.drop_constraint("ck_upload_sessions_upload_status_valid", type_="check")
        batch.drop_constraint("ck_upload_sessions_upload_size_nonnegative", type_="check")
        batch.drop_constraint("uq_upload_sessions_resource", type_="unique")
        batch.create_index("ix_upload_sessions_resource", ["resource_type", "resource_id"])
