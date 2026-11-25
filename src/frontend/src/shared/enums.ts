import type {
  AssetImportance,
  AssetStatus,
  AssetType,
  DispatchStatus,
  FaultType,
  LightingPointStatus,
  MaterialUnit,
  NotEligibleReason,
  PowerSupplyType,
  PriorityCode,
  ReviewResolution,
  ReworkStatus,
  RoadLevel,
  RoleCode,
  RuleCode,
  SafetyChecklistPayload,
  ScopeType,
  TicketStatus,
} from './types'

export const ROLE_LABELS: Record<RoleCode, string> = {
  ADMIN: '运维管理员',
  INSPECTOR: '巡查员',
  TECHNICIAN: '维修人员',
}

export const SCOPE_TYPE_LABELS: Record<ScopeType, string> = {
  INSPECT: '巡查/验收权 (INSPECT)',
  REPAIR: '维修接单权 (REPAIR)',
  MANAGE: '派工管理权 (MANAGE)',
}

export interface StatusDisplayMeta {
  label: string
  tagType: 'primary' | 'success' | 'warning' | 'danger' | 'info'
}

export const TICKET_STATUS_META: Record<TicketStatus, StatusDisplayMeta> = {
  AI_CANDIDATE_PENDING: { label: '待确认候选', tagType: 'warning' },
  PENDING_DISPATCH: { label: '待派工', tagType: 'warning' },
  DISPATCHED: { label: '已派工待接单', tagType: 'primary' },
  IN_REPAIR: { label: '维修中', tagType: 'primary' },
  PENDING_ACCEPTANCE: { label: '待验收', tagType: 'warning' },
  REWORK_PENDING_DISPATCH: { label: '返修待派工', tagType: 'danger' },
  MANUAL_REVIEW: { label: '人工复核中', tagType: 'danger' },
  CLOSED: { label: '已关闭', tagType: 'success' },
  CANCELLED: { label: '已撤销', tagType: 'info' },
}

export const DISPATCH_STATUS_META: Record<DispatchStatus, StatusDisplayMeta> = {
  PENDING_ACCEPT: { label: '待接单', tagType: 'warning' },
  ACCEPTED: { label: '维修中', tagType: 'primary' },
  REJECTED: { label: '已拒单', tagType: 'danger' },
  TIMEOUT_REASSIGNED: { label: '超时已回收', tagType: 'danger' },
  COMPLETED: { label: '已完工', tagType: 'success' },
  ESCALATED: { label: '已升级', tagType: 'danger' },
  CANCELLED: { label: '已取消', tagType: 'info' },
}

export const REWORK_STATUS_META: Record<ReworkStatus, StatusDisplayMeta> = {
  PENDING_DISPATCH: { label: '待派工', tagType: 'warning' },
  IN_MANUAL_REVIEW: { label: '待人工复核', tagType: 'danger' },
  DISPATCHED: { label: '已派工', tagType: 'primary' },
  IN_REPAIR: { label: '返修中', tagType: 'primary' },
  PENDING_ACCEPTANCE: { label: '待验收', tagType: 'warning' },
  PASSED: { label: '返修通过', tagType: 'success' },
  CLOSED_BY_REVIEW: { label: '复核结案', tagType: 'info' },
  CANCELLED: { label: '已取消', tagType: 'info' },
}

export const PRIORITY_META: Record<
  PriorityCode,
  {
    code: PriorityCode
    label: string
    tagType: 'danger' | 'warning' | 'primary' | 'info'
    effect: 'dark' | 'plain' | 'light'
  }
> = {
  P1: { code: 'P1', label: '特急', tagType: 'danger', effect: 'dark' },
  P2: { code: 'P2', label: '加急', tagType: 'warning', effect: 'light' },
  P3: { code: 'P3', label: '普通', tagType: 'primary', effect: 'plain' },
  P4: { code: 'P4', label: '观察', tagType: 'info', effect: 'plain' },
}

export const FAULT_TYPE_LABELS: Record<FaultType, string> = {
  LAMP_OUT: '灭灯',
  FIXTURE_DAMAGED: '灯具破损',
  OCCLUDED: '遮挡',
  ELECTRIC_HAZARD: '电气安全隐患',
  FLICKERING: '闪烁',
  OTHER: '其他',
}

export const AI_ALLOWED_FAULT_TYPES: ReadonlyArray<'LAMP_OUT' | 'FIXTURE_DAMAGED' | 'OCCLUDED' | 'OTHER'> = [
  'LAMP_OUT',
  'FIXTURE_DAMAGED',
  'OCCLUDED',
  'OTHER',
]

export const MANUAL_NOTE_REQUIRED_FAULT_TYPES: ReadonlyArray<FaultType> = [
  'FLICKERING',
  'ELECTRIC_HAZARD',
]

export const ROAD_LEVEL_LABELS: Record<RoadLevel, string> = {
  EXPRESS: '快速路',
  MAIN: '主干道',
  BRANCH: '支路',
}

export const ASSET_IMPORTANCE_LABELS: Record<AssetImportance, string> = {
  CRITICAL: '关键 (CRITICAL)',
  HIGH: '重要 (HIGH)',
  NORMAL: '普通 (NORMAL)',
}

export const ASSET_TYPE_LABELS: Record<AssetType, string> = {
  LUMINAIRE: '灯具',
  POLE: '灯杆',
  BRACKET: '灯臂',
  CONTROL_BOX: '配电箱',
  CABLE: '线缆',
  OTHER: '其他',
}

export const ASSET_STATUS_LABELS: Record<AssetStatus, string> = {
  IN_SERVICE: '在役',
  FAULTY: '故障中',
  REPAIRING: '维修中',
  RETIRED: '退役',
}

export const POINT_STATUS_LABELS: Record<LightingPointStatus, string> = {
  IN_SERVICE: '在用',
  SUSPENDED: '暂停',
  RETIRED: '退役',
}

export const POWER_SUPPLY_LABELS: Record<PowerSupplyType, string> = {
  GRID: '市电供电',
  SOLAR: '太阳能',
  OTHER: '其他',
}

export const NOT_ELIGIBLE_REASON_TEXT: Record<NotEligibleReason, string> = {
  NO_REGION_SCOPE: '该维修人员未获此运维区域的维修授权',
  SCOPE_EXPIRED: '该维修人员的区域授权已过期',
  ACCOUNT_INACTIVE: '该账号已停用',
  WRONG_ROLE: '该账号不是维修人员',
  OVERLOADED: '该维修人员当前在途工单已满',
}

export function getNotEligibleReasonText(reason: string | undefined): string {
  if (!reason) return '不符合派工条件'
  return (NOT_ELIGIBLE_REASON_TEXT as Record<string, string>)[reason] ?? reason
}

export const RULE_CODE_META: Record<
  RuleCode,
  { label: string; summary: string }
> = {
  grading_rules: {
    label: '故障确定性分级规则 (grading_rules)',
    summary: '基于故障类型、道路等级、资产重要度与安全标志的表驱动分级（P4 兜底）',
  },
  sla_policy: {
    label: '郑州法定 48 小时时限策略 (sla_policy)',
    summary: '郑州市令第 152 号第 19 条第 3 款 48h（2880 分钟）基准与 24h 安全告知时限',
  },
  dispatch_scoring_weights: {
    label: '区域派单推荐权重 (dispatch_scoring_weights)',
    summary: '维修员推荐排序权重（负载/技能/准时率之和严格等于 1.0）',
  },
  rework_policy: {
    label: '返修轮次与强制复核策略 (rework_policy)',
    summary: '最大返修轮次（默认 2 轮），超阈值触发 FORCE_ADMIN_REVIEW',
  },
  ai_candidate_ttl_hours: {
    label: 'AI 待确认候选过期时间 (ai_candidate_ttl_hours)',
    summary: 'AI_CANDIDATE_PENDING 未确认自动过期小时数（默认 72h）',
  },
}

export const ALL_RULE_CODES: ReadonlyArray<RuleCode> = [
  'grading_rules',
  'sla_policy',
  'dispatch_scoring_weights',
  'rework_policy',
  'ai_candidate_ttl_hours',
]

export const SAFETY_CHECKLIST_ITEMS: ReadonlyArray<{
  key: keyof SafetyChecklistPayload
  label: string
  testid: string
}> = [
  { key: 'power_isolated', label: '已断电隔离并挂牌', testid: 'repair-safety-item-power-isolated' },
  { key: 'voltage_verified_zero', label: '验电笔确认无残余电压', testid: 'repair-safety-item-voltage-verified-zero' },
  { key: 'insulation_intact', label: '绝缘层及线缆包扎完好', testid: 'repair-safety-item-insulation-intact' },
  { key: 'grounding_connected', label: '金属灯杆接地连接可靠', testid: 'repair-safety-item-grounding-connected' },
  { key: 'ppe_worn', label: '已按规范佩戴绝缘劳保用品', testid: 'repair-safety-item-ppe-worn' },
]

export const MATERIAL_UNITS: ReadonlyArray<{ value: MaterialUnit; label: string }> = [
  { value: 'PCS', label: '个/只 (PCS)' },
  { value: 'M', label: '米 (M)' },
  { value: 'KG', label: '千克 (KG)' },
  { value: 'SET', label: '套 (SET)' },
]

export const REVIEW_RESOLUTION_LABELS: Record<ReviewResolution, string> = {
  APPROVE_REASSIGN: '核准重新派工 (APPROVE_REASSIGN)',
  SPECIAL_CLOSE: '特批关闭结案 (SPECIAL_CLOSE，理由不少于 10 字)',
  TERMINATE_CANCEL: '终止并撤销工单 (TERMINATE_CANCEL)',
}

export const VISION_FALLBACK_MESSAGES: Record<string, string> = {
  'vision.disabled.manual_fallback': '图像识别功能当前未启用，请直接填写人工报障单。',
  'vision.timeout.manual_fallback': '图像识别超时，请直接填写人工报障单或稍后重试识别。',
  'vision.failed.manual_fallback': '图像识别失败，请直接填写人工报障单。',
}

export const ERROR_CODE_TEXT: Record<string, string> = {
  INVALID_CREDENTIALS: '用户名或密码错误',
  TOKEN_EXPIRED: '登录已过期，请重新登录',
  TOKEN_INVALID: '登录凭证无效或已被吊销，请重新登录',
  TOKEN_MISSING: '缺少登录凭证，请先登录',
  FORBIDDEN_ROLE: '你的角色无权执行此操作',
  FORBIDDEN_REGION_SCOPE: '你未被授权操作该运维区域',
  ACCOUNT_DISABLED: '当前账号已被停用或锁定',
  RESOURCE_NOT_FOUND: '请求的资源不存在或已被移除',
  EVIDENCE_NOT_ANNOTATED: '该证据暂无标注图',
  DUPLICATE_ACTIVE_TICKET: '该照明点位已存在未关闭的同类型活跃故障工单',
  IDEMPOTENCY_REQUEST_IN_PROGRESS: '正在提交中，请稍候原样重试',
  INVALID_STATE_TRANSITION: '工单当前状态不允许此操作，请刷新后重试',
  AI_CANDIDATE_NOT_CONFIRMED: '请先完成 AI 候选的人工确认，禁止直接派工',
  ELECTRICAL_SAFETY_CHECK_REQUIRED: '请先完成全部 5 项现场断电与电气安全检查并填写检查说明',
  DISPATCH_TECHNICIAN_NOT_ELIGIBLE: '所选维修人员不具备派工资格',
  TECHNICIAN_OVERLOADED: '该维修人员当前在途工单已达上限',
  POINT_HAS_ACTIVE_TICKET: '该点位存在活跃故障工单，暂不允许变更所属区域',
  RULE_VALUE_IMMUTABLE: '业务规则载荷不可原地修改，请通过提交新版本变更',
  INVALID_RULE_PAYLOAD: '业务规则载荷校验失败，请检查字段与权重配置',
  IDEMPOTENCY_PAYLOAD_MISMATCH: '同幂等键下请求内容已变更，已重置提交状态，请重新提交',
  VALIDATION_ERROR: '请求参数校验失败，请检查表单输入',
  IDEMPOTENCY_KEY_REQUIRED: '缺少幂等提交标识 (Idempotency-Key)',
  IDEMPOTENCY_KEY_INVALID: '幂等提交标识格式不合规',
  IMAGE_SIZE_EXCEEDED: '单张图片超过 15 MiB（15,728,640 B），请压缩或删减超限图片后重传',
  UNSUPPORTED_IMAGE_FORMAT: '仅支持静态 JPEG / PNG / WebP 图片（不支持动图或损坏文件）',
  MEDIA_VIDEO_STREAM_FORBIDDEN: '仅支持静态巡查图片，严禁上传视频文件或实时视频流',
  IMAGE_PIXEL_LIMIT_EXCEEDED: '图片分辨率总像素超过 40 MP 上限，请缩小后重传',
  TOO_MANY_FILES: '单次最多上传 10 张静态图片，请删减后重试',
  NO_FILES: '请至少选择 1 张静态巡查图片',
  INTERNAL_ERROR: '服务端发生异常，已记录请求编号',
  SERVICE_UNAVAILABLE: '服务暂不可用，请稍后重试',
  VISION_DISABLED: '图像识别功能当前未启用，可直接切换人工报障',
  VISION_TIMEOUT: '图像识别超时，可直接填写人工报障单或稍后重试',
  VISION_INFERENCE_FAILED: '图像识别处理失败，可直接填写人工报障单',
}
