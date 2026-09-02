"""database-backed identity and authorization

Revision ID: 20260902_0002
Revises: 20260825_0001
Create Date: 2026-09-02 12:00:00.000000
"""

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID, uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0002"
down_revision: str | Sequence[str] | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PERMISSIONS = (
    ("*", "system", "系统全部能力", "critical"),
    ("handover:read", "handover", "查看交接班记录", "normal"),
    ("handover:create", "handover", "创建交接班记录", "normal"),
    ("handover:edit", "handover", "编辑交接班内容", "high"),
    ("handover:process", "handover", "转写和生成交接摘要", "normal"),
    ("handover:confirm", "handover", "确认交接班记录", "high"),
    ("handover:delete", "handover", "删除交接班记录", "high"),
    ("event:read", "event", "查看生产事件", "normal"),
    ("event:extract", "event", "抽取生产事件", "normal"),
    ("event:edit", "event", "编辑生产事件", "high"),
    ("event:review", "event", "审核生产事件", "high"),
    ("event:transform", "event", "重抽取、合并和拆分事件", "high"),
    ("event:classify", "event", "执行异常分级", "high"),
    ("inspection:read", "inspection", "查看巡检记录", "normal"),
    ("inspection:create", "inspection", "创建巡检记录", "normal"),
    ("inspection:upload", "inspection", "上传巡检图片", "normal"),
    ("inspection:analyze", "inspection", "分析巡检图片", "normal"),
    ("inspection:review", "inspection", "审核巡检隐患", "high"),
    ("inspection:link", "inspection", "关联巡检隐患", "high"),
    ("inspection:workflow", "inspection", "启动隐患处置流程", "high"),
    ("inspection:delete", "inspection", "删除巡检图片", "high"),
    ("maintenance:read", "maintenance", "查看维检工单", "normal"),
    ("maintenance:write", "maintenance", "编辑和提交维检工单", "normal"),
    ("maintenance:review", "maintenance", "复核异常分类", "high"),
    ("maintenance:approve", "maintenance", "审批维检工单", "high"),
    ("maintenance:dispatch", "maintenance", "派发维检工单", "high"),
    ("maintenance:execute", "maintenance", "执行和解决维检工单", "normal"),
    ("maintenance:close", "maintenance", "关闭或取消维检工单", "high"),
    ("maintenance:attachment", "maintenance", "上传维检附件", "normal"),
    ("report:read", "report", "查看生产报告", "normal"),
    ("report:create", "report", "创建生产报告", "normal"),
    ("report:generate", "report", "生成生产报告", "normal"),
    ("report:edit", "report", "编辑生产报告", "high"),
    ("report:review", "report", "审核生产报告", "high"),
    ("report:publish", "report", "发布生产报告", "critical"),
    ("report:withdraw", "report", "撤回生产报告", "critical"),
    ("report:export", "report", "导出生产报告", "normal"),
    ("job:read", "job", "查看异步任务", "normal"),
    ("job:cancel", "job", "取消异步任务", "high"),
    ("job:retry", "job", "重试异步任务", "high"),
    ("admin:user", "admin", "管理用户", "critical"),
    ("admin:org", "admin", "管理组织", "critical"),
    ("admin:role", "admin", "管理角色", "critical"),
    ("admin:permission", "admin", "分配和撤销权限", "critical"),
    ("admin:audit", "admin", "查看安全审计", "high"),
    ("admin:model", "admin", "管理模型配置", "critical"),
    ("admin:prompt", "admin", "管理提示词", "critical"),
    ("admin:config", "admin", "管理业务配置", "critical"),
)


def _json_value(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


def upgrade() -> None:
    with op.batch_alter_table("user_accounts") as batch:
        batch.add_column(
            sa.Column(
                "external_issuer",
                sa.String(length=300),
                server_default="legacy",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "authz_version",
                sa.Integer(),
                server_default="1",
                nullable=False,
            )
        )
        batch.drop_constraint("uq_user_accounts_external_subject", type_="unique")
        batch.create_unique_constraint(
            "uq_user_accounts_external_identity",
            ["external_issuer", "external_subject"],
        )
        batch.create_index(
            "ix_user_accounts_organization_active",
            ["organization_unit_id", "active"],
            unique=False,
        )

    with op.batch_alter_table("user_accounts") as batch:
        batch.alter_column("external_issuer", server_default=None)
        batch.alter_column("authz_version", server_default=None)

    with op.batch_alter_table("role_definitions") as batch:
        batch.add_column(
            sa.Column(
                "active",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )

    with op.batch_alter_table("role_definitions") as batch:
        batch.alter_column("active", server_default=None)

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE role_assignments
            SET assigned_by = NULL
            WHERE assigned_by IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM user_accounts WHERE user_accounts.id = role_assignments.assigned_by
              )
            """
        )
    )
    with op.batch_alter_table("role_assignments") as batch:
        batch.drop_constraint("uq_role_assignment_scope", type_="unique")
        batch.add_column(
            sa.Column("scope_type", sa.String(length=32), server_default="own_org", nullable=False)
        )
        batch.add_column(sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("grant_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("revoke_reason", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_role_assignments_assigned_by_user_accounts",
            "user_accounts",
            ["assigned_by"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_role_assignments_revoked_by_user_accounts",
            "user_accounts",
            ["revoked_by"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_role_assignments_validity",
            ["active", "effective_from", "expires_at"],
            unique=False,
        )

    with op.batch_alter_table("role_assignments") as batch:
        batch.alter_column("scope_type", server_default=None)

    assignment_table = sa.table(
        "role_assignments",
        sa.column("id", sa.Uuid()),
        sa.column("data_scope", sa.JSON()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_digest", sa.String()),
    )
    assignment_rows = bind.execute(
        sa.select(assignment_table.c.id, assignment_table.c.data_scope)
    ).all()
    valid_scope_types = {
        "own_org",
        "org_and_descendants",
        "custom_orgs",
        "global",
        "owned",
        "assigned",
    }
    for assignment_id, raw_scope in assignment_rows:
        scope_document = _json_value(raw_scope)
        scope_type = (
            scope_document.get("type", "own_org") if isinstance(scope_document, dict) else "own_org"
        )
        if scope_type not in valid_scope_types:
            scope_type = (
                "custom_orgs"
                if isinstance(scope_document, dict) and scope_document.get("organization_unit_ids")
                else "own_org"
            )
        raw_organization_ids = (
            scope_document.get("organization_unit_ids", [])
            if isinstance(scope_document, dict)
            else []
        )
        organization_ids = sorted({str(item) for item in raw_organization_ids})
        if scope_type in {"own_org", "global", "owned", "assigned"}:
            organization_ids = []
        if scope_type == "custom_orgs" and not organization_ids:
            scope_type = "own_org"
        normalized_scope = {
            "type": scope_type,
            "organization_unit_ids": organization_ids,
        }
        normalized_scope_json = json.dumps(
            normalized_scope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        bind.execute(
            assignment_table.update()
            .where(assignment_table.c.id == assignment_id)
            .values(
                scope_type=scope_type,
                data_scope=normalized_scope,
                scope_digest=hashlib.sha256(normalized_scope_json.encode()).hexdigest(),
            )
        )

    op.create_table(
        "permission_definitions",
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("module", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_permission_definitions"),
        sa.UniqueConstraint("code", name="uq_permission_definitions_code"),
    )
    op.create_index(
        "ix_permission_definitions_module",
        "permission_definitions",
        ["module"],
        unique=False,
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission_definitions.id"],
            name="fk_role_permissions_permission_id_permission_definitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role_definitions.id"],
            name="fk_role_permissions_role_id_role_definitions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_permissions"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"], unique=False)
    op.create_index(
        "ix_role_permissions_permission_id",
        "role_permissions",
        ["permission_id"],
        unique=False,
    )

    legacy_rows = bind.execute(sa.text("SELECT id, permissions FROM role_definitions")).all()
    normalized_legacy_rows: list[tuple[UUID, set[str]]] = []
    catalog_codes = {item[0] for item in PERMISSIONS}
    legacy_only_codes: set[str] = set()
    for role_id, raw_permissions in legacy_rows:
        parsed_permissions = _json_value(raw_permissions)
        permission_codes = (
            {str(code) for code in parsed_permissions}
            if isinstance(parsed_permissions, list)
            else set()
        )
        normalized_legacy_rows.append((UUID(str(role_id)), permission_codes))
        legacy_only_codes.update(permission_codes - catalog_codes)

    permission_ids: dict[str, UUID] = {}
    permission_table = sa.table(
        "permission_definitions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("module", sa.String()),
        sa.column("name", sa.String()),
        sa.column("risk_level", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    records = []
    for code, module, name, risk_level in PERMISSIONS:
        permission_id = uuid4()
        permission_ids[code] = permission_id
        records.append(
            {
                "id": permission_id,
                "code": code,
                "module": module,
                "name": name,
                "risk_level": risk_level,
                "active": True,
            }
        )
    for code in sorted(legacy_only_codes):
        permission_id = uuid4()
        permission_ids[code] = permission_id
        records.append(
            {
                "id": permission_id,
                "code": code,
                "module": "legacy",
                "name": code,
                "risk_level": "high",
                "active": True,
            }
        )
    op.bulk_insert(permission_table, records)

    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )
    for role_id, legacy_permissions in normalized_legacy_rows:
        for code in sorted(legacy_permissions):
            bind.execute(
                role_permission_table.insert().values(
                    id=uuid4(),
                    role_id=role_id,
                    permission_id=permission_ids[code],
                )
            )


def downgrade() -> None:
    op.drop_index("ix_role_permissions_permission_id", table_name="role_permissions")
    op.drop_index("ix_role_permissions_role_id", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("ix_permission_definitions_module", table_name="permission_definitions")
    op.drop_table("permission_definitions")

    with op.batch_alter_table("role_assignments") as batch:
        batch.drop_index("ix_role_assignments_validity")
        batch.drop_constraint("fk_role_assignments_revoked_by_user_accounts", type_="foreignkey")
        batch.drop_constraint("fk_role_assignments_assigned_by_user_accounts", type_="foreignkey")
        batch.drop_column("revoke_reason")
        batch.drop_column("revoked_by")
        batch.drop_column("revoked_at")
        batch.drop_column("grant_reason")
        batch.drop_column("expires_at")
        batch.drop_column("effective_from")
        batch.drop_column("scope_type")
        batch.create_unique_constraint(
            "uq_role_assignment_scope",
            ["user_id", "role_id", "scope_digest"],
        )

    with op.batch_alter_table("role_definitions") as batch:
        batch.drop_column("active")

    with op.batch_alter_table("user_accounts") as batch:
        batch.drop_index("ix_user_accounts_organization_active")
        batch.drop_constraint("uq_user_accounts_external_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_user_accounts_external_subject",
            ["external_subject"],
        )
        batch.drop_column("authz_version")
        batch.drop_column("external_issuer")
