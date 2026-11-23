"""Seed initial active v1 business_rules defaults.

Revision ID: 0004_business_rules_seed
Revises: 0003_circular_fk_scheme_b
Create Date: 2026-11-24

"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_business_rules_seed"
down_revision: str | Sequence[str] | None = "0003_circular_fk_scheme_b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_RULES: tuple[tuple[str, dict[str, Any], str], ...] = (
    (
        "grading_rules",
        {
            "display_names": {
                "P1": "特急",
                "P2": "加急",
                "P3": "普通",
                "P4": "观察",
            },
            "rules": [
                {
                    "rule_id": "R1_SAFETY_OR_EXPRESS_CRITICAL",
                    "priority": "P1",
                    "any": [
                        {"fault_type": "ELECTRIC_HAZARD"},
                        {"safety_flag": True},
                        {
                            "road_level": "EXPRESS",
                            "asset_importance_in": ["CRITICAL", "HIGH"],
                        },
                    ],
                },
                {
                    "rule_id": "R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET",
                    "priority": "P2",
                    "any": [
                        {"road_level": "EXPRESS"},
                        {
                            "road_level": "MAIN",
                            "fault_type_in": ["FIXTURE_DAMAGED", "LAMP_OUT"],
                        },
                        {"asset_importance_in": ["CRITICAL", "HIGH"]},
                    ],
                },
                {
                    "rule_id": "R3_BRANCH_OR_MAIN_MINOR",
                    "priority": "P3",
                    "any": [
                        {
                            "road_level": "BRANCH",
                            "fault_type_in": [
                                "LAMP_OUT",
                                "OCCLUDED",
                                "FLICKERING",
                                "FIXTURE_DAMAGED",
                            ],
                        },
                        {
                            "road_level": "MAIN",
                            "fault_type_in": ["OCCLUDED", "FLICKERING", "OTHER"],
                        },
                    ],
                },
            ],
            "default_priority": "P4",
            "default_rule_id": "R4_FALLBACK_OBSERVATION",
        },
        "表驱动确定性故障分级规则（P1 特急 / P2 加急 / P3 普通 / P4 观察，D-12/D-13）",
    ),
    (
        "sla_policy",
        {
            "statutory_deadline_hours": 48,
            "response_minutes": None,
            "emergency_notice_hours": 24,
            "applies_to_priorities": ["P1", "P2", "P3", "P4"],
        },
        (
            "郑州市令第 152 号第 19 条第 3 款法定 48 小时维修养护/督促基线"
            "与第 24 条第 2 款 24h 紧急手续标志（D-11）"
        ),
    ),
    (
        "dispatch_scoring_weights",
        {
            "weights": {
                "workload": 0.40,
                "skill": 0.35,
                "on_time_rate": 0.25,
            },
            "max_recommendations": 10,
            "overload_penalty_factor": 0.0,
        },
        "区域派单候选维修员推荐排序权重（纯辅助参考排序，不影响法定 48h 时限）",
    ),
    (
        "rework_policy",
        {
            "max_rework_rounds": 2,
            "on_exceed_action": "FORCE_ADMIN_REVIEW",
        },
        "返修阈值与超阈值强制人工复核策略（最大正常返修 2 轮，D-14）",
    ),
    (
        "ai_candidate_ttl_hours",
        {
            "ttl_hours": 72,
        },
        "AI 候选单未确认自动过期回收时限（72 小时，D-15）",
    ),
)


def upgrade() -> None:
    """Insert initial v1 active business rules into business_rules."""
    insert_stmt = sa.text(
        "INSERT INTO business_rules "
        "(rule_code, version, rule_value, description, is_active) "
        "VALUES (:rule_code, :version, CAST(:rule_value AS jsonb), :description, :is_active)"
    )
    for rule_code, rule_value, description in _SEED_RULES:
        op.execute(
            insert_stmt.bindparams(
                rule_code=rule_code,
                version=1,
                rule_value=json.dumps(rule_value, ensure_ascii=False),
                description=description,
                is_active=True,
            )
        )


def downgrade() -> None:
    """Remove initial v1 seeded business rules."""
    op.execute(
        sa.text(
            "DELETE FROM business_rules "
            "WHERE version = 1 "
            "AND rule_code IN ("
            "'grading_rules', "
            "'sla_policy', "
            "'dispatch_scoring_weights', "
            "'rework_policy', "
            "'ai_candidate_ttl_hours'"
            ")"
        )
    )
