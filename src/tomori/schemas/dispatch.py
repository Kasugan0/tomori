"""区域派工、改派、维修完工安全检查与耗材明细 Schema。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from tomori.schemas.common import (
    DispatchCreateMode,
    DispatchMode,
    DispatchStatus,
    FaultType,
    MaterialUnit,
    NotEligibleReason,
    ORMModel,
    PriorityCode,
    TicketStatus,
)
from tomori.schemas.ticket import FaultTicketOut


class DispatchCandidateItem(ORMModel):
    """合格维修员候选人及推荐打分项。"""

    account_id: int
    username: str
    full_name: str
    scope_authorization_id: int
    active_dispatch_count: int
    max_active_dispatch: int
    skill_match: bool
    on_time_rate: float
    recommend_score: float


class DispatchExcludedItem(ORMModel):
    """被排除候选人及不合格原因（5 值枚举）。"""

    account_id: int
    username: str
    full_name: str
    reason: NotEligibleReason
    active_dispatch_count: int
    max_active_dispatch: int


class DispatchCandidatesResponseOut(ORMModel):
    """GET /tickets/{id}/dispatch-candidates 响应模型。"""

    ticket_id: int
    generated_at: datetime
    candidates: list[DispatchCandidateItem] = Field(default_factory=list)
    excluded: list[DispatchExcludedItem] = Field(default_factory=list)


class TicketDispatchIn(BaseModel):
    """派工请求体（POST /tickets/{id}/dispatch）。"""

    technician_account_id: int = Field(gt=0)
    scope_authorization_id: int = Field(gt=0)
    dispatch_mode: DispatchCreateMode = "MANUAL"
    remark: str | None = Field(default=None, max_length=256)


class TicketReassignIn(BaseModel):
    """改派 / 升级改派请求体（POST /tickets/{id}/reassign）。"""

    technician_account_id: int = Field(gt=0)
    scope_authorization_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=256)
    escalate: bool = False

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason 不能为空白")
        return v


class DispatchRejectIn(BaseModel):
    """维修员拒单请求体（POST /dispatches/{id}/reject）。"""

    reject_reason: str = Field(min_length=1, max_length=256)

    @field_validator("reject_reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reject_reason 不能为空白")
        return v


class DispatchEscalateIn(BaseModel):
    """维修员异常升级请求体（POST /dispatches/{id}/escalate）。"""

    escalate_reason: str = Field(min_length=1, max_length=256)

    @field_validator("escalate_reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("escalate_reason 不能为空白")
        return v


class DispatchRecordOut(ORMModel):
    """派工记录响应模型。"""

    id: int
    ticket_id: int
    ticket_no: str | None = None
    ticket_status: TicketStatus | None = None
    ticket_priority: PriorityCode | None = None
    ticket_fault_type: FaultType | None = None
    ticket_title: str | None = None
    point_code: str | None = None
    point_name: str | None = None
    region_id: int
    region_name: str | None = None
    rework_order_id: int | None = None
    technician_account_id: int
    technician_username: str | None = None
    technician_full_name: str | None = None
    scope_authorization_id: int
    dispatched_by_account_id: int
    dispatch_mode: DispatchMode
    status: DispatchStatus
    recommend_score: Decimal | None = None
    dispatched_at: datetime
    accept_deadline_at: datetime
    repair_deadline_at: datetime | None = None
    accepted_at: datetime | None = None
    rejected_at: datetime | None = None
    reject_reason: str | None = None
    completed_at: datetime | None = None
    escalated_at: datetime | None = None
    escalate_reason: str | None = None
    cancelled_at: datetime | None = None
    created_at: datetime


class SafetyChecklistIn(BaseModel):
    """电气安全检查 5 项输入（保持布尔可传 False，由服务层收集全部 missing_items）。"""

    power_isolated: bool
    voltage_verified_zero: bool
    insulation_intact: bool
    grounding_connected: bool
    ppe_worn: bool


class SafetyChecklistOut(ORMModel):
    """电气安全检查 5 项输出。"""

    power_isolated: bool
    voltage_verified_zero: bool
    insulation_intact: bool
    grounding_connected: bool
    ppe_worn: bool


class MaterialItemIn(BaseModel):
    """维修耗材明细输入。"""

    material_code: str = Field(min_length=1, max_length=64)
    material_name: str = Field(min_length=1, max_length=128)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit: MaterialUnit
    unit_price: Decimal | None = Field(default=None, ge=Decimal("0"))


class MaterialUsageOut(ORMModel):
    """维修耗材明细输出。"""

    id: int | None = None
    repair_process_id: int | None = None
    ticket_id: int | None = None
    material_code: str
    material_name: str
    quantity: float
    unit: MaterialUnit
    unit_price: float | None = None
    created_at: datetime | None = None


class CompleteRepairIn(BaseModel):
    """维修完工提交请求体（POST /dispatches/{id}/complete）。"""

    electrical_safety_checked: bool
    safety_checklist: SafetyChecklistIn
    safety_note: str = Field(max_length=512)
    arrived_at: datetime
    root_cause: str | None = Field(default=None, max_length=256)
    repair_summary: str = Field(min_length=1)
    repair_started_at: datetime | None = None
    repair_finished_at: datetime | None = None
    materials: list[MaterialItemIn] = Field(default_factory=list)

    @field_validator("repair_summary")
    @classmethod
    def _summary_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repair_summary 不能为空白")
        return v

    @model_validator(mode="after")
    def _validate_time_order(self) -> CompleteRepairIn:
        if (
            self.repair_started_at is not None
            and self.repair_finished_at is not None
            and self.repair_finished_at < self.repair_started_at
        ):
            raise ValueError("repair_finished_at 不得早于 repair_started_at")
        return self


class RepairProcessPatchIn(BaseModel):
    """IN_REPAIR 期间修订维修过程与覆盖式耗材清单（PATCH /repair-processes/{id}）。"""

    electrical_safety_checked: bool | None = None
    safety_checklist: SafetyChecklistIn | None = None
    safety_note: str | None = Field(default=None, max_length=512)
    arrived_at: datetime | None = None
    root_cause: str | None = Field(default=None, max_length=256)
    repair_summary: str | None = Field(default=None, min_length=1)
    repair_started_at: datetime | None = None
    repair_finished_at: datetime | None = None
    materials: list[MaterialItemIn] | None = None

    @field_validator("repair_summary")
    @classmethod
    def _summary_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("repair_summary 不能为空白")
        return v

    @model_validator(mode="after")
    def _validate_time_order(self) -> RepairProcessPatchIn:
        if (
            self.repair_started_at is not None
            and self.repair_finished_at is not None
            and self.repair_finished_at < self.repair_started_at
        ):
            raise ValueError("repair_finished_at 不得早于 repair_started_at")
        return self


class RepairProcessOut(ORMModel):
    """维修过程记录响应模型。"""

    id: int
    dispatch_record_id: int
    ticket_id: int
    technician_account_id: int
    arrived_at: datetime
    electrical_safety_checked: bool
    safety_checklist_json: SafetyChecklistOut
    safety_note: str
    root_cause: str | None = None
    repair_summary: str
    repair_started_at: datetime | None = None
    repair_finished_at: datetime | None = None
    materials: list[MaterialUsageOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CompleteRepairResponseOut(ORMModel):
    """POST /dispatches/{id}/complete 响应模型。"""

    dispatch: DispatchRecordOut
    ticket: FaultTicketOut
    repair_process_id: int
    material_usage_ids: list[int] = Field(default_factory=list)


__all__ = [
    "CompleteRepairIn",
    "CompleteRepairResponseOut",
    "DispatchCandidateItem",
    "DispatchCandidatesResponseOut",
    "DispatchEscalateIn",
    "DispatchExcludedItem",
    "DispatchRecordOut",
    "DispatchRejectIn",
    "MaterialItemIn",
    "MaterialUsageOut",
    "RepairProcessOut",
    "RepairProcessPatchIn",
    "SafetyChecklistIn",
    "SafetyChecklistOut",
    "TicketDispatchIn",
    "TicketReassignIn",
]
