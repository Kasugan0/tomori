export type RoleCode = 'ADMIN' | 'INSPECTOR' | 'TECHNICIAN'

export type ScopeType = 'INSPECT' | 'REPAIR' | 'MANAGE'

export type AccountStatus = 'ACTIVE' | 'DISABLED' | 'LOCKED'

export type TicketStatus =
  | 'AI_CANDIDATE_PENDING'
  | 'PENDING_DISPATCH'
  | 'DISPATCHED'
  | 'IN_REPAIR'
  | 'PENDING_ACCEPTANCE'
  | 'REWORK_PENDING_DISPATCH'
  | 'MANUAL_REVIEW'
  | 'CLOSED'
  | 'CANCELLED'

export type DispatchStatus =
  | 'PENDING_ACCEPT'
  | 'ACCEPTED'
  | 'REJECTED'
  | 'TIMEOUT_REASSIGNED'
  | 'COMPLETED'
  | 'ESCALATED'
  | 'CANCELLED'

export type ReworkStatus =
  | 'PENDING_DISPATCH'
  | 'IN_MANUAL_REVIEW'
  | 'DISPATCHED'
  | 'IN_REPAIR'
  | 'PENDING_ACCEPTANCE'
  | 'PASSED'
  | 'CLOSED_BY_REVIEW'
  | 'CANCELLED'

export type PriorityCode = 'P1' | 'P2' | 'P3' | 'P4'

export type FaultType =
  | 'LAMP_OUT'
  | 'FIXTURE_DAMAGED'
  | 'OCCLUDED'
  | 'ELECTRIC_HAZARD'
  | 'FLICKERING'
  | 'OTHER'

export type RoadLevel = 'EXPRESS' | 'MAIN' | 'BRANCH'

export type AssetImportance = 'CRITICAL' | 'HIGH' | 'NORMAL'

export type AssetType = 'LUMINAIRE' | 'POLE' | 'BRACKET' | 'CONTROL_BOX' | 'CABLE' | 'OTHER'

export type AssetStatus = 'IN_SERVICE' | 'FAULTY' | 'REPAIRING' | 'RETIRED'

export type LightingPointStatus = 'IN_SERVICE' | 'SUSPENDED' | 'RETIRED'

export type PowerSupplyType = 'GRID' | 'SOLAR' | 'OTHER'

export type ReportSource = 'AI_CANDIDATE' | 'MANUAL_INSPECT' | 'MANUAL_ADMIN'

export type EvidenceType =
  | 'INSPECTION_PHOTO'
  | 'ANNOTATED_PHOTO'
  | 'REPAIR_PHOTO'
  | 'ACCEPTANCE_PHOTO'

export type AcceptanceDecision = 'APPROVED' | 'REJECTED'

export type ReviewResolution = 'APPROVE_REASSIGN' | 'SPECIAL_CLOSE' | 'TERMINATE_CANCEL'

export type MaterialUnit = 'PCS' | 'M' | 'KG' | 'SET'

export type NotEligibleReason =
  | 'NO_REGION_SCOPE'
  | 'SCOPE_EXPIRED'
  | 'ACCOUNT_INACTIVE'
  | 'WRONG_ROLE'
  | 'OVERLOADED'

export type RuleCode =
  | 'grading_rules'
  | 'sla_policy'
  | 'dispatch_scoring_weights'
  | 'rework_policy'
  | 'ai_candidate_ttl_hours'

export type SceneLightingHint = 'auto' | 'day' | 'night' | 'twilight'

export type InferenceMode = 'yolo26_onnx_e2e' | 'yolo26_pt' | 'baseline_pretrained_opencv'

export type QualityGrade = 'pass' | 'warn' | 'fail'

export type FusionStatus =
  | 'consistent'
  | 'daytime_unpowered_suppressed'
  | 'low_confidence_conflict'
  | 'occlusion_merged'
  | 'baseline_heuristic'

export type VisualSignalState =
  | 'LAMP_OUT'
  | 'FIXTURE_DAMAGED'
  | 'OCCLUDED'
  | 'DAYTIME_ALWAYS_ON_SUSPECT'
  | 'NORMAL_LIT'
  | 'DAYTIME_UNPOWERED_EXPECTED'

export type RawClass =
  | 'streetlight_on'
  | 'streetlight_off'
  | 'streetlight_damaged'
  | 'streetlight_occluded'

export type CandidateDecision = 'pending' | 'confirmed' | 'modified' | 'rejected'

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages?: number
}

export interface ApiErrorDetails {
  existing_ticket_id?: number
  existing_ticket_no?: string
  existing_status?: string
  reason?: NotEligibleReason | string
  missing_items?: string[]
  file_index?: number
  size_bytes?: number
  fallback?: {
    action?: string
    resubmit_required?: boolean
    message_key?: string
  }
  analysis_stage?: string
  [key: string]: unknown
}

export interface ApiErrorPayload {
  code: string
  message?: string
  message_key?: string
  details: ApiErrorDetails | null
  request_id: string
  timestamp?: string
}

export interface ApiError {
  httpStatus: number
  error: ApiErrorPayload
}

export interface RoleOut {
  id: number
  code: RoleCode
  name: string
  description: string | null
}

export interface ScopeAuthorizationOut {
  id: number
  account_id: number
  region_id: number
  region_code?: string
  region_name?: string
  scope_type: ScopeType
  is_active: boolean
  valid_from: string
  valid_to: string | null
  granted_by_account_id: number | null
  remark: string | null
  created_at: string
}

export interface AccountOut {
  id: number
  username: string
  full_name: string
  employee_no: string | null
  phone: string | null
  email: string | null
  status: AccountStatus
  role_code: RoleCode
  skills: string[]
  max_active_dispatch: number
  token_version: number
  manager_account_id: number | null
  last_login_at: string | null
  created_at: string
  scope_authorizations?: ScopeAuthorizationOut[]
}

export interface AuthMeOut {
  account: AccountOut
  role_code: RoleCode
  scope_authorizations: ScopeAuthorizationOut[]
}

export interface LoginOut {
  access_token: string
  token_type: string
  expires_in: number
  account: AccountOut
}

export interface MaintenanceRegionOut {
  id: number
  region_code: string
  name: string
  parent_region_id: number | null
  level: number
  boundary_geojson: Record<string, unknown> | null
  description: string | null
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface LightingPointOut {
  id: number
  point_code: string
  region_id: number
  region_name?: string
  name: string
  road_name: string
  road_level: RoadLevel
  longitude: number | string
  latitude: number | string
  install_height_m: number | string | null
  lamp_count: number
  power_supply_type: PowerSupplyType
  status: LightingPointStatus
  description: string | null
  created_at: string
}

export interface FacilityAssetOut {
  id: number
  asset_code: string
  lighting_point_id: number
  point_code?: string
  asset_type: AssetType
  name: string
  model_spec: string | null
  rated_power_w: number | string | null
  importance: AssetImportance
  installed_at: string | null
  status: AssetStatus
  description: string | null
  created_at: string
}

export interface BusinessRuleOut {
  id: number
  rule_code: RuleCode
  version: number
  rule_value: Record<string, unknown>
  description: string | null
  is_active: boolean
  effective_from: string
  created_by_account_id: number | null
  created_at: string
}

export interface ImageQualityOut {
  quality_grade: QualityGrade
  laplacian_var: number
  brightness_mean_v: number
  overexposed_ratio: number
  underexposed_ratio: number
  scene_lighting: 'day' | 'night' | 'twilight'
  warnings: string[]
}

export interface OpencvRoiMetricsOut {
  roi_v_mean: number
  roi_v_p95: number
  bg_ring_v_median: number
  luminance_contrast: number
  green_foliage_ratio: number
  edge_density: number
  fusion_status: FusionStatus
}

export interface FaultCandidateOut {
  candidate_id: string
  raw_class: RawClass
  candidate_fault_type: 'LAMP_OUT' | 'FIXTURE_DAMAGED' | 'OCCLUDED' | 'OTHER' | null
  visual_signal_state: VisualSignalState
  is_fault_candidate: boolean
  bbox_norm: [number, number, number, number]
  bbox_px: [number, number, number, number]
  yolo_confidence: number
  fused_confidence: number
  suggested_severity_hint: 'none' | PriorityCode
  opencv_roi_metrics: OpencvRoiMetricsOut
  diagnostic_summary: string
  requires_inspector_confirmation: true
  replaces_electrical_safety_check: false
}

export interface FaultCandidateResultOut {
  evidence_id?: number
  analysis_id: string
  inference_mode: InferenceMode
  ai_is_baseline_mode: boolean
  model_version: string
  image_width: number
  image_height: number
  original_image_url: string
  annotated_image_url: string | null
  image_quality: ImageQualityOut
  primary_fault_type_suggestion: 'LAMP_OUT' | 'FIXTURE_DAMAGED' | 'OCCLUDED' | 'OTHER' | null
  max_fused_confidence: number
  candidates: FaultCandidateOut[]
  requires_inspector_confirmation: true
  replaces_electrical_safety_check: false
  disclaimer: string
}

export interface FaultEvidenceOut {
  id: number
  ticket_id: number | null
  analysis_id: string | null
  evidence_type: EvidenceType
  media_mime: string
  storage_path: string
  file_size_bytes: number
  sha256: string
  image_width: number
  image_height: number
  ai_result_json: FaultCandidateResultOut | null
  ai_is_baseline_mode: boolean
  uploaded_by_account_id: number
  url?: string
  raw_url?: string
  annotated_url?: string | null
  captured_at: string | null
  created_at: string
}

export interface SafetyChecklistPayload {
  power_isolated: boolean
  voltage_verified_zero: boolean
  insulation_intact: boolean
  grounding_connected: boolean
  ppe_worn: boolean
}

export interface MaterialUsageOut {
  id?: number
  repair_process_id?: number
  ticket_id?: number
  material_code: string
  material_name: string
  quantity: number
  unit: MaterialUnit
  unit_price: number | null
  created_at?: string
}

export interface RepairProcessOut {
  id: number
  dispatch_record_id: number
  ticket_id: number
  technician_account_id: number
  arrived_at: string
  electrical_safety_checked: boolean
  safety_checklist_json: SafetyChecklistPayload
  safety_note: string
  root_cause: string | null
  repair_summary: string
  repair_started_at: string | null
  repair_finished_at: string | null
  materials?: MaterialUsageOut[]
  created_at: string
  updated_at: string
}

export interface DispatchRecordOut {
  id: number
  ticket_id: number
  ticket_no?: string
  ticket_status?: TicketStatus
  ticket_priority?: PriorityCode | null
  ticket_fault_type?: FaultType
  ticket_title?: string
  point_code?: string
  point_name?: string
  region_id: number
  region_name?: string
  rework_order_id: number | null
  technician_account_id: number
  technician_username?: string
  technician_full_name?: string
  scope_authorization_id: number
  dispatched_by_account_id: number
  dispatch_mode: string
  status: DispatchStatus
  recommend_score: number | string | null
  dispatched_at: string
  accept_deadline_at: string
  repair_deadline_at?: string | null
  accepted_at: string | null
  rejected_at: string | null
  reject_reason: string | null
  completed_at: string | null
  escalated_at: string | null
  escalate_reason: string | null
  cancelled_at: string | null
  created_at: string
}

export interface AcceptanceRecordOut {
  id: number
  ticket_id: number
  repair_process_id: number
  rework_order_id: number | null
  inspector_account_id: number
  decision: AcceptanceDecision
  comment: string | null
  accepted_at: string
  created_at: string
}

export interface ReworkOrderOut {
  id: number
  rework_no: string
  ticket_id: number
  source_acceptance_id: number
  round_no: number
  region_id: number
  status: ReworkStatus
  reject_reason: string
  requires_manual_review: boolean
  manual_review_decided_by_account_id: number | null
  manual_review_decided_at: string | null
  manual_review_decision: ReviewResolution | null
  manual_review_note: string | null
  closed_at: string | null
  created_at: string
  updated_at: string
}

export interface FaultTicketOut {
  id: number
  ticket_no: string
  region_id: number
  region_name?: string
  lighting_point_id: number
  point_code?: string
  point_name?: string
  facility_asset_id: number | null
  report_source: ReportSource
  fault_type: FaultType
  title: string
  description: string | null
  status: TicketStatus
  priority: PriorityCode | null
  grading_score: number | string | null
  grading_snapshot_json: Record<string, unknown> | null
  reporter_account_id: number
  confirmed_by_inspector: boolean
  confirmed_by_inspector_id: number | null
  confirmed_at: string | null
  ai_suggestion_json: Record<string, unknown> | null
  ai_max_confidence: number | string | null
  response_deadline_at: string | null
  repair_deadline_at: string | null
  is_sla_overdue: boolean
  overdue_stage: 'RESPONSE' | 'REPAIR' | null
  overdue_marked_at: string | null
  rework_count: number
  dispatch_reject_count: number
  manual_review_reason: string | null
  cancel_reason: string | null
  cancelled_by_account_id: number | null
  cancelled_at: string | null
  closed_at: string | null
  created_at: string
  updated_at: string
}

export interface TimelineEntryOut {
  id: number
  action: string
  actor_username: string | null
  actor_role_code: RoleCode | null
  business_snapshot_json?: Record<string, unknown> | null
  created_at: string
}

export interface FaultTicketDetailOut extends FaultTicketOut {
  evidences: FaultEvidenceOut[]
  dispatches: DispatchRecordOut[]
  repair_processes: RepairProcessOut[]
  acceptances: AcceptanceRecordOut[]
  rework_orders: ReworkOrderOut[]
  timeline: TimelineEntryOut[]
}

export interface AiCandidatesResponseOut {
  ticket: FaultTicketOut | null
  evidences: Array<FaultEvidenceOut & Partial<FaultCandidateResultOut>>
}

export interface DispatchCandidateItem {
  account_id: number
  username: string
  full_name: string
  scope_authorization_id: number
  active_dispatch_count: number
  max_active_dispatch: number
  skill_match: boolean
  on_time_rate: number
  recommend_score: number
}

export interface DispatchExcludedItem {
  account_id: number
  username: string
  full_name: string
  reason: NotEligibleReason
  active_dispatch_count: number
  max_active_dispatch: number
}

export interface DispatchCandidatesResponseOut {
  ticket_id: number
  generated_at: string
  candidates: DispatchCandidateItem[]
  excluded: DispatchExcludedItem[]
}

export interface AcceptanceResponseOut {
  acceptance_id: number
  decision: AcceptanceDecision
  ticket: FaultTicketOut
  rework_order: ReworkOrderOut | null
}

export interface NotificationTaskOut {
  id: number
  recipient_account_id: number
  event_type: string
  channel: string
  title: string
  body: string
  payload_json: Record<string, unknown> | null
  related_ticket_id: number | null
  related_dispatch_id: number | null
  status: string
  attempt_count: number
  max_attempts: number
  next_retry_at: string | null
  last_error: string | null
  sent_at: string | null
  is_read: boolean
  read_at: string | null
  acknowledged_by_account_id: number | null
  acknowledged_at: string | null
  created_at: string
}

export interface AuditLogOut {
  id: number
  request_id: string
  actor_account_id: number | null
  actor_username: string | null
  actor_role_code: RoleCode | null
  action: string
  resource_type: string
  resource_id: string | null
  ticket_id: number | null
  method: string
  path: string
  request_payload: Record<string, unknown> | null
  idempotency_key: string | null
  response_status: number
  error_code: string | null
  business_snapshot_json: Record<string, unknown> | null
  client_ip: string | null
  created_at: string
}

export interface SlaScanResponseOut {
  marked_overdue: number
  timeout_reassigned: number
  notifications_created: number
}

export interface DashboardSummaryOut {
  pending_dispatch_count?: number
  in_repair_count?: number
  pending_acceptance_count?: number
  manual_review_count?: number
  sla_overdue_count?: number
  dead_letter_count?: number
  [key: string]: number | string | undefined
}
