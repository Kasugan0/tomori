"""运维区域、照明点位、设施资产与版本化业务规则 Schema。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tomori.schemas.common import (
    AssetImportance,
    AssetStatus,
    AssetType,
    FaultType,
    LightingPointStatus,
    ORMModel,
    PowerSupplyType,
    PriorityCode,
    RoadLevel,
    RuleCode,
)


class RegionCreateIn(BaseModel):
    """创建运维区域请求体。"""

    region_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    parent_region_id: int | None = Field(default=None, gt=0)
    level: int = Field(default=3, ge=1, le=3)
    boundary_geojson: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=512)


class RegionUpdateIn(BaseModel):
    """更新运维区域请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_region_id: int | None = Field(default=None, gt=0)
    level: int | None = Field(default=None, ge=1, le=3)
    boundary_geojson: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None


class MaintenanceRegionOut(ORMModel):
    """运维区域响应模型。"""

    id: int
    region_code: str
    name: str
    parent_region_id: int | None = None
    level: int
    boundary_geojson: dict[str, Any] | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


class LightingPointCreateIn(BaseModel):
    """创建照明点位请求体。"""

    point_code: str = Field(min_length=1, max_length=64)
    region_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    road_name: str = Field(min_length=1, max_length=128)
    road_level: RoadLevel
    longitude: Decimal = Field(ge=Decimal("-180"), le=Decimal("180"))
    latitude: Decimal = Field(ge=Decimal("-90"), le=Decimal("90"))
    install_height_m: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("999.99"))
    lamp_count: int = Field(default=1, ge=1)
    power_supply_type: PowerSupplyType = "GRID"
    description: str | None = Field(default=None, max_length=512)


class LightingPointUpdateIn(BaseModel):
    """更新照明点位请求体。"""

    region_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    road_name: str | None = Field(default=None, min_length=1, max_length=128)
    road_level: RoadLevel | None = None
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    install_height_m: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("999.99"))
    lamp_count: int | None = Field(default=None, ge=1)
    power_supply_type: PowerSupplyType | None = None
    status: LightingPointStatus | None = None
    description: str | None = Field(default=None, max_length=512)


class LightingPointOut(ORMModel):
    """照明点位响应模型。"""

    id: int
    point_code: str
    region_id: int
    region_name: str | None = None
    name: str
    road_name: str
    road_level: RoadLevel
    longitude: Decimal
    latitude: Decimal
    install_height_m: Decimal | None = None
    lamp_count: int
    power_supply_type: PowerSupplyType
    status: LightingPointStatus
    description: str | None = None
    created_at: datetime


class FacilityAssetCreateIn(BaseModel):
    """创建设施资产请求体。"""

    asset_code: str = Field(min_length=1, max_length=64)
    lighting_point_id: int = Field(gt=0)
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=128)
    model_spec: str | None = Field(default=None, max_length=128)
    rated_power_w: Decimal | None = Field(default=None, ge=Decimal("0"))
    importance: AssetImportance = "NORMAL"
    installed_at: date | None = None
    description: str | None = Field(default=None, max_length=512)


class FacilityAssetUpdateIn(BaseModel):
    """更新设施资产请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    asset_type: AssetType | None = None
    model_spec: str | None = Field(default=None, max_length=128)
    rated_power_w: Decimal | None = Field(default=None, ge=Decimal("0"))
    importance: AssetImportance | None = None
    status: AssetStatus | None = None
    description: str | None = Field(default=None, max_length=512)


class FacilityAssetOut(ORMModel):
    """设施资产响应模型。"""

    id: int
    asset_code: str
    lighting_point_id: int
    point_code: str | None = None
    asset_type: AssetType
    name: str
    model_spec: str | None = None
    rated_power_w: Decimal | None = None
    importance: AssetImportance
    installed_at: date | None = None
    status: AssetStatus
    description: str | None = None
    created_at: datetime


# ── 5 类 business_rules 结构化载荷校验（供服务层转译 422 INVALID_RULE_PAYLOAD） ──


def _reject_bool_int(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("不允许使用布尔值代替整数")
    return value


class GradingCondition(BaseModel):
    """grading_rules 单条合取条件（白名单字段）。"""

    model_config = ConfigDict(extra="forbid")

    fault_type: FaultType | None = None
    fault_type_in: list[FaultType] | None = Field(default=None, min_length=1)
    road_level: RoadLevel | None = None
    road_level_in: list[RoadLevel] | None = Field(default=None, min_length=1)
    asset_importance_in: list[AssetImportance] | None = Field(default=None, min_length=1)
    safety_flag: bool | None = None

    @model_validator(mode="after")
    def _ensure_non_empty(self) -> GradingCondition:
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
            raise ValueError("条件对象至少包含一个有效匹配字段")
        return self


class GradingRuleBranch(BaseModel):
    """grading_rules 单个优先级分支。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=64)
    priority: PriorityCode
    any: list[GradingCondition] = Field(min_length=1)


class GradingRulesValue(BaseModel):
    """grading_rules 规则载荷校验模型。"""

    model_config = ConfigDict(extra="forbid")

    display_names: dict[PriorityCode, str] | None = None
    rules: list[GradingRuleBranch] = Field(min_length=1)
    default_priority: Literal["P4"]
    default_rule_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_structure(self) -> GradingRulesValue:
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
                raise ValueError("display_names 必须覆盖 P1..P4 全部四个优先级")
            if any(not label.strip() for label in self.display_names.values()):
                raise ValueError("display_names 显示文案不得为空")
        return self


def _default_applies_to_priorities() -> list[PriorityCode]:
    return ["P1", "P2", "P3", "P4"]


class SlaPolicyValue(BaseModel):
    """sla_policy 规则载荷校验模型（郑州市令第 152 号 48h 法定基线）。"""

    model_config = ConfigDict(extra="forbid")

    statutory_deadline_hours: int = Field(gt=0)
    response_minutes: int | None = Field(default=None, gt=0)
    emergency_notice_hours: int = Field(default=24, gt=0)
    applies_to_priorities: list[PriorityCode] = Field(
        default_factory=_default_applies_to_priorities,
    )

    @field_validator(
        "statutory_deadline_hours",
        "response_minutes",
        "emergency_notice_hours",
        mode="before",
    )
    @classmethod
    def _check_not_bool(cls, v: Any) -> Any:
        return _reject_bool_int(v)

    @model_validator(mode="after")
    def _validate_priorities(self) -> SlaPolicyValue:
        expected: set[PriorityCode] = {"P1", "P2", "P3", "P4"}
        if set(self.applies_to_priorities) != expected:
            raise ValueError("applies_to_priorities 必须覆盖 P1..P4")
        return self


class DispatchScoringWeights(BaseModel):
    """派单候选排序三权重子对象（权重和严格等于 1.0）。"""

    model_config = ConfigDict(extra="forbid")

    workload: float = Field(ge=0.0, le=1.0)
    skill: float = Field(ge=0.0, le=1.0)
    on_time_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_sum(self) -> DispatchScoringWeights:
        total = self.workload + self.skill + self.on_time_rate
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"权重之和必须等于 1.0，当前为 {total}")
        return self


class DispatchScoringWeightsValue(BaseModel):
    """dispatch_scoring_weights 规则载荷校验模型。"""

    model_config = ConfigDict(extra="forbid")

    weights: DispatchScoringWeights
    max_recommendations: int = Field(default=10, ge=1, le=50)
    overload_penalty_factor: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("max_recommendations", mode="before")
    @classmethod
    def _check_not_bool(cls, v: Any) -> Any:
        return _reject_bool_int(v)


class ReworkPolicyValue(BaseModel):
    """rework_policy 规则载荷校验模型。"""

    model_config = ConfigDict(extra="forbid")

    max_rework_rounds: int = Field(gt=0)
    on_exceed_action: Literal["FORCE_ADMIN_REVIEW"]

    @field_validator("max_rework_rounds", mode="before")
    @classmethod
    def _check_not_bool(cls, v: Any) -> Any:
        return _reject_bool_int(v)


class AiCandidateTtlValue(BaseModel):
    """ai_candidate_ttl_hours 规则载荷校验模型。"""

    model_config = ConfigDict(extra="forbid")

    ttl_hours: int = Field(gt=0)

    @field_validator("ttl_hours", mode="before")
    @classmethod
    def _check_not_bool(cls, v: Any) -> Any:
        return _reject_bool_int(v)


RULE_VALUE_MODELS: dict[RuleCode, type[BaseModel]] = {
    "grading_rules": GradingRulesValue,
    "sla_policy": SlaPolicyValue,
    "dispatch_scoring_weights": DispatchScoringWeightsValue,
    "rework_policy": ReworkPolicyValue,
    "ai_candidate_ttl_hours": AiCandidateTtlValue,
}


def validate_rule_value(rule_code: RuleCode, rule_value: dict[str, Any]) -> BaseModel:
    """按 rule_code 校验规则载荷，失败抛出 pydantic.ValidationError。"""
    model_cls = RULE_VALUE_MODELS[rule_code]
    return model_cls.model_validate(rule_value)


class BusinessRuleCreateIn(BaseModel):
    """发布新版本业务规则请求体（POST /business-rules）。"""

    rule_code: RuleCode
    rule_value: dict[str, Any]
    description: str | None = Field(default=None, max_length=512)


class BusinessRulePatchIn(BaseModel):
    """更新规则元信息请求体（显式接住 rule_value 供服务层抛出 422 RULE_VALUE_IMMUTABLE）。"""

    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=512)
    rule_value: Any = None


class BusinessRuleOut(ORMModel):
    """业务规则响应模型。"""

    id: int
    rule_code: RuleCode
    version: int
    rule_value: dict[str, Any]
    description: str | None = None
    is_active: bool
    effective_from: datetime
    created_by_account_id: int | None = None
    created_at: datetime


__all__ = [
    "RULE_VALUE_MODELS",
    "AiCandidateTtlValue",
    "BusinessRuleCreateIn",
    "BusinessRuleOut",
    "BusinessRulePatchIn",
    "DispatchScoringWeights",
    "DispatchScoringWeightsValue",
    "FacilityAssetCreateIn",
    "FacilityAssetOut",
    "FacilityAssetUpdateIn",
    "GradingCondition",
    "GradingRuleBranch",
    "GradingRulesValue",
    "LightingPointCreateIn",
    "LightingPointOut",
    "LightingPointUpdateIn",
    "MaintenanceRegionOut",
    "RegionCreateIn",
    "RegionUpdateIn",
    "ReworkPolicyValue",
    "SlaPolicyValue",
    "validate_rule_value",
]
