"""Initial 17-table core schema and roles dictionary seed.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import conv

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all 17 core tables in physical topological order 1..17 and seed roles."""
    # 1. maintenance_regions (⑧)
    op.create_table(
        "maintenance_regions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("region_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("parent_region_id", sa.BigInteger(), nullable=True),
        sa.Column("level", sa.SmallInteger(), server_default=sa.text("3"), nullable=False),
        sa.Column("boundary_geojson", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level BETWEEN 1 AND 3",
            name=conv("ck_maintenance_regions_level_range"),
        ),
        sa.CheckConstraint(
            "parent_region_id IS NULL OR parent_region_id <> id",
            name=conv("ck_maintenance_regions_no_self_parent"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_region_id"],
            ["maintenance_regions.id"],
            name="fk_maintenance_regions_parent_region_id_maintenance_regions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_maintenance_regions"),
        sa.UniqueConstraint("region_code", name="uq_maintenance_regions_region_code"),
    )
    op.create_index(
        "ix_maintenance_regions_parent_region_id",
        "maintenance_regions",
        ["parent_region_id"],
        unique=False,
    )

    # 2. roles (②)
    roles_table = op.create_table(
        "roles",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code IN ('ADMIN', 'INSPECTOR', 'TECHNICIAN')",
            name=conv("ck_roles_code_enum"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("code", name="uq_roles_code"),
    )
    op.bulk_insert(
        roles_table,
        [
            {
                "code": "ADMIN",
                "name": "运维管理员",
                "description": (
                    "区域/点位/资产/规则配置、分级派工、超时改派、返修强制复核、审计与通知"
                ),
            },
            {
                "code": "INSPECTOR",
                "name": "巡查员",
                "description": (
                    "点位巡查、静态图片上传与 AI 候选确认、人工报障、误报撤销、维修后验收"
                ),
            },
            {
                "code": "TECHNICIAN",
                "name": "维修人员",
                "description": "接单/拒单、到场、电气安全检查、维修与耗材登记、完成提交",
            },
        ],
    )

    # 3. lighting_points (⑨)
    op.create_table(
        "lighting_points",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("point_code", sa.String(length=64), nullable=False),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("road_name", sa.String(length=128), nullable=False),
        sa.Column(
            "road_level",
            sa.String(length=16),
            server_default=sa.text("'BRANCH'"),
            nullable=False,
        ),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=False),
        sa.Column("install_height_m", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("lamp_count", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "power_supply_type",
            sa.String(length=16),
            server_default=sa.text("'GRID'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'IN_SERVICE'"),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "road_level IN ('EXPRESS', 'MAIN', 'BRANCH')",
            name=conv("ck_lighting_points_road_level_enum"),
        ),
        sa.CheckConstraint(
            "status IN ('IN_SERVICE', 'SUSPENDED', 'RETIRED')",
            name=conv("ck_lighting_points_status_enum"),
        ),
        sa.CheckConstraint(
            "power_supply_type IN ('GRID', 'SOLAR', 'OTHER')",
            name=conv("ck_lighting_points_power_supply_type_enum"),
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name=conv("ck_lighting_points_longitude_range"),
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name=conv("ck_lighting_points_latitude_range"),
        ),
        sa.CheckConstraint(
            "lamp_count >= 1",
            name=conv("ck_lighting_points_lamp_count_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["maintenance_regions.id"],
            name="fk_lighting_points_region_id_maintenance_regions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_lighting_points"),
        sa.UniqueConstraint("point_code", name="uq_lighting_points_point_code"),
    )
    op.create_index(
        "ix_lighting_points_region_id_status",
        "lighting_points",
        ["region_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_lighting_points_road_name",
        "lighting_points",
        ["road_name"],
        unique=False,
    )

    # 4. accounts (①)
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=64), nullable=False),
        sa.Column("employee_no", sa.String(length=32), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "skills",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "max_active_dispatch",
            sa.SmallInteger(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("manager_account_id", sa.BigInteger(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED', 'LOCKED')",
            name=conv("ck_accounts_status_enum"),
        ),
        sa.CheckConstraint(
            "token_version >= 1",
            name=conv("ck_accounts_token_version_positive"),
        ),
        sa.CheckConstraint(
            "max_active_dispatch BETWEEN 1 AND 20",
            name=conv("ck_accounts_max_active_dispatch_range"),
        ),
        sa.CheckConstraint(
            "username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'",
            name=conv("ck_accounts_username_format"),
        ),
        sa.ForeignKeyConstraint(
            ["manager_account_id"],
            ["accounts.id"],
            name="fk_accounts_manager_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("username", name="uq_accounts_username"),
        sa.UniqueConstraint("employee_no", name="uq_accounts_employee_no"),
    )

    # 5. account_roles (③)
    op.create_table(
        "account_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.SmallInteger(), nullable=False),
        sa.Column("granted_by_account_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_roles_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_account_roles_role_id_roles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"],
            ["accounts.id"],
            name="fk_account_roles_granted_by_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_roles"),
        sa.UniqueConstraint("account_id", name="uq_account_roles_account_id"),
        sa.UniqueConstraint("account_id", "role_id", name="uq_account_roles_account_id_role_id"),
    )
    op.create_index(
        "ix_account_roles_role_id",
        "account_roles",
        ["role_id"],
        unique=False,
    )

    # 6. scope_authorizations (④)
    op.create_table(
        "scope_authorizations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by_account_id", sa.BigInteger(), nullable=True),
        sa.Column("remark", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope_type IN ('INSPECT', 'REPAIR', 'MANAGE')",
            name=conv("ck_scope_authorizations_scope_type_enum"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=conv("ck_scope_authorizations_valid_window"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_scope_authorizations_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["maintenance_regions.id"],
            name="fk_scope_authorizations_region_id_maintenance_regions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"],
            ["accounts.id"],
            name="fk_scope_authorizations_granted_by_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scope_authorizations"),
    )
    op.create_index(
        "ix_scope_authorizations_region_type_active",
        "scope_authorizations",
        ["region_id", "scope_type", "is_active"],
        unique=False,
    )

    # 7. business_rules (⑤)
    op.create_table(
        "business_rules",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rule_code", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("rule_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_account_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=conv("ck_business_rules_version_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rule_value) = 'object'",
            name=conv("ck_business_rules_rule_value_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            ["accounts.id"],
            name="fk_business_rules_created_by_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_rules"),
        sa.UniqueConstraint("rule_code", "version", name="uq_business_rules_code_version"),
    )

    # 8. facility_assets (⑩)
    op.create_table(
        "facility_assets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("asset_code", sa.String(length=64), nullable=False),
        sa.Column("lighting_point_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_type", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("model_spec", sa.String(length=128), nullable=True),
        sa.Column("rated_power_w", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column(
            "importance",
            sa.String(length=16),
            server_default=sa.text("'NORMAL'"),
            nullable=False,
        ),
        sa.Column("installed_at", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'IN_SERVICE'"),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset_type IN ('LUMINAIRE', 'POLE', 'BRACKET', 'CONTROL_BOX', 'CABLE', 'OTHER')",
            name=conv("ck_facility_assets_asset_type_enum"),
        ),
        sa.CheckConstraint(
            "importance IN ('CRITICAL', 'HIGH', 'NORMAL')",
            name=conv("ck_facility_assets_importance_enum"),
        ),
        sa.CheckConstraint(
            "status IN ('IN_SERVICE', 'FAULTY', 'REPAIRING', 'RETIRED')",
            name=conv("ck_facility_assets_status_enum"),
        ),
        sa.CheckConstraint(
            "rated_power_w IS NULL OR rated_power_w >= 0",
            name=conv("ck_facility_assets_rated_power_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["lighting_point_id"],
            ["lighting_points.id"],
            name="fk_facility_assets_lighting_point_id_lighting_points",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_facility_assets"),
        sa.UniqueConstraint("asset_code", name="uq_facility_assets_asset_code"),
    )
    op.create_index(
        "ix_facility_assets_lighting_point_id",
        "facility_assets",
        ["lighting_point_id"],
        unique=False,
    )

    # 9. fault_tickets (⑪)
    op.create_table(
        "fault_tickets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_no", sa.String(length=32), nullable=False),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("lighting_point_id", sa.BigInteger(), nullable=False),
        sa.Column("facility_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("report_source", sa.String(length=24), nullable=False),
        sa.Column("fault_type", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=4), nullable=True),
        sa.Column("grading_score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("grading_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reporter_account_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "confirmed_by_inspector",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("confirmed_by_inspector_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_suggestion_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_max_confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("response_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repair_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_sla_overdue",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("overdue_stage", sa.String(length=16), nullable=True),
        sa.Column("overdue_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rework_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "dispatch_reject_count",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("manual_review_reason", sa.String(length=256), nullable=True),
        sa.Column("cancel_reason", sa.String(length=256), nullable=True),
        sa.Column("cancelled_by_account_id", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "report_source IN ('AI_CANDIDATE', 'MANUAL_INSPECT', 'MANUAL_ADMIN')",
            name=conv("ck_fault_tickets_report_source_enum"),
        ),
        sa.CheckConstraint(
            "fault_type IN "
            "('LAMP_OUT', 'FIXTURE_DAMAGED', 'OCCLUDED', "
            "'ELECTRIC_HAZARD', 'FLICKERING', 'OTHER')",
            name=conv("ck_fault_tickets_fault_type_enum"),
        ),
        sa.CheckConstraint(
            "status IN "
            "('AI_CANDIDATE_PENDING', 'PENDING_DISPATCH', 'DISPATCHED', 'IN_REPAIR', "
            "'PENDING_ACCEPTANCE', 'REWORK_PENDING_DISPATCH', 'MANUAL_REVIEW', "
            "'CLOSED', 'CANCELLED')",
            name=conv("ck_fault_tickets_status_enum"),
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('P1', 'P2', 'P3', 'P4')",
            name=conv("ck_fault_tickets_priority_enum"),
        ),
        sa.CheckConstraint(
            "rework_count >= 0 AND dispatch_reject_count >= 0",
            name=conv("ck_fault_tickets_counters_non_negative"),
        ),
        sa.CheckConstraint(
            "grading_score IS NULL OR (grading_score >= 0 AND grading_score <= 100)",
            name=conv("ck_fault_tickets_grading_score_range"),
        ),
        sa.CheckConstraint(
            "ai_max_confidence IS NULL OR (ai_max_confidence >= 0 AND ai_max_confidence <= 1)",
            name=conv("ck_fault_tickets_ai_max_confidence_range"),
        ),
        sa.CheckConstraint(
            "(confirmed_by_inspector = FALSE "
            "AND confirmed_by_inspector_id IS NULL "
            "AND confirmed_at IS NULL "
            "AND status = 'AI_CANDIDATE_PENDING') "
            "OR (confirmed_by_inspector = TRUE "
            "AND confirmed_by_inspector_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL "
            "AND status <> 'AI_CANDIDATE_PENDING')",
            name=conv("ck_fault_tickets_inspector_confirmation_serialized"),
        ),
        sa.CheckConstraint(
            "NOT (report_source = 'AI_CANDIDATE' "
            "AND fault_type IN ('FLICKERING', 'ELECTRIC_HAZARD'))",
            name=conv("ck_fault_tickets_ai_source_excludes_unsupported_types"),
        ),
        sa.CheckConstraint(
            "status <> 'MANUAL_REVIEW' OR manual_review_reason IS NOT NULL",
            name=conv("ck_fault_tickets_manual_review_reason_required"),
        ),
        sa.CheckConstraint(
            "(status = 'CANCELLED' AND cancel_reason IS NOT NULL "
            "AND cancelled_by_account_id IS NOT NULL AND cancelled_at IS NOT NULL) "
            "OR (status <> 'CANCELLED' AND cancel_reason IS NULL "
            "AND cancelled_by_account_id IS NULL AND cancelled_at IS NULL)",
            name=conv("ck_fault_tickets_cancel_fields_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL)",
            name=conv("ck_fault_tickets_closed_at_consistent"),
        ),
        sa.CheckConstraint(
            "(is_sla_overdue = TRUE AND overdue_stage IS NOT NULL "
            "AND overdue_marked_at IS NOT NULL) "
            "OR (is_sla_overdue = FALSE AND overdue_stage IS NULL AND overdue_marked_at IS NULL)",
            name=conv("ck_fault_tickets_overdue_consistent"),
        ),
        sa.CheckConstraint(
            "overdue_stage IS NULL OR overdue_stage IN ('RESPONSE', 'REPAIR')",
            name=conv("ck_fault_tickets_overdue_stage_enum"),
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["maintenance_regions.id"],
            name="fk_fault_tickets_region_id_maintenance_regions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lighting_point_id"],
            ["lighting_points.id"],
            name="fk_fault_tickets_lighting_point_id_lighting_points",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_asset_id"],
            ["facility_assets.id"],
            name="fk_fault_tickets_facility_asset_id_facility_assets",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_account_id"],
            ["accounts.id"],
            name="fk_fault_tickets_reporter_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_inspector_id"],
            ["accounts.id"],
            name="fk_fault_tickets_confirmed_by_inspector_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_account_id"],
            ["accounts.id"],
            name="fk_fault_tickets_cancelled_by_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fault_tickets"),
        sa.UniqueConstraint("ticket_no", name="uq_fault_tickets_ticket_no"),
    )
    op.create_index(
        "ix_fault_tickets_lighting_point_id",
        "fault_tickets",
        ["lighting_point_id"],
        unique=False,
    )
    op.create_index(
        "ix_fault_tickets_reporter_account_id",
        "fault_tickets",
        ["reporter_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_fault_tickets_created_at",
        "fault_tickets",
        [sa.text("created_at DESC")],
        unique=False,
    )

    # 10. fault_evidences (⑫)
    op.create_table(
        "fault_evidences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_type", sa.String(length=24), nullable=False),
        sa.Column("media_mime", sa.String(length=32), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.Column("ai_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "ai_is_baseline_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("uploaded_by_account_id", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_type IN "
            "('INSPECTION_PHOTO', 'ANNOTATED_PHOTO', 'REPAIR_PHOTO', 'ACCEPTANCE_PHOTO')",
            name=conv("ck_fault_evidences_evidence_type_enum"),
        ),
        sa.CheckConstraint(
            "media_mime IN ('image/jpeg', 'image/png', 'image/webp')",
            name=conv("ck_fault_evidences_media_mime_static_image_only"),
        ),
        sa.CheckConstraint(
            "file_size_bytes > 0 AND file_size_bytes <= 15728640",
            name=conv("ck_fault_evidences_file_size_range"),
        ),
        sa.CheckConstraint(
            "image_width > 0 AND image_height > 0 "
            "AND image_width <= 20000 AND image_height <= 20000",
            name=conv("ck_fault_evidences_image_dimensions_positive"),
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name=conv("ck_fault_evidences_sha256_format"),
        ),
        sa.CheckConstraint(
            "ticket_id IS NOT NULL "
            "OR (evidence_type = 'INSPECTION_PHOTO' AND analysis_id IS NOT NULL)",
            name=conv("ck_fault_evidences_ticket_or_orphan_inspection"),
        ),
        sa.CheckConstraint(
            "ai_is_baseline_mode = FALSE "
            "OR ai_result_json IS NULL "
            "OR coalesce((ai_result_json->>'max_fused_confidence')::numeric, 0) <= 0.65",
            name=conv("ck_fault_evidences_baseline_confidence_cap"),
        ),
        sa.CheckConstraint(
            "ai_result_json IS NULL "
            "OR ((ai_result_json->>'requires_inspector_confirmation')::boolean IS TRUE "
            "AND (ai_result_json->>'replaces_electrical_safety_check')::boolean IS FALSE)",
            name=conv("ck_fault_evidences_ai_safety_flags_required"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["fault_tickets.id"],
            name="fk_fault_evidences_ticket_id_fault_tickets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_account_id"],
            ["accounts.id"],
            name="fk_fault_evidences_uploaded_by_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fault_evidences"),
        sa.UniqueConstraint(
            "ticket_id",
            "sha256",
            name="uq_fault_evidences_sha256_per_ticket",
        ),
    )
    op.create_index(
        "ix_fault_evidences_ticket_id_type",
        "fault_evidences",
        ["ticket_id", "evidence_type"],
        unique=False,
    )

    # 11. dispatch_records (⑬) — rework_order_id FK delayed to 0003
    op.create_table(
        "dispatch_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("rework_order_id", sa.BigInteger(), nullable=True),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("technician_account_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_authorization_id", sa.BigInteger(), nullable=False),
        sa.Column("dispatched_by_account_id", sa.BigInteger(), nullable=False),
        sa.Column("dispatch_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'PENDING_ACCEPT'"),
            nullable=False,
        ),
        sa.Column("recommend_score", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column(
            "candidate_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "dispatched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("accept_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(length=256), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalate_reason", sa.String(length=256), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN "
            "('PENDING_ACCEPT', 'ACCEPTED', 'REJECTED', 'TIMEOUT_REASSIGNED', "
            "'COMPLETED', 'ESCALATED', 'CANCELLED')",
            name=conv("ck_dispatch_records_status_enum"),
        ),
        sa.CheckConstraint(
            "dispatch_mode IN ('MANUAL', 'AUTO_RECOMMENDED', 'REASSIGN')",
            name=conv("ck_dispatch_records_dispatch_mode_enum"),
        ),
        sa.CheckConstraint(
            "recommend_score IS NULL OR recommend_score >= 0",
            name=conv("ck_dispatch_records_recommend_score_range"),
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR (reject_reason IS NOT NULL AND rejected_at IS NOT NULL)",
            name=conv("ck_dispatch_records_reject_reason_required"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('ESCALATED', 'TIMEOUT_REASSIGNED') OR escalated_at IS NOT NULL",
            name=conv("ck_dispatch_records_escalate_reason_required"),
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name=conv("ck_dispatch_records_completed_at_required"),
        ),
        sa.CheckConstraint(
            "(status IN ('ACCEPTED', 'COMPLETED')) = (accepted_at IS NOT NULL)",
            name=conv("ck_dispatch_records_accepted_at_required"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["fault_tickets.id"],
            name="fk_dispatch_records_ticket_id_fault_tickets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["technician_account_id"],
            ["accounts.id"],
            name="fk_dispatch_records_technician_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_authorization_id"],
            ["scope_authorizations.id"],
            name="fk_dispatch_records_scope_authorization_id_scope_authorizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dispatched_by_account_id"],
            ["accounts.id"],
            name="fk_dispatch_records_dispatched_by_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dispatch_records"),
    )
    op.create_index(
        "ix_dispatch_records_ticket_id",
        "dispatch_records",
        ["ticket_id", sa.text("dispatched_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_dispatch_records_region_id",
        "dispatch_records",
        ["region_id", sa.text("dispatched_at DESC")],
        unique=False,
    )

    # 12. repair_processes (⑭)
    op.create_table(
        "repair_processes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dispatch_record_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("technician_account_id", sa.BigInteger(), nullable=False),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "electrical_safety_checked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "safety_checklist_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("safety_note", sa.String(length=512), nullable=False),
        sa.Column("root_cause", sa.String(length=256), nullable=True),
        sa.Column("repair_summary", sa.Text(), nullable=False),
        sa.Column("repair_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repair_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "electrical_safety_checked = FALSE "
            "OR ("
            "(safety_checklist_json->>'power_isolated')::boolean IS TRUE "
            "AND (safety_checklist_json->>'voltage_verified_zero')::boolean IS TRUE "
            "AND (safety_checklist_json->>'insulation_intact')::boolean IS TRUE "
            "AND (safety_checklist_json->>'grounding_connected')::boolean IS TRUE "
            "AND (safety_checklist_json->>'ppe_worn')::boolean IS TRUE"
            ")",
            name=conv("ck_repair_processes_safety_checklist_complete"),
        ),
        sa.CheckConstraint(
            "length(btrim(safety_note)) > 0",
            name=conv("ck_repair_processes_safety_note_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(repair_summary)) > 0",
            name=conv("ck_repair_processes_repair_summary_not_blank"),
        ),
        sa.CheckConstraint(
            "repair_finished_at IS NULL "
            "OR repair_started_at IS NULL "
            "OR repair_finished_at >= repair_started_at",
            name=conv("ck_repair_processes_time_order"),
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_record_id"],
            ["dispatch_records.id"],
            name="fk_repair_processes_dispatch_record_id_dispatch_records",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["fault_tickets.id"],
            name="fk_repair_processes_ticket_id_fault_tickets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["technician_account_id"],
            ["accounts.id"],
            name="fk_repair_processes_technician_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_repair_processes"),
        sa.UniqueConstraint("dispatch_record_id", name="uq_repair_processes_dispatch_record_id"),
    )
    op.create_index(
        "ix_repair_processes_ticket_id",
        "repair_processes",
        ["ticket_id"],
        unique=False,
    )
    op.create_index(
        "ix_repair_processes_technician",
        "repair_processes",
        ["technician_account_id", sa.text("created_at DESC")],
        unique=False,
    )

    # 13. material_usages (⑮)
    op.create_table(
        "material_usages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repair_process_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("material_code", sa.String(length=64), nullable=False),
        sa.Column("material_name", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=conv("ck_material_usages_quantity_positive"),
        ),
        sa.CheckConstraint(
            "unit IN ('PCS', 'M', 'KG', 'SET')",
            name=conv("ck_material_usages_unit_enum"),
        ),
        sa.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name=conv("ck_material_usages_unit_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["repair_process_id"],
            ["repair_processes.id"],
            name="fk_material_usages_repair_process_id_repair_processes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["fault_tickets.id"],
            name="fk_material_usages_ticket_id_fault_tickets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_usages"),
        sa.UniqueConstraint(
            "repair_process_id",
            "material_code",
            name="uq_material_usages_repair_code",
        ),
    )
    op.create_index(
        "ix_material_usages_ticket_id",
        "material_usages",
        ["ticket_id"],
        unique=False,
    )

    # 14. rework_orders (⑰) — source_acceptance_id FK delayed to 0003 (Scheme B)
    op.create_table(
        "rework_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rework_no", sa.String(length=32), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("source_acceptance_id", sa.BigInteger(), nullable=False),
        sa.Column("round_no", sa.SmallInteger(), nullable=False),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'PENDING_DISPATCH'"),
            nullable=False,
        ),
        sa.Column("reject_reason", sa.String(length=512), nullable=False),
        sa.Column(
            "requires_manual_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("manual_review_decided_by_account_id", sa.BigInteger(), nullable=True),
        sa.Column("manual_review_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_review_decision", sa.String(length=32), nullable=True),
        sa.Column("manual_review_note", sa.String(length=512), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN "
            "('PENDING_DISPATCH', 'IN_MANUAL_REVIEW', 'DISPATCHED', 'IN_REPAIR', "
            "'PENDING_ACCEPTANCE', 'PASSED', 'CLOSED_BY_REVIEW', 'CANCELLED')",
            name=conv("ck_rework_orders_status_enum"),
        ),
        sa.CheckConstraint(
            "round_no >= 1",
            name=conv("ck_rework_orders_round_positive"),
        ),
        sa.CheckConstraint(
            "manual_review_decision IS NULL "
            "OR manual_review_decision IN "
            "('APPROVE_REASSIGN', 'SPECIAL_CLOSE', 'TERMINATE_CANCEL')",
            name=conv("ck_rework_orders_manual_review_decision_enum"),
        ),
        sa.CheckConstraint(
            "(requires_manual_review = TRUE "
            "AND status IN ('IN_MANUAL_REVIEW', 'CLOSED_BY_REVIEW', 'CANCELLED')) "
            "OR (requires_manual_review = FALSE "
            "AND status NOT IN ('IN_MANUAL_REVIEW', 'CLOSED_BY_REVIEW'))",
            name=conv("ck_rework_orders_manual_review_state_consistent"),
        ),
        sa.CheckConstraint(
            "(manual_review_decision IS NULL "
            "AND manual_review_decided_by_account_id IS NULL "
            "AND manual_review_decided_at IS NULL) "
            "OR (manual_review_decision IS NOT NULL "
            "AND manual_review_decided_by_account_id IS NOT NULL "
            "AND manual_review_decided_at IS NOT NULL "
            "AND status IN ('CLOSED_BY_REVIEW', 'CANCELLED'))",
            name=conv("ck_rework_orders_manual_review_fields_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["fault_tickets.id"],
            name="fk_rework_orders_ticket_id_fault_tickets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manual_review_decided_by_account_id"],
            ["accounts.id"],
            name="fk_rework_orders_manual_review_decided_by_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rework_orders"),
        sa.UniqueConstraint("rework_no", name="uq_rework_orders_rework_no"),
        sa.UniqueConstraint("ticket_id", "round_no", name="uq_rework_orders_ticket_round"),
    )
    op.create_index(
        "ix_rework_orders_status_created",
        "rework_orders",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rework_orders_region_id",
        "rework_orders",
        ["region_id", sa.text("created_at DESC")],
        unique=False,
    )

    # 15. acceptance_records (⑯) — inline FK to rework_orders (Scheme B)
    op.create_table(
        "acceptance_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("repair_process_id", sa.BigInteger(), nullable=False),
        sa.Column("rework_order_id", sa.BigInteger(), nullable=True),
        sa.Column("inspector_account_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.String(length=512), nullable=True),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED')",
            name=conv("ck_acceptance_records_decision_enum"),
        ),
        sa.CheckConstraint(
            "decision <> 'REJECTED' OR (comment IS NOT NULL AND length(btrim(comment)) > 0)",
            name=conv("ck_acceptance_records_reject_comment_required"),
        ),
        sa.CheckConstraint(
            "(decision = 'REJECTED') = (rework_order_id IS NOT NULL)",
            name=conv("ck_acceptance_records_rework_linked_on_reject"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["fault_tickets.id"],
            name="fk_acceptance_records_ticket_id_fault_tickets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["repair_process_id"],
            ["repair_processes.id"],
            name="fk_acceptance_records_repair_process_id_repair_processes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rework_order_id"],
            ["rework_orders.id"],
            name="fk_acceptance_records_rework_order_id_rework_orders",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inspector_account_id"],
            ["accounts.id"],
            name="fk_acceptance_records_inspector_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_acceptance_records"),
        sa.UniqueConstraint("repair_process_id", name="uq_acceptance_records_repair_process_id"),
    )
    op.create_index(
        "ix_acceptance_records_ticket_id",
        "acceptance_records",
        ["ticket_id", sa.text("accepted_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_acceptance_records_inspector",
        "acceptance_records",
        ["inspector_account_id", sa.text("accepted_at DESC")],
        unique=False,
    )

    # 16. audit_logs (⑦)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_account_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=True),
        sa.Column("actor_role_code", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=256), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_payload_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "business_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("response_body_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name=conv("ck_audit_logs_response_status_valid"),
        ),
        sa.CheckConstraint(
            "method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')",
            name=conv("ck_audit_logs_method_enum"),
        ),
        sa.CheckConstraint(
            "request_payload_hash IS NULL OR request_payload_hash ~ '^[0-9a-f]{64}$'",
            name=conv("ck_audit_logs_payload_hash_format"),
        ),
        sa.CheckConstraint(
            "(response_status < 400) OR error_code IS NOT NULL",
            name=conv("ck_audit_logs_error_code_iff_failure"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["accounts.id"],
            name="fk_audit_logs_actor_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_logs_created_at",
        "audit_logs",
        [sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_actor_created",
        "audit_logs",
        ["actor_account_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_action_created",
        "audit_logs",
        ["action", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_request_id",
        "audit_logs",
        ["request_id"],
        unique=False,
    )

    # 17. notification_tasks (⑥)
    op.create_table(
        "notification_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recipient_account_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column(
            "channel",
            sa.String(length=16),
            server_default=sa.text("'IN_APP'"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("related_ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("related_dispatch_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), server_default=sa.text("3"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_account_id", sa.BigInteger(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'PROCESSING', 'FAILED', 'DEAD_LETTER')",
            name=conv("ck_notification_tasks_status_enum"),
        ),
        sa.CheckConstraint(
            "channel IN ('IN_APP', 'SMS', 'EMAIL')",
            name=conv("ck_notification_tasks_channel_enum"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name=conv("ck_notification_tasks_attempts_non_negative"),
        ),
        sa.CheckConstraint(
            "(status = 'SENT') = (sent_at IS NOT NULL)",
            name=conv("ck_notification_tasks_sent_at_required"),
        ),
        sa.CheckConstraint(
            "status <> 'DEAD_LETTER' OR last_error IS NOT NULL",
            name=conv("ck_notification_tasks_dead_letter_error_required"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('FAILED', 'PENDING') "
            "OR next_retry_at IS NULL "
            "OR attempt_count < max_attempts",
            name=conv("ck_notification_tasks_failed_next_retry"),
        ),
        sa.CheckConstraint(
            "(is_read = TRUE) = (read_at IS NOT NULL)",
            name=conv("ck_notification_tasks_read_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["recipient_account_id"],
            ["accounts.id"],
            name="fk_notification_tasks_recipient_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_ticket_id"],
            ["fault_tickets.id"],
            name="fk_notification_tasks_related_ticket_id_fault_tickets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_dispatch_id"],
            ["dispatch_records.id"],
            name="fk_notification_tasks_related_dispatch_id_dispatch_records",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_account_id"],
            ["accounts.id"],
            name="fk_notification_tasks_acknowledged_by_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_tasks"),
    )
    op.create_index(
        "ix_notification_tasks_related_ticket_id",
        "notification_tasks",
        ["related_ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    """Delete seeded roles and drop all 17 tables in reverse physical order 17..1."""
    # 17. notification_tasks
    op.drop_index("ix_notification_tasks_related_ticket_id", table_name="notification_tasks")
    op.drop_table("notification_tasks")

    # 16. audit_logs
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    # 15. acceptance_records
    op.drop_index("ix_acceptance_records_inspector", table_name="acceptance_records")
    op.drop_index("ix_acceptance_records_ticket_id", table_name="acceptance_records")
    op.drop_table("acceptance_records")

    # 14. rework_orders
    op.drop_index("ix_rework_orders_region_id", table_name="rework_orders")
    op.drop_index("ix_rework_orders_status_created", table_name="rework_orders")
    op.drop_table("rework_orders")

    # 13. material_usages
    op.drop_index("ix_material_usages_ticket_id", table_name="material_usages")
    op.drop_table("material_usages")

    # 12. repair_processes
    op.drop_index("ix_repair_processes_technician", table_name="repair_processes")
    op.drop_index("ix_repair_processes_ticket_id", table_name="repair_processes")
    op.drop_table("repair_processes")

    # 11. dispatch_records
    op.drop_index("ix_dispatch_records_region_id", table_name="dispatch_records")
    op.drop_index("ix_dispatch_records_ticket_id", table_name="dispatch_records")
    op.drop_table("dispatch_records")

    # 10. fault_evidences
    op.drop_index("ix_fault_evidences_ticket_id_type", table_name="fault_evidences")
    op.drop_table("fault_evidences")

    # 9. fault_tickets
    op.drop_index("ix_fault_tickets_created_at", table_name="fault_tickets")
    op.drop_index("ix_fault_tickets_reporter_account_id", table_name="fault_tickets")
    op.drop_index("ix_fault_tickets_lighting_point_id", table_name="fault_tickets")
    op.drop_table("fault_tickets")

    # 8. facility_assets
    op.drop_index("ix_facility_assets_lighting_point_id", table_name="facility_assets")
    op.drop_table("facility_assets")

    # 7. business_rules
    op.drop_table("business_rules")

    # 6. scope_authorizations
    op.drop_index("ix_scope_authorizations_region_type_active", table_name="scope_authorizations")
    op.drop_table("scope_authorizations")

    # 5. account_roles
    op.drop_index("ix_account_roles_role_id", table_name="account_roles")
    op.drop_table("account_roles")

    # 4. accounts
    op.drop_table("accounts")

    # 3. lighting_points
    op.drop_index("ix_lighting_points_road_name", table_name="lighting_points")
    op.drop_index("ix_lighting_points_region_id_status", table_name="lighting_points")
    op.drop_table("lighting_points")

    # 2. roles (delete seeded rows before dropping table)
    op.execute(sa.text("DELETE FROM roles WHERE code IN ('ADMIN', 'INSPECTOR', 'TECHNICIAN')"))
    op.drop_table("roles")

    # 1. maintenance_regions
    op.drop_index("ix_maintenance_regions_parent_region_id", table_name="maintenance_regions")
    op.drop_table("maintenance_regions")
