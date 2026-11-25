"""后台异步工作协程包（SLA 超时扫描 + AI 候选 TTL 清理 + 通知队列投递重试）。

采用惰性重导出以保持 `tomori.workers.notifier` 与 `tomori.workers.sla_scanner`
在同批次并发构建期间相互独立可测；静态类型检查通过 `TYPE_CHECKING` 完整覆盖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tomori.workers.notifier import (
        DEFAULT_NOTIFIER_BATCH_SIZE,
        DEFAULT_NOTIFIER_INTERVAL_SECONDS,
        PROCESSING_TIMEOUT_MINUTES,
        WORKER_PROCESSING_TIMEOUT_ERROR,
        ChannelDispatcher,
        DeliveryResult,
        compute_backoff_seconds,
        default_channel_dispatcher,
        notifier_loop,
        process_due_notifications_once,
        recover_stuck_processing_tasks,
    )
    from tomori.workers.sla_scanner import (
        DEFAULT_AI_CANDIDATE_TTL_HOURS,
        DEFAULT_ORPHAN_CLEANUP_AGE_HOURS,
        DEFAULT_ORPHAN_CLEANUP_BATCH_SIZE,
        cleanup_orphan_uploads_once,
        expire_stale_ai_candidates_once,
        run_sla_scan_once,
        sla_scanner_loop,
    )

__all__ = [
    "DEFAULT_AI_CANDIDATE_TTL_HOURS",
    "DEFAULT_NOTIFIER_BATCH_SIZE",
    "DEFAULT_NOTIFIER_INTERVAL_SECONDS",
    "DEFAULT_ORPHAN_CLEANUP_AGE_HOURS",
    "DEFAULT_ORPHAN_CLEANUP_BATCH_SIZE",
    "PROCESSING_TIMEOUT_MINUTES",
    "WORKER_PROCESSING_TIMEOUT_ERROR",
    "ChannelDispatcher",
    "DeliveryResult",
    "cleanup_orphan_uploads_once",
    "compute_backoff_seconds",
    "default_channel_dispatcher",
    "expire_stale_ai_candidates_once",
    "notifier_loop",
    "process_due_notifications_once",
    "recover_stuck_processing_tasks",
    "run_sla_scan_once",
    "sla_scanner_loop",
]

_NOTIFIER_EXPORTS = frozenset(
    {
        "DEFAULT_NOTIFIER_BATCH_SIZE",
        "DEFAULT_NOTIFIER_INTERVAL_SECONDS",
        "PROCESSING_TIMEOUT_MINUTES",
        "WORKER_PROCESSING_TIMEOUT_ERROR",
        "ChannelDispatcher",
        "DeliveryResult",
        "compute_backoff_seconds",
        "default_channel_dispatcher",
        "notifier_loop",
        "process_due_notifications_once",
        "recover_stuck_processing_tasks",
    }
)

_SLA_SCANNER_EXPORTS = frozenset(
    {
        "DEFAULT_AI_CANDIDATE_TTL_HOURS",
        "DEFAULT_ORPHAN_CLEANUP_AGE_HOURS",
        "DEFAULT_ORPHAN_CLEANUP_BATCH_SIZE",
        "cleanup_orphan_uploads_once",
        "expire_stale_ai_candidates_once",
        "run_sla_scan_once",
        "sla_scanner_loop",
    }
)


def __getattr__(name: str) -> Any:
    if name in _NOTIFIER_EXPORTS:
        from tomori.workers import notifier

        return getattr(notifier, name)
    if name in _SLA_SCANNER_EXPORTS:
        from tomori.workers import sla_scanner

        return getattr(sla_scanner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
