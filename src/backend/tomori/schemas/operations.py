"""验收、返修、人工复核、站内通知、操作审计与 SLA 扫描 Schema。"""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address, IPv4Interface, IPv6Address, IPv6Interface
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from tomori.schemas.common import (
    AcceptanceDecision,
    NotificationChannel,
    NotificationStatus,
    ORMModel,
    ReviewResolution,
    ReworkStatus,
    RoleCode,
)
from tomori.schemas.ticket import FaultTicketOut

type IPAddressValue = IPv4Address | IPv6Address | IPv4Interface | IPv6Interface


class AcceptanceSubmitIn(BaseModel):
    """巡查员现场验收结论提交请求体（POST /tickets/{id}/acceptance）。"""

    decision: AcceptanceDecision
    comment: str | None = Field(default=None, max_length=512)
    evidence_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_reject_comment(self) -> AcceptanceSubmitIn:
        if self.decision == "REJECTED" and not (self.comment and self.comment.strip()):
            raise ValueError("驳回返修 (REJECTED) 时必须填写非空 comment")
        return self


class AcceptanceRecordOut(ORMModel):
    """验收记录响应模型。"""

    id: int
    ticket_id: int
    repair_process_id: int
    rework_order_id: int | None = None
    inspector_account_id: int
    decision: AcceptanceDecision
    comment: str | None = None
    accepted_at: datetime
    created_at: datetime


class ReworkOrderOut(ORMModel):
    """返修单响应模型。"""

    id: int
    rework_no: str
    ticket_id: int
    source_acceptance_id: int
    round_no: int
    region_id: int
    status: ReworkStatus
    reject_reason: str
    requires_manual_review: bool
    manual_review_decided_by_account_id: int | None = None
    manual_review_decided_at: datetime | None = None
    manual_review_decision: ReviewResolution | None = None
    manual_review_note: str | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AcceptanceResponseOut(ORMModel):
    """POST /tickets/{id}/acceptance 响应模型。"""

    acceptance_id: int
    decision: AcceptanceDecision
    ticket: FaultTicketOut
    rework_order: ReworkOrderOut | None = None


class ReviewResolveIn(BaseModel):
    """管理员人工复核裁决请求体（POST /tickets/{id}/review-resolve）。"""

    resolution: ReviewResolution
    reason: str = Field(min_length=1, max_length=512)
    # SPECIAL_CLOSE 督促留证三要素（郑州市令第 152 号第 19 条第 3 款）：
    # 督促时间 / 受督促单位 / 督促凭证证据（先经 POST /tickets/{id}/evidences 上传）
    urged_at: datetime | None = None
    urged_unit: str | None = Field(default=None, max_length=128)
    urge_evidence_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_reason(self) -> ReviewResolveIn:
        trimmed = self.reason.strip()
        if not trimmed:
            raise ValueError("reason 不能为空白")
        if self.resolution == "SPECIAL_CLOSE":
            if len(trimmed) < 10:
                raise ValueError("SPECIAL_CLOSE 理由长度不少于 10 个字符")
            if self.urged_at is None:
                raise ValueError("SPECIAL_CLOSE 必须提供督促时间 urged_at")
            if self.urged_at.tzinfo is None or self.urged_at.utcoffset() is None:
                raise ValueError("urged_at 必须携带明确时区偏移")
            if not (self.urged_unit and self.urged_unit.strip()):
                raise ValueError("SPECIAL_CLOSE 必须提供受督促单位 urged_unit")
            if not self.urge_evidence_ids:
                raise ValueError("SPECIAL_CLOSE 必须提供督促凭证 urge_evidence_ids")
        elif self.urged_at is not None or self.urged_unit is not None or self.urge_evidence_ids:
            raise ValueError("urged_at / urged_unit / urge_evidence_ids 仅 SPECIAL_CLOSE 允许携带")
        return self


class NotificationTaskOut(ORMModel):
    """站内通知与死信任务响应模型。"""

    id: int
    recipient_account_id: int
    event_type: str
    channel: NotificationChannel
    title: str
    body: str
    payload_json: dict[str, Any] | None = None
    related_ticket_id: int | None = None
    related_dispatch_id: int | None = None
    status: NotificationStatus
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None = None
    last_error: str | None = None
    sent_at: datetime | None = None
    is_read: bool
    read_at: datetime | None = None
    acknowledged_by_account_id: int | None = None
    acknowledged_at: datetime | None = None
    created_at: datetime


class AuditLogOut(ORMModel):
    """操作审计日志只读响应模型。"""

    id: int
    request_id: str | UUID
    actor_account_id: int | None = None
    actor_username: str | None = None
    actor_role_code: RoleCode | None = None
    action: str
    resource_type: str
    resource_id: str | int | None = None
    ticket_id: int | None = None
    method: str
    path: str
    request_payload: dict[str, Any] | None = None
    idempotency_key: str | None = None
    response_status: int
    error_code: str | None = None
    business_snapshot_json: dict[str, Any] | None = None
    client_ip: str | IPAddressValue | None = None
    created_at: datetime

    @field_validator("request_id", mode="after")
    @classmethod
    def _coerce_request_id(cls, v: str | UUID) -> str:
        return str(v)

    @field_validator("resource_id", mode="after")
    @classmethod
    def _coerce_resource_id(cls, v: str | int | None) -> str | None:
        return str(v) if v is not None else None

    @field_validator("client_ip", mode="after")
    @classmethod
    def _coerce_client_ip(cls, v: str | IPAddressValue | None) -> str | None:
        return str(v) if v is not None else None


class SlaScanResponseOut(ORMModel):
    """POST /sla/scan 响应模型。"""

    marked_overdue: int = 0
    timeout_reassigned: int = 0
    notifications_created: int = 0


class DashboardSummaryOut(ORMModel):
    """管理员看板汇总计数响应模型。"""

    pending_dispatch_count: int = 0
    in_repair_count: int = 0
    pending_acceptance_count: int = 0
    manual_review_count: int = 0
    sla_overdue_count: int = 0
    dead_letter_count: int = 0


__all__ = [
    "AcceptanceRecordOut",
    "AcceptanceResponseOut",
    "AcceptanceSubmitIn",
    "AuditLogOut",
    "DashboardSummaryOut",
    "NotificationTaskOut",
    "ReviewResolveIn",
    "ReworkOrderOut",
    "SlaScanResponseOut",
]
