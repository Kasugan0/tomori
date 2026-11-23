from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime


class FaultEvidence(Base):
    """故障证据表（⑫ fault_evidences）。"""

    __tablename__ = "fault_evidences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="CASCADE"),
        nullable=True,
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(24), nullable=False)
    media_mime: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    image_width: Mapped[int] = mapped_column(Integer, nullable=False)
    image_height: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ai_is_baseline_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    uploaded_by_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    captured_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint("ticket_id", "sha256", name="uq_fault_evidences_sha256_per_ticket"),
        CheckConstraint(
            "evidence_type IN "
            "('INSPECTION_PHOTO', 'ANNOTATED_PHOTO', 'REPAIR_PHOTO', 'ACCEPTANCE_PHOTO')",
            name="evidence_type_enum",
        ),
        CheckConstraint(
            "media_mime IN ('image/jpeg', 'image/png', 'image/webp')",
            name="media_mime_static_image_only",
        ),
        CheckConstraint(
            "file_size_bytes > 0 AND file_size_bytes <= 15728640",
            name="file_size_range",
        ),
        CheckConstraint(
            "image_width > 0 AND image_height > 0 "
            "AND image_width <= 20000 AND image_height <= 20000",
            name="image_dimensions_positive",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_format",
        ),
        CheckConstraint(
            "ticket_id IS NOT NULL "
            "OR (evidence_type = 'INSPECTION_PHOTO' AND analysis_id IS NOT NULL)",
            name="ticket_or_orphan_inspection",
        ),
        CheckConstraint(
            "ai_is_baseline_mode = false "
            "OR ai_result_json IS NULL "
            "OR coalesce((ai_result_json->>'max_fused_confidence')::numeric, 0) <= 0.65",
            name="baseline_confidence_cap",
        ),
        CheckConstraint(
            "ai_result_json IS NULL OR ("
            "(ai_result_json->>'requires_inspector_confirmation')::boolean IS TRUE "
            "AND (ai_result_json->>'replaces_electrical_safety_check')::boolean IS FALSE"
            ")",
            name="ai_safety_flags_required",
        ),
        Index("ix_fault_evidences_ticket_id_type", "ticket_id", "evidence_type"),
    )
