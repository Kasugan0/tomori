"""17 张业务表 ORM 汇总（SQLAlchemy 2.0 声明式，物理建表拓扑序 1..17）。

任何 consumer（`migrations/env.py`、服务层、测试）只应 `import tomori.models`，
以确保 `Base.metadata` 收齐全部 17 张表、76 CHECK、41 外键列、22 UNIQUE、37 普通索引。
"""

from __future__ import annotations

from tomori.models.acceptance_record import AcceptanceRecord
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.business_rule import BusinessRule
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.facility_asset import FacilityAsset
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.models.material_usage import MaterialUsage
from tomori.models.notification_task import NotificationTask
from tomori.models.repair_process import RepairProcess
from tomori.models.rework_order import ReworkOrder
from tomori.models.role import Role
from tomori.models.scope_authorization import ScopeAuthorization

__all__ = [
    # 1. 运维区域（⑧）
    "MaintenanceRegion",
    # 2. 角色（②）
    "Role",
    # 3. 照明点位（⑨）
    "LightingPoint",
    # 4. 账号（①）
    "Account",
    # 5. 账号角色（③）
    "AccountRole",
    # 6. 业务范围授权（④）
    "ScopeAuthorization",
    # 7. 业务规则（⑤）
    "BusinessRule",
    # 8. 设施资产（⑩）
    "FacilityAsset",
    # 9. 故障单（⑪）
    "FaultTicket",
    # 10. 故障证据（⑫）
    "FaultEvidence",
    # 11. 派工记录（⑬）
    "DispatchRecord",
    # 12. 维修过程（⑭）
    "RepairProcess",
    # 13. 材料使用（⑮）
    "MaterialUsage",
    # 14. 返修单（⑰）
    "ReworkOrder",
    # 15. 验收记录（⑯）
    "AcceptanceRecord",
    # 16. 操作审计（⑦）
    "AuditLog",
    # 17. 通知任务（⑥）
    "NotificationTask",
]
