"""表驱动确定性故障分级引擎 (`grading_rules`)、5 类业务规则严格校验与加载器。

严格遵循 `docs/DESIGN.md` §3.3 与 `docs/API.md` §7.4：
- 仅基于四项输入 (`fault_type`, `road_level`, `asset_importance`, `safety_flag`)
  从高到低依次求值 `grading_rules`，命中首个分支即返回，其余由 `default_priority` (`P4`) 兜底。
- 4 因子完备覆盖 `6 × 3 × 4 × 2 = 144` 种组合（默认规则命中分布 `94 + 34 + 14 + 2 = 144`）。
- 废除一切浮点和魔数权重：`fault_tickets.grading_score` 恒为 `None`。
- 无资产绑定时 `asset_importance` 为 `None`（绝不虚构 `"NONE"` 或 35 分）。
- 严禁依赖 `fastapi`，所有业务规则异常统一抛 `AppError`。
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.models.business_rule import BusinessRule
from tomori.schemas.common import (
    AssetImportance,
    FaultType,
    PriorityCode,
    RoadLevel,
)
from tomori.services.exceptions import (
    INVALID_RULE_PAYLOAD,
    VALIDATION_ERROR,
    AppError,
)

__all__ = [
    "ASSET_IMPORTANCES",
    "DEFAULT_BUSINESS_RULES",
    "DEFAULT_GRADING_RULES",
    "FAULT_TYPES",
    "PRIORITIES",
    "ROAD_LEVELS",
    "VALID_RULE_CODES",
    "GradingOutcome",
    "evaluate_grading",
    "evaluate_grading_rules",
    "evaluate_ticket_grading",
    "get_active_rule",
    "validate_rule_payload",
    "validate_rule_payload_strict",
    "verify_grading_completeness",
]

FAULT_TYPES: Final[tuple[str, ...]] = (
    "LAMP_OUT",
    "FIXTURE_DAMAGED",
    "OCCLUDED",
    "ELECTRIC_HAZARD",
    "FLICKERING",
    "OTHER",
)
ROAD_LEVELS: Final[tuple[str, ...]] = ("EXPRESS", "MAIN", "BRANCH")
ASSET_IMPORTANCES: Final[tuple[str | None, ...]] = ("CRITICAL", "HIGH", "NORMAL", None)
PRIORITIES: Final[tuple[str, ...]] = ("P1", "P2", "P3", "P4")
VALID_RULE_CODES: Final[frozenset[str]] = frozenset(
    {
        "grading_rules",
        "sla_policy",
        "dispatch_scoring_weights",
        "rework_policy",
        "ai_candidate_ttl_hours",
    }
)

DEFAULT_GRADING_RULES: Final[dict[str, Any]] = {
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
                {"road_level": "EXPRESS", "asset_importance_in": ["CRITICAL", "HIGH"]},
            ],
        },
        {
            "rule_id": "R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET",
            "priority": "P2",
            "any": [
                {"road_level": "EXPRESS"},
                {"road_level": "MAIN", "fault_type_in": ["FIXTURE_DAMAGED", "LAMP_OUT"]},
                {"asset_importance_in": ["CRITICAL", "HIGH"]},
            ],
        },
        {
            "rule_id": "R3_BRANCH_OR_MAIN_MINOR",
            "priority": "P3",
            "any": [
                {
                    "road_level": "BRANCH",
                    "fault_type_in": ["LAMP_OUT", "OCCLUDED", "FLICKERING", "FIXTURE_DAMAGED"],
                },
                {"road_level": "MAIN", "fault_type_in": ["OCCLUDED", "FLICKERING", "OTHER"]},
            ],
        },
    ],
    "default_priority": "P4",
    "default_rule_id": "R4_FALLBACK_OBSERVATION",
}

DEFAULT_BUSINESS_RULES: Final[dict[str, dict[str, Any]]] = {
    "grading_rules": DEFAULT_GRADING_RULES,
    "sla_policy": {
        "statutory_deadline_hours": 48,
        "response_minutes": None,
        "emergency_notice_hours": 24,
        "applies_to_priorities": ["P1", "P2", "P3", "P4"],
    },
    "dispatch_scoring_weights": {
        "weights": {
            "workload": 0.40,
            "skill": 0.35,
            "on_time_rate": 0.25,
        },
        "max_recommendations": 10,
        "overload_penalty_factor": 0.0,
    },
    "rework_policy": {
        "max_rework_rounds": 2,
        "on_exceed_action": "FORCE_ADMIN_REVIEW",
    },
    "ai_candidate_ttl_hours": {
        "ttl_hours": 72,
    },
}


# ──────────────────────────────────────────────────────────────────────
# 5 类 business_rules 严格 Pydantic 校验模型（422 INVALID_RULE_PAYLOAD）
# ──────────────────────────────────────────────────────────────────────


class _StrictGradingClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_type: FaultType | None = None
    fault_type_in: list[FaultType] | None = Field(default=None, min_length=1)
    road_level: RoadLevel | None = None
    road_level_in: list[RoadLevel] | None = Field(default=None, min_length=1)
    asset_importance_in: list[AssetImportance] | None = Field(default=None, min_length=1)
    safety_flag: StrictBool | None = None

    @model_validator(mode="after")
    def _ensure_at_least_one_field(self) -> _StrictGradingClause:
        if all(
            v is None
            for v in (
                self.fault_type,
                self.fault_type_in,
                self.road_level,
                self.road_level_in,
                self.asset_importance_in,
                self.safety_flag,
            )
        ):
            raise ValueError("单条匹配条件至少需包含一个白名单字段")
        return self


class _StrictGradingBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: StrictStr = Field(min_length=1, max_length=64)
    priority: PriorityCode
    any: list[_StrictGradingClause] = Field(min_length=1)

    @field_validator("rule_id")
    @classmethod
    def _check_rule_id_non_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("rule_id 不能为空白字符")
        return stripped


class _StrictGradingRulesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_names: dict[PriorityCode, StrictStr] | None = None
    rules: list[_StrictGradingBranch] = Field(min_length=1)
    default_priority: Literal["P4"]
    default_rule_id: StrictStr = Field(min_length=1, max_length=64)

    @field_validator("default_rule_id")
    @classmethod
    def _check_default_id_non_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("default_rule_id 不能为空白字符")
        return stripped

    @model_validator(mode="after")
    def _validate_uniqueness_and_labels(self) -> _StrictGradingRulesPayload:
        seen_ids: set[str] = set()
        for branch in self.rules:
            if branch.rule_id in seen_ids:
                raise ValueError(f"重复的 rule_id: {branch.rule_id}")
            seen_ids.add(branch.rule_id)
        if self.default_rule_id in seen_ids:
            raise ValueError("default_rule_id 不得与 rules 中的 rule_id 重复")
        if self.display_names is not None:
            expected_keys: set[PriorityCode] = {"P1", "P2", "P3", "P4"}
            if set(self.display_names.keys()) != expected_keys:
                raise ValueError("display_names 必须完整覆盖 P1..P4 四个等级")
            if any(not v.strip() for v in self.display_names.values()):
                raise ValueError("display_names 文案不得为空白")
        return self


def _default_priorities() -> list[PriorityCode]:
    return ["P1", "P2", "P3", "P4"]


class _StrictSlaPolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statutory_deadline_hours: StrictInt = Field(gt=0)
    response_minutes: StrictInt | None = Field(default=None, gt=0)
    emergency_notice_hours: StrictInt = Field(default=24, gt=0)
    applies_to_priorities: list[PriorityCode] = Field(default_factory=_default_priorities)

    @model_validator(mode="after")
    def _validate_priorities(self) -> _StrictSlaPolicyPayload:
        expected: set[PriorityCode] = {"P1", "P2", "P3", "P4"}
        if set(self.applies_to_priorities) != expected:
            raise ValueError("applies_to_priorities 必须覆盖 P1..P4 全部四个等级（含 P4 观察）")
        return self


class _StrictWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workload: StrictFloat | StrictInt
    skill: StrictFloat | StrictInt
    on_time_rate: StrictFloat | StrictInt

    @model_validator(mode="after")
    def _validate_weights_sum(self) -> _StrictWeights:
        vals = (float(self.workload), float(self.skill), float(self.on_time_rate))
        if any(not math.isfinite(v) or v < 0.0 or v > 1.0 for v in vals):
            raise ValueError("各项权重必须为 [0.0, 1.0] 范围内的有限非负数")
        total = vals[0] + vals[1] + vals[2]
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"三项权重之和必须严格等于 1.0，当前为 {total}")
        return self


class _StrictDispatchScoringPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: _StrictWeights
    max_recommendations: StrictInt = Field(default=10, ge=1, le=50)
    overload_penalty_factor: StrictFloat | StrictInt = Field(default=0.0)

    @model_validator(mode="after")
    def _validate_penalty(self) -> _StrictDispatchScoringPayload:
        factor = float(self.overload_penalty_factor)
        if not math.isfinite(factor) or factor < 0.0 or factor > 1.0:
            raise ValueError("overload_penalty_factor 必须在 [0.0, 1.0] 范围内")
        return self


class _StrictReworkPolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_rework_rounds: StrictInt = Field(gt=0)
    on_exceed_action: Literal["FORCE_ADMIN_REVIEW"]


class _StrictAiCandidateTtlPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_hours: StrictInt = Field(gt=0)


_STRICT_RULE_MODELS: Final[dict[str, type[BaseModel]]] = {
    "grading_rules": _StrictGradingRulesPayload,
    "sla_policy": _StrictSlaPolicyPayload,
    "dispatch_scoring_weights": _StrictDispatchScoringPayload,
    "rework_policy": _StrictReworkPolicyPayload,
    "ai_candidate_ttl_hours": _StrictAiCandidateTtlPayload,
}


def validate_rule_payload(rule_code: str, rule_value: Any) -> dict[str, Any]:
    """严格校验 5 类业务规则载荷，任何结构或语义违规一律抛出 `422 INVALID_RULE_PAYLOAD`。"""
    if not isinstance(rule_code, str) or rule_code not in _STRICT_RULE_MODELS:
        raise AppError(
            INVALID_RULE_PAYLOAD,
            f"不支持的业务规则编码: {rule_code!r}",
            {"rule_code": rule_code, "allowed_rule_codes": sorted(VALID_RULE_CODES)},
        )
    if not isinstance(rule_value, Mapping):
        raise AppError(
            INVALID_RULE_PAYLOAD,
            f"业务规则 {rule_code} 载荷必须为 JSON 对象",
            {"rule_code": rule_code},
        )

    model_cls = _STRICT_RULE_MODELS[rule_code]
    try:
        validated = model_cls.model_validate(dict(rule_value))
    except ValidationError as exc:
        raise AppError(
            INVALID_RULE_PAYLOAD,
            f"业务规则 {rule_code} 载荷校验失败",
            {
                "rule_code": rule_code,
                "errors": [
                    {
                        "loc": [str(part) for part in err.get("loc", ())],
                        "msg": str(err.get("msg", "")),
                    }
                    for err in exc.errors()
                ],
            },
        ) from exc

    if rule_code == "grading_rules":
        dumped = validated.model_dump(exclude_none=True)
        return dumped
    return validated.model_dump()


validate_rule_payload_strict = validate_rule_payload


class GradingOutcome(dict[str, Any]):
    """确定性分级结果（既是匹配 `grading_snapshot_json` 结构的 `dict`，也提供强类型属性访问）。

    `grading_score` 在任何情形下恒为 `None`。
    """

    def __init__(
        self,
        *,
        priority: str,
        matched_rule_id: str,
        rule_version: int,
        emergency_action_required: bool,
        inputs: dict[str, Any],
    ) -> None:
        snapshot: dict[str, Any] = {
            "rule_code": "grading_rules",
            "rule_version": rule_version,
            "matched_rule_id": matched_rule_id,
            "priority": priority,
            "inputs": inputs,
            "emergency_action_required": emergency_action_required,
        }
        super().__init__(
            {
                **snapshot,
                "grading_score": None,
            }
        )
        self._snapshot_json = snapshot

    @property
    def priority(self) -> str:
        return str(self["priority"])

    @property
    def matched_rule_id(self) -> str:
        return str(self["matched_rule_id"])

    @property
    def rule_version(self) -> int:
        return int(self["rule_version"])

    @property
    def emergency_action_required(self) -> bool:
        return bool(self["emergency_action_required"])

    @property
    def inputs(self) -> dict[str, Any]:
        raw: dict[str, Any] = self["inputs"]
        return raw

    @property
    def grading_score(self) -> None:
        return None

    @property
    def snapshot_json(self) -> dict[str, Any]:
        return dict(self._snapshot_json)


async def get_active_rule(
    session: AsyncSession,
    rule_code: str,
) -> tuple[int, dict[str, Any]]:
    """读取指定 `rule_code` 当前活跃版本 `(version, rule_value)`；未命中时回退种子默认值。"""
    if rule_code not in VALID_RULE_CODES:
        raise AppError(
            INVALID_RULE_PAYLOAD,
            f"未知的业务规则编码: {rule_code!r}",
            {"rule_code": rule_code, "allowed_rule_codes": sorted(VALID_RULE_CODES)},
        )
    row = await session.scalar(
        select(BusinessRule).where(
            BusinessRule.rule_code == rule_code,
            BusinessRule.is_active.is_(True),
        )
    )
    if row is None:
        return 1, copy.deepcopy(DEFAULT_BUSINESS_RULES[rule_code])
    return row.version, dict(row.rule_value)


def _condition_matches(
    cond: Mapping[str, Any],
    *,
    fault_type: str,
    road_level: str,
    asset_importance: str | None,
    safety_flag: bool,
) -> bool:
    """单条合取条件求值：所有非空键必须同时满足 (`AND`)。"""
    if "fault_type" in cond and cond["fault_type"] is not None and fault_type != cond["fault_type"]:
        return False
    if (
        "fault_type_in" in cond
        and cond["fault_type_in"] is not None
        and fault_type not in cond["fault_type_in"]
    ):
        return False
    if "road_level" in cond and cond["road_level"] is not None and road_level != cond["road_level"]:
        return False
    if (
        "road_level_in" in cond
        and cond["road_level_in"] is not None
        and road_level not in cond["road_level_in"]
    ):
        return False
    if (
        "asset_importance_in" in cond
        and cond["asset_importance_in"] is not None
        and (asset_importance is None or asset_importance not in cond["asset_importance_in"])
    ):
        return False
    return not (
        "safety_flag" in cond
        and cond["safety_flag"] is not None
        and safety_flag is not bool(cond["safety_flag"])
    )


def evaluate_grading(
    fault_type: str,
    road_level: str,
    asset_importance: str | None,
    safety_flag: bool,
    rule_value: Mapping[str, Any] | None = None,
    *,
    rule_version: int = 1,
) -> GradingOutcome:
    """确定性规则表求值（4 因子 `6 × 3 × 4 × 2 = 144` 组合完备覆盖：`94 + 34 + 14 + 2 = 144`）。"""
    if fault_type not in FAULT_TYPES:
        raise AppError(
            VALIDATION_ERROR,
            f"非法的故障类型 fault_type: {fault_type!r}",
            {"field": "fault_type", "fault_type": fault_type},
        )
    if road_level not in ROAD_LEVELS:
        raise AppError(
            VALIDATION_ERROR,
            f"非法的道路等级 road_level: {road_level!r}",
            {"field": "road_level", "road_level": road_level},
        )
    if asset_importance not in ASSET_IMPORTANCES:
        raise AppError(
            VALIDATION_ERROR,
            f"非法的设施重要度 asset_importance: {asset_importance!r}",
            {"field": "asset_importance", "asset_importance": asset_importance},
        )
    if not isinstance(safety_flag, bool):
        raise AppError(
            VALIDATION_ERROR,
            "safety_flag 必须为布尔值",
            {"field": "safety_flag"},
        )

    if rule_value is None:
        effective_rule: Mapping[str, Any] = DEFAULT_GRADING_RULES
    else:
        effective_rule = validate_rule_payload("grading_rules", rule_value)

    emergency_action = fault_type == "ELECTRIC_HAZARD" or safety_flag

    matched_priority = str(effective_rule.get("default_priority", "P4"))
    matched_rule_id = str(effective_rule.get("default_rule_id", "R4_FALLBACK_OBSERVATION"))

    for branch in effective_rule.get("rules", ()):
        any_list = branch.get("any", ())
        if any(
            _condition_matches(
                cond,
                fault_type=fault_type,
                road_level=road_level,
                asset_importance=asset_importance,
                safety_flag=safety_flag,
            )
            for cond in any_list
        ):
            matched_priority = str(branch["priority"])
            matched_rule_id = str(branch["rule_id"])
            break

    inputs: dict[str, Any] = {
        "fault_type": fault_type,
        "road_level": road_level,
        "asset_importance": asset_importance,
        "safety_flag": safety_flag,
    }
    return GradingOutcome(
        priority=matched_priority,
        matched_rule_id=matched_rule_id,
        rule_version=rule_version,
        emergency_action_required=emergency_action,
        inputs=inputs,
    )


evaluate_grading_rules = evaluate_grading


async def evaluate_ticket_grading(
    session: AsyncSession,
    *,
    fault_type: str,
    road_level: str,
    asset_importance: str | None,
    safety_flag: bool,
) -> GradingOutcome:
    """从数据库读取当前活跃 `grading_rules` 版本并计算工单分级结果。"""
    version, rule_val = await get_active_rule(session, "grading_rules")
    return evaluate_grading(
        fault_type=fault_type,
        road_level=road_level,
        asset_importance=asset_importance,
        safety_flag=safety_flag,
        rule_value=rule_val,
        rule_version=version,
    )


def verify_grading_completeness(
    rule_value: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """穷举全部 `6 × 3 × 4 × 2 = 144` 种合法输入组合，返回各 `matched_rule_id` 命中数。"""
    counts: dict[str, int] = {}
    for ft in FAULT_TYPES:
        for rl in ROAD_LEVELS:
            for ai in ASSET_IMPORTANCES:
                for sf in (True, False):
                    res = evaluate_grading(ft, rl, ai, sf, rule_value)
                    rule_id = res["matched_rule_id"]
                    counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts
