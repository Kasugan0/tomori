"""公共 Schema 基类、领域枚举类型别名与通用分页响应模型。"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RoleCode = Literal["ADMIN", "INSPECTOR", "TECHNICIAN"]

ScopeType = Literal["INSPECT", "REPAIR", "MANAGE"]

AccountStatus = Literal["ACTIVE", "DISABLED", "LOCKED"]

TicketStatus = Literal[
    "AI_CANDIDATE_PENDING",
    "PENDING_DISPATCH",
    "DISPATCHED",
    "IN_REPAIR",
    "PENDING_ACCEPTANCE",
    "REWORK_PENDING_DISPATCH",
    "MANUAL_REVIEW",
    "CLOSED",
    "CANCELLED",
]

DispatchStatus = Literal[
    "PENDING_ACCEPT",
    "ACCEPTED",
    "REJECTED",
    "TIMEOUT_REASSIGNED",
    "COMPLETED",
    "ESCALATED",
    "CANCELLED",
]

DispatchMode = Literal["MANUAL", "AUTO_RECOMMENDED", "REASSIGN"]

DispatchCreateMode = Literal["MANUAL", "AUTO_RECOMMENDED"]

ReworkStatus = Literal[
    "PENDING_DISPATCH",
    "IN_MANUAL_REVIEW",
    "DISPATCHED",
    "IN_REPAIR",
    "PENDING_ACCEPTANCE",
    "PASSED",
    "CLOSED_BY_REVIEW",
    "CANCELLED",
]

PriorityCode = Literal["P1", "P2", "P3", "P4"]

FaultType = Literal[
    "LAMP_OUT",
    "FIXTURE_DAMAGED",
    "OCCLUDED",
    "ELECTRIC_HAZARD",
    "FLICKERING",
    "OTHER",
]

AiFaultType = Literal["LAMP_OUT", "FIXTURE_DAMAGED", "OCCLUDED", "OTHER"]

RoadLevel = Literal["EXPRESS", "MAIN", "BRANCH"]

AssetImportance = Literal["CRITICAL", "HIGH", "NORMAL"]

AssetType = Literal["LUMINAIRE", "POLE", "BRACKET", "CONTROL_BOX", "CABLE", "OTHER"]

AssetStatus = Literal["IN_SERVICE", "FAULTY", "REPAIRING", "RETIRED"]

LightingPointStatus = Literal["IN_SERVICE", "SUSPENDED", "RETIRED"]

PowerSupplyType = Literal["GRID", "SOLAR", "OTHER"]

ReportSource = Literal["AI_CANDIDATE", "MANUAL_INSPECT", "MANUAL_ADMIN"]

EvidenceType = Literal[
    "INSPECTION_PHOTO",
    "ANNOTATED_PHOTO",
    "REPAIR_PHOTO",
    "ACCEPTANCE_PHOTO",
]

AcceptanceDecision = Literal["APPROVED", "REJECTED"]

ReviewResolution = Literal["APPROVE_REASSIGN", "SPECIAL_CLOSE", "TERMINATE_CANCEL"]

MaterialUnit = Literal["PCS", "M", "KG", "SET"]

NotEligibleReason = Literal[
    "NO_REGION_SCOPE",
    "SCOPE_EXPIRED",
    "ACCOUNT_INACTIVE",
    "WRONG_ROLE",
    "OVERLOADED",
]

RuleCode = Literal[
    "grading_rules",
    "sla_policy",
    "dispatch_scoring_weights",
    "rework_policy",
    "ai_candidate_ttl_hours",
]

SceneLightingHint = Literal["auto", "day", "night", "twilight"]

SceneLighting = Literal["day", "night", "twilight"]

InferenceMode = Literal["yolo26_onnx_e2e", "yolo26_pt", "baseline_pretrained_opencv"]

QualityGrade = Literal["pass", "warn", "fail"]

FusionStatus = Literal[
    "consistent",
    "daytime_unpowered_suppressed",
    "low_confidence_conflict",
    "occlusion_merged",
    "baseline_heuristic",
]

VisualSignalState = Literal[
    "LAMP_OUT",
    "FIXTURE_DAMAGED",
    "OCCLUDED",
    "DAYTIME_ALWAYS_ON_SUSPECT",
    "NORMAL_LIT",
    "DAYTIME_UNPOWERED_EXPECTED",
]

RawClass = Literal[
    "streetlight_on",
    "streetlight_off",
    "streetlight_damaged",
    "streetlight_occluded",
]

SeverityHint = Literal["none", "P1", "P2", "P3", "P4"]

OverdueStage = Literal["RESPONSE", "REPAIR"]

NotificationChannel = Literal["IN_APP", "SMS", "EMAIL"]

NotificationStatus = Literal["PENDING", "SENT", "PROCESSING", "FAILED", "DEAD_LETTER"]


class ORMModel(BaseModel):
    """支持 SQLAlchemy ORM 对象直接映射的输出模型基类。"""

    model_config = ConfigDict(from_attributes=True)


class StatusOut(BaseModel):
    """通用状态操作响应。"""

    status: str = "ok"


class PaginatedResponse[T](BaseModel):
    """统一分页列表响应信封（自动计算 total_pages）。"""

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=0)
    total_pages: int = 0

    @model_validator(mode="after")
    def _compute_total_pages(self) -> PaginatedResponse[T]:
        if "total_pages" not in self.model_fields_set:
            if self.total == 0 or self.page_size <= 0:
                self.total_pages = 0
            else:
                self.total_pages = max(1, math.ceil(self.total / self.page_size))
        return self


__all__ = [
    "AcceptanceDecision",
    "AccountStatus",
    "AiFaultType",
    "AssetImportance",
    "AssetStatus",
    "AssetType",
    "DispatchCreateMode",
    "DispatchMode",
    "DispatchStatus",
    "EvidenceType",
    "FaultType",
    "FusionStatus",
    "InferenceMode",
    "LightingPointStatus",
    "MaterialUnit",
    "NotEligibleReason",
    "NotificationChannel",
    "NotificationStatus",
    "ORMModel",
    "OverdueStage",
    "PaginatedResponse",
    "PowerSupplyType",
    "PriorityCode",
    "QualityGrade",
    "RawClass",
    "ReportSource",
    "ReviewResolution",
    "ReworkStatus",
    "RoadLevel",
    "RoleCode",
    "RuleCode",
    "SceneLighting",
    "SceneLightingHint",
    "ScopeType",
    "SeverityHint",
    "StatusOut",
    "TicketStatus",
    "VisualSignalState",
]
