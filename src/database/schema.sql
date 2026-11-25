-- ============================================================================
-- Tomori (#141 公共照明故障工单系统) — 数据库建表脚本（schema.sql）
-- ----------------------------------------------------------------------------
-- 由 `migrations/`（0001_initial_schema + 0002_partial_unique_indexes +
-- 0003_circular_fk_scheme_b + 0004_business_rules_seed）经 `alembic upgrade head`
-- 后 `pg_dump --schema-only` 汇总导出；可直接 `psql -f src/database/schema.sql`
-- 重建与 Alembic 一致的完整结构（不含 `alembic_version` 版本台账，由 Alembic 自管）。
--
-- 规模（实测口径）：17 业务表 / 76 CHECK / 41 FOREIGN KEY / 16 UNIQUE 约束 +
-- 43 INDEX（含 6 partial UNIQUE INDEX）/ 17 SEQUENCE。
--
-- 数据源：docker compose `tomori-postgres`（postgres:17.12-alpine，127.0.0.1:25432）
-- 使用：psql -h 127.0.0.1 -p 25432 -U tomori -d <new_db> -f src/database/schema.sql
-- ============================================================================
--
-- PostgreSQL database dump
--

\restrict YJH5lFwGQvBWo4etgUi6vqd4G5iXQScquKVUMHhKxnLl9WQAJUzJD4zeRShsP3y

-- Dumped from database version 17.12
-- Dumped by pg_dump version 18.7

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: acceptance_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.acceptance_records (
    id bigint NOT NULL,
    ticket_id bigint NOT NULL,
    repair_process_id bigint NOT NULL,
    rework_order_id bigint,
    inspector_account_id bigint NOT NULL,
    decision character varying(16) NOT NULL,
    comment character varying(512),
    accepted_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_acceptance_records_decision_enum CHECK (((decision)::text = ANY ((ARRAY['APPROVED'::character varying, 'REJECTED'::character varying])::text[]))),
    CONSTRAINT ck_acceptance_records_reject_comment_required CHECK ((((decision)::text <> 'REJECTED'::text) OR ((comment IS NOT NULL) AND (length(btrim((comment)::text)) > 0)))),
    CONSTRAINT ck_acceptance_records_rework_linked_on_reject CHECK ((((decision)::text = 'REJECTED'::text) = (rework_order_id IS NOT NULL)))
);


--
-- Name: acceptance_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.acceptance_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: acceptance_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.acceptance_records_id_seq OWNED BY public.acceptance_records.id;


--
-- Name: account_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_roles (
    id bigint NOT NULL,
    account_id bigint NOT NULL,
    role_id smallint NOT NULL,
    granted_by_account_id bigint,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: account_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.account_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: account_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.account_roles_id_seq OWNED BY public.account_roles.id;


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id bigint NOT NULL,
    username character varying(64) NOT NULL,
    password_hash character varying(255) NOT NULL,
    full_name character varying(64) NOT NULL,
    employee_no character varying(32),
    phone character varying(32),
    email character varying(128),
    status character varying(16) DEFAULT 'ACTIVE'::character varying NOT NULL,
    skills text[] DEFAULT '{}'::text[] NOT NULL,
    max_active_dispatch smallint DEFAULT 3 NOT NULL,
    token_version integer DEFAULT 1 NOT NULL,
    manager_account_id bigint,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_accounts_max_active_dispatch_range CHECK (((max_active_dispatch >= 1) AND (max_active_dispatch <= 20))),
    CONSTRAINT ck_accounts_status_enum CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'DISABLED'::character varying, 'LOCKED'::character varying])::text[]))),
    CONSTRAINT ck_accounts_token_version_positive CHECK ((token_version >= 1)),
    CONSTRAINT ck_accounts_username_format CHECK (((username)::text ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'::text))
);


--
-- Name: accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.accounts_id_seq OWNED BY public.accounts.id;


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id bigint NOT NULL,
    request_id uuid NOT NULL,
    actor_account_id bigint,
    actor_username character varying(64),
    actor_role_code character varying(32),
    action character varying(64) NOT NULL,
    resource_type character varying(32) NOT NULL,
    resource_id bigint,
    ticket_id bigint,
    method character varying(8) NOT NULL,
    path character varying(256) NOT NULL,
    request_payload jsonb,
    request_payload_hash character(64),
    idempotency_key character varying(128),
    response_status smallint NOT NULL,
    error_code character varying(64),
    business_snapshot_json jsonb,
    response_body_json jsonb,
    client_ip inet,
    user_agent character varying(256),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_audit_logs_error_code_iff_failure CHECK (((response_status < 400) OR (error_code IS NOT NULL))),
    CONSTRAINT ck_audit_logs_method_enum CHECK (((method)::text = ANY ((ARRAY['GET'::character varying, 'POST'::character varying, 'PUT'::character varying, 'PATCH'::character varying, 'DELETE'::character varying])::text[]))),
    CONSTRAINT ck_audit_logs_payload_hash_format CHECK (((request_payload_hash IS NULL) OR (request_payload_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_audit_logs_response_status_valid CHECK (((response_status >= 100) AND (response_status <= 599)))
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_logs_id_seq OWNED BY public.audit_logs.id;


--
-- Name: business_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.business_rules (
    id bigint NOT NULL,
    rule_code character varying(64) NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    rule_value jsonb NOT NULL,
    description character varying(512),
    is_active boolean DEFAULT true NOT NULL,
    effective_from timestamp with time zone DEFAULT now() NOT NULL,
    created_by_account_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_business_rules_rule_value_is_object CHECK ((jsonb_typeof(rule_value) = 'object'::text)),
    CONSTRAINT ck_business_rules_version_positive CHECK ((version >= 1))
);


--
-- Name: business_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.business_rules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: business_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.business_rules_id_seq OWNED BY public.business_rules.id;


--
-- Name: dispatch_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dispatch_records (
    id bigint NOT NULL,
    ticket_id bigint NOT NULL,
    rework_order_id bigint,
    region_id bigint NOT NULL,
    technician_account_id bigint NOT NULL,
    scope_authorization_id bigint NOT NULL,
    dispatched_by_account_id bigint NOT NULL,
    dispatch_mode character varying(16) NOT NULL,
    status character varying(24) DEFAULT 'PENDING_ACCEPT'::character varying NOT NULL,
    recommend_score numeric(6,4),
    candidate_snapshot_json jsonb,
    dispatched_at timestamp with time zone DEFAULT now() NOT NULL,
    accept_deadline_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    rejected_at timestamp with time zone,
    reject_reason character varying(256),
    completed_at timestamp with time zone,
    escalated_at timestamp with time zone,
    escalate_reason character varying(256),
    cancelled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_dispatch_records_accepted_at_required CHECK ((((status)::text = ANY ((ARRAY['ACCEPTED'::character varying, 'COMPLETED'::character varying])::text[])) = (accepted_at IS NOT NULL))),
    CONSTRAINT ck_dispatch_records_completed_at_required CHECK ((((status)::text = 'COMPLETED'::text) = (completed_at IS NOT NULL))),
    CONSTRAINT ck_dispatch_records_dispatch_mode_enum CHECK (((dispatch_mode)::text = ANY ((ARRAY['MANUAL'::character varying, 'AUTO_RECOMMENDED'::character varying, 'REASSIGN'::character varying])::text[]))),
    CONSTRAINT ck_dispatch_records_escalate_reason_required CHECK ((((status)::text <> ALL ((ARRAY['ESCALATED'::character varying, 'TIMEOUT_REASSIGNED'::character varying])::text[])) OR (escalated_at IS NOT NULL))),
    CONSTRAINT ck_dispatch_records_recommend_score_range CHECK (((recommend_score IS NULL) OR (recommend_score >= (0)::numeric))),
    CONSTRAINT ck_dispatch_records_reject_reason_required CHECK ((((status)::text <> 'REJECTED'::text) OR ((reject_reason IS NOT NULL) AND (rejected_at IS NOT NULL)))),
    CONSTRAINT ck_dispatch_records_status_enum CHECK (((status)::text = ANY ((ARRAY['PENDING_ACCEPT'::character varying, 'ACCEPTED'::character varying, 'REJECTED'::character varying, 'TIMEOUT_REASSIGNED'::character varying, 'COMPLETED'::character varying, 'ESCALATED'::character varying, 'CANCELLED'::character varying])::text[])))
);


--
-- Name: dispatch_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dispatch_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dispatch_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dispatch_records_id_seq OWNED BY public.dispatch_records.id;


--
-- Name: facility_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facility_assets (
    id bigint NOT NULL,
    asset_code character varying(64) NOT NULL,
    lighting_point_id bigint NOT NULL,
    asset_type character varying(24) NOT NULL,
    name character varying(128) NOT NULL,
    model_spec character varying(128),
    rated_power_w numeric(8,2),
    importance character varying(16) DEFAULT 'NORMAL'::character varying NOT NULL,
    installed_at date,
    status character varying(16) DEFAULT 'IN_SERVICE'::character varying NOT NULL,
    description character varying(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_facility_assets_asset_type_enum CHECK (((asset_type)::text = ANY ((ARRAY['LUMINAIRE'::character varying, 'POLE'::character varying, 'BRACKET'::character varying, 'CONTROL_BOX'::character varying, 'CABLE'::character varying, 'OTHER'::character varying])::text[]))),
    CONSTRAINT ck_facility_assets_importance_enum CHECK (((importance)::text = ANY ((ARRAY['CRITICAL'::character varying, 'HIGH'::character varying, 'NORMAL'::character varying])::text[]))),
    CONSTRAINT ck_facility_assets_rated_power_non_negative CHECK (((rated_power_w IS NULL) OR (rated_power_w >= (0)::numeric))),
    CONSTRAINT ck_facility_assets_status_enum CHECK (((status)::text = ANY ((ARRAY['IN_SERVICE'::character varying, 'FAULTY'::character varying, 'REPAIRING'::character varying, 'RETIRED'::character varying])::text[])))
);


--
-- Name: facility_assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.facility_assets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: facility_assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.facility_assets_id_seq OWNED BY public.facility_assets.id;


--
-- Name: fault_evidences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fault_evidences (
    id bigint NOT NULL,
    ticket_id bigint,
    analysis_id uuid,
    evidence_type character varying(24) NOT NULL,
    media_mime character varying(32) NOT NULL,
    storage_path character varying(512) NOT NULL,
    file_size_bytes bigint NOT NULL,
    sha256 character(64) NOT NULL,
    image_width integer NOT NULL,
    image_height integer NOT NULL,
    ai_result_json jsonb,
    ai_is_baseline_mode boolean DEFAULT false NOT NULL,
    uploaded_by_account_id bigint NOT NULL,
    captured_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fault_evidences_ai_safety_flags_required CHECK (((ai_result_json IS NULL) OR ((((ai_result_json ->> 'requires_inspector_confirmation'::text))::boolean IS TRUE) AND (((ai_result_json ->> 'replaces_electrical_safety_check'::text))::boolean IS FALSE)))),
    CONSTRAINT ck_fault_evidences_baseline_confidence_cap CHECK (((ai_is_baseline_mode = false) OR (ai_result_json IS NULL) OR (COALESCE(((ai_result_json ->> 'max_fused_confidence'::text))::numeric, (0)::numeric) <= 0.65))),
    CONSTRAINT ck_fault_evidences_evidence_type_enum CHECK (((evidence_type)::text = ANY ((ARRAY['INSPECTION_PHOTO'::character varying, 'ANNOTATED_PHOTO'::character varying, 'REPAIR_PHOTO'::character varying, 'ACCEPTANCE_PHOTO'::character varying])::text[]))),
    CONSTRAINT ck_fault_evidences_file_size_range CHECK (((file_size_bytes > 0) AND (file_size_bytes <= 15728640))),
    CONSTRAINT ck_fault_evidences_image_dimensions_positive CHECK (((image_width > 0) AND (image_height > 0) AND (image_width <= 20000) AND (image_height <= 20000))),
    CONSTRAINT ck_fault_evidences_media_mime_static_image_only CHECK (((media_mime)::text = ANY ((ARRAY['image/jpeg'::character varying, 'image/png'::character varying, 'image/webp'::character varying])::text[]))),
    CONSTRAINT ck_fault_evidences_sha256_format CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_fault_evidences_ticket_or_orphan_inspection CHECK (((ticket_id IS NOT NULL) OR (((evidence_type)::text = 'INSPECTION_PHOTO'::text) AND (analysis_id IS NOT NULL))))
);


--
-- Name: fault_evidences_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fault_evidences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fault_evidences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fault_evidences_id_seq OWNED BY public.fault_evidences.id;


--
-- Name: fault_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fault_tickets (
    id bigint NOT NULL,
    ticket_no character varying(32) NOT NULL,
    region_id bigint NOT NULL,
    lighting_point_id bigint NOT NULL,
    facility_asset_id bigint,
    report_source character varying(24) NOT NULL,
    fault_type character varying(24) NOT NULL,
    title character varying(128) NOT NULL,
    description text,
    status character varying(32) NOT NULL,
    priority character varying(4),
    grading_score numeric(6,2),
    grading_snapshot_json jsonb,
    reporter_account_id bigint NOT NULL,
    confirmed_by_inspector boolean DEFAULT false NOT NULL,
    confirmed_by_inspector_id bigint,
    confirmed_at timestamp with time zone,
    ai_suggestion_json jsonb,
    ai_max_confidence numeric(4,3),
    response_deadline_at timestamp with time zone,
    repair_deadline_at timestamp with time zone,
    is_sla_overdue boolean DEFAULT false NOT NULL,
    overdue_stage character varying(16),
    overdue_marked_at timestamp with time zone,
    rework_count smallint DEFAULT 0 NOT NULL,
    dispatch_reject_count smallint DEFAULT 0 NOT NULL,
    manual_review_reason character varying(256),
    cancel_reason character varying(256),
    cancelled_by_account_id bigint,
    cancelled_at timestamp with time zone,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fault_tickets_ai_max_confidence_range CHECK (((ai_max_confidence IS NULL) OR ((ai_max_confidence >= (0)::numeric) AND (ai_max_confidence <= (1)::numeric)))),
    CONSTRAINT ck_fault_tickets_ai_source_excludes_unsupported_types CHECK ((NOT (((report_source)::text = 'AI_CANDIDATE'::text) AND ((fault_type)::text = ANY ((ARRAY['FLICKERING'::character varying, 'ELECTRIC_HAZARD'::character varying])::text[]))))),
    CONSTRAINT ck_fault_tickets_cancel_fields_consistent CHECK (((((status)::text = 'CANCELLED'::text) AND (cancel_reason IS NOT NULL) AND (cancelled_by_account_id IS NOT NULL) AND (cancelled_at IS NOT NULL)) OR (((status)::text <> 'CANCELLED'::text) AND (cancel_reason IS NULL) AND (cancelled_by_account_id IS NULL) AND (cancelled_at IS NULL)))),
    CONSTRAINT ck_fault_tickets_closed_at_consistent CHECK ((((status)::text = 'CLOSED'::text) = (closed_at IS NOT NULL))),
    CONSTRAINT ck_fault_tickets_counters_non_negative CHECK (((rework_count >= 0) AND (dispatch_reject_count >= 0))),
    CONSTRAINT ck_fault_tickets_fault_type_enum CHECK (((fault_type)::text = ANY ((ARRAY['LAMP_OUT'::character varying, 'FIXTURE_DAMAGED'::character varying, 'OCCLUDED'::character varying, 'ELECTRIC_HAZARD'::character varying, 'FLICKERING'::character varying, 'OTHER'::character varying])::text[]))),
    CONSTRAINT ck_fault_tickets_grading_score_range CHECK (((grading_score IS NULL) OR ((grading_score >= (0)::numeric) AND (grading_score <= (100)::numeric)))),
    CONSTRAINT ck_fault_tickets_inspector_confirmation_serialized CHECK ((((confirmed_by_inspector = false) AND (confirmed_by_inspector_id IS NULL) AND (confirmed_at IS NULL) AND ((status)::text = 'AI_CANDIDATE_PENDING'::text)) OR ((confirmed_by_inspector = true) AND (confirmed_by_inspector_id IS NOT NULL) AND (confirmed_at IS NOT NULL) AND ((status)::text <> 'AI_CANDIDATE_PENDING'::text)))),
    CONSTRAINT ck_fault_tickets_manual_review_reason_required CHECK ((((status)::text <> 'MANUAL_REVIEW'::text) OR (manual_review_reason IS NOT NULL))),
    CONSTRAINT ck_fault_tickets_overdue_consistent CHECK ((((is_sla_overdue = true) AND (overdue_stage IS NOT NULL) AND (overdue_marked_at IS NOT NULL)) OR ((is_sla_overdue = false) AND (overdue_stage IS NULL) AND (overdue_marked_at IS NULL)))),
    CONSTRAINT ck_fault_tickets_overdue_stage_enum CHECK (((overdue_stage IS NULL) OR ((overdue_stage)::text = ANY ((ARRAY['RESPONSE'::character varying, 'REPAIR'::character varying])::text[])))),
    CONSTRAINT ck_fault_tickets_priority_enum CHECK (((priority IS NULL) OR ((priority)::text = ANY ((ARRAY['P1'::character varying, 'P2'::character varying, 'P3'::character varying, 'P4'::character varying])::text[])))),
    CONSTRAINT ck_fault_tickets_report_source_enum CHECK (((report_source)::text = ANY ((ARRAY['AI_CANDIDATE'::character varying, 'MANUAL_INSPECT'::character varying, 'MANUAL_ADMIN'::character varying])::text[]))),
    CONSTRAINT ck_fault_tickets_status_enum CHECK (((status)::text = ANY ((ARRAY['AI_CANDIDATE_PENDING'::character varying, 'PENDING_DISPATCH'::character varying, 'DISPATCHED'::character varying, 'IN_REPAIR'::character varying, 'PENDING_ACCEPTANCE'::character varying, 'REWORK_PENDING_DISPATCH'::character varying, 'MANUAL_REVIEW'::character varying, 'CLOSED'::character varying, 'CANCELLED'::character varying])::text[])))
);


--
-- Name: fault_tickets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fault_tickets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fault_tickets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fault_tickets_id_seq OWNED BY public.fault_tickets.id;


--
-- Name: lighting_points; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lighting_points (
    id bigint NOT NULL,
    point_code character varying(64) NOT NULL,
    region_id bigint NOT NULL,
    name character varying(128) NOT NULL,
    road_name character varying(128) NOT NULL,
    road_level character varying(16) DEFAULT 'BRANCH'::character varying NOT NULL,
    longitude numeric(10,7) NOT NULL,
    latitude numeric(10,7) NOT NULL,
    install_height_m numeric(5,2),
    lamp_count smallint DEFAULT 1 NOT NULL,
    power_supply_type character varying(16) DEFAULT 'GRID'::character varying NOT NULL,
    status character varying(16) DEFAULT 'IN_SERVICE'::character varying NOT NULL,
    description character varying(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_lighting_points_lamp_count_positive CHECK ((lamp_count >= 1)),
    CONSTRAINT ck_lighting_points_latitude_range CHECK (((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric))),
    CONSTRAINT ck_lighting_points_longitude_range CHECK (((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric))),
    CONSTRAINT ck_lighting_points_power_supply_type_enum CHECK (((power_supply_type)::text = ANY ((ARRAY['GRID'::character varying, 'SOLAR'::character varying, 'OTHER'::character varying])::text[]))),
    CONSTRAINT ck_lighting_points_road_level_enum CHECK (((road_level)::text = ANY ((ARRAY['EXPRESS'::character varying, 'MAIN'::character varying, 'BRANCH'::character varying])::text[]))),
    CONSTRAINT ck_lighting_points_status_enum CHECK (((status)::text = ANY ((ARRAY['IN_SERVICE'::character varying, 'SUSPENDED'::character varying, 'RETIRED'::character varying])::text[])))
);


--
-- Name: lighting_points_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.lighting_points_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: lighting_points_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.lighting_points_id_seq OWNED BY public.lighting_points.id;


--
-- Name: maintenance_regions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_regions (
    id bigint NOT NULL,
    region_code character varying(32) NOT NULL,
    name character varying(128) NOT NULL,
    parent_region_id bigint,
    level smallint DEFAULT 3 NOT NULL,
    boundary_geojson jsonb,
    description character varying(512),
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_maintenance_regions_level_range CHECK (((level >= 1) AND (level <= 3))),
    CONSTRAINT ck_maintenance_regions_no_self_parent CHECK (((parent_region_id IS NULL) OR (parent_region_id <> id)))
);


--
-- Name: maintenance_regions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.maintenance_regions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: maintenance_regions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.maintenance_regions_id_seq OWNED BY public.maintenance_regions.id;


--
-- Name: material_usages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.material_usages (
    id bigint NOT NULL,
    repair_process_id bigint NOT NULL,
    ticket_id bigint NOT NULL,
    material_code character varying(64) NOT NULL,
    material_name character varying(128) NOT NULL,
    quantity numeric(10,3) NOT NULL,
    unit character varying(16) NOT NULL,
    unit_price numeric(12,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_material_usages_quantity_positive CHECK ((quantity > (0)::numeric)),
    CONSTRAINT ck_material_usages_unit_enum CHECK (((unit)::text = ANY ((ARRAY['PCS'::character varying, 'M'::character varying, 'KG'::character varying, 'SET'::character varying])::text[]))),
    CONSTRAINT ck_material_usages_unit_price_non_negative CHECK (((unit_price IS NULL) OR (unit_price >= (0)::numeric)))
);


--
-- Name: material_usages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.material_usages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: material_usages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.material_usages_id_seq OWNED BY public.material_usages.id;


--
-- Name: notification_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_tasks (
    id bigint NOT NULL,
    recipient_account_id bigint NOT NULL,
    event_type character varying(48) NOT NULL,
    channel character varying(16) DEFAULT 'IN_APP'::character varying NOT NULL,
    title character varying(128) NOT NULL,
    body text NOT NULL,
    payload_json jsonb,
    related_ticket_id bigint,
    related_dispatch_id bigint,
    status character varying(16) DEFAULT 'PENDING'::character varying NOT NULL,
    attempt_count smallint DEFAULT 0 NOT NULL,
    max_attempts smallint DEFAULT 3 NOT NULL,
    next_retry_at timestamp with time zone,
    last_error character varying(512),
    sent_at timestamp with time zone,
    is_read boolean DEFAULT false NOT NULL,
    read_at timestamp with time zone,
    acknowledged_by_account_id bigint,
    acknowledged_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_notification_tasks_attempts_non_negative CHECK (((attempt_count >= 0) AND (max_attempts >= 1) AND (attempt_count <= max_attempts))),
    CONSTRAINT ck_notification_tasks_channel_enum CHECK (((channel)::text = ANY ((ARRAY['IN_APP'::character varying, 'SMS'::character varying, 'EMAIL'::character varying])::text[]))),
    CONSTRAINT ck_notification_tasks_dead_letter_error_required CHECK ((((status)::text <> 'DEAD_LETTER'::text) OR (last_error IS NOT NULL))),
    CONSTRAINT ck_notification_tasks_failed_next_retry CHECK ((((status)::text <> ALL ((ARRAY['FAILED'::character varying, 'PENDING'::character varying])::text[])) OR (next_retry_at IS NULL) OR (attempt_count < max_attempts))),
    CONSTRAINT ck_notification_tasks_read_consistent CHECK (((is_read = true) = (read_at IS NOT NULL))),
    CONSTRAINT ck_notification_tasks_sent_at_required CHECK ((((status)::text = 'SENT'::text) = (sent_at IS NOT NULL))),
    CONSTRAINT ck_notification_tasks_status_enum CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'SENT'::character varying, 'PROCESSING'::character varying, 'FAILED'::character varying, 'DEAD_LETTER'::character varying])::text[])))
);


--
-- Name: notification_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_tasks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_tasks_id_seq OWNED BY public.notification_tasks.id;


--
-- Name: repair_processes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.repair_processes (
    id bigint NOT NULL,
    dispatch_record_id bigint NOT NULL,
    ticket_id bigint NOT NULL,
    technician_account_id bigint NOT NULL,
    arrived_at timestamp with time zone NOT NULL,
    electrical_safety_checked boolean DEFAULT false NOT NULL,
    safety_checklist_json jsonb NOT NULL,
    safety_note character varying(512) NOT NULL,
    root_cause character varying(256),
    repair_summary text NOT NULL,
    repair_started_at timestamp with time zone,
    repair_finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_repair_processes_repair_summary_not_blank CHECK ((length(btrim(repair_summary)) > 0)),
    CONSTRAINT ck_repair_processes_safety_checklist_complete CHECK (((electrical_safety_checked = false) OR ((((safety_checklist_json ->> 'power_isolated'::text))::boolean IS TRUE) AND (((safety_checklist_json ->> 'voltage_verified_zero'::text))::boolean IS TRUE) AND (((safety_checklist_json ->> 'insulation_intact'::text))::boolean IS TRUE) AND (((safety_checklist_json ->> 'grounding_connected'::text))::boolean IS TRUE) AND (((safety_checklist_json ->> 'ppe_worn'::text))::boolean IS TRUE)))),
    CONSTRAINT ck_repair_processes_safety_note_not_blank CHECK ((length(btrim((safety_note)::text)) > 0)),
    CONSTRAINT ck_repair_processes_time_order CHECK (((repair_finished_at IS NULL) OR (repair_started_at IS NULL) OR (repair_finished_at >= repair_started_at)))
);


--
-- Name: repair_processes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.repair_processes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: repair_processes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.repair_processes_id_seq OWNED BY public.repair_processes.id;


--
-- Name: rework_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rework_orders (
    id bigint NOT NULL,
    rework_no character varying(32) NOT NULL,
    ticket_id bigint NOT NULL,
    source_acceptance_id bigint NOT NULL,
    round_no smallint NOT NULL,
    region_id bigint NOT NULL,
    status character varying(24) DEFAULT 'PENDING_DISPATCH'::character varying NOT NULL,
    reject_reason character varying(512) NOT NULL,
    requires_manual_review boolean DEFAULT false NOT NULL,
    manual_review_decided_by_account_id bigint,
    manual_review_decided_at timestamp with time zone,
    manual_review_decision character varying(32),
    manual_review_note character varying(512),
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rework_orders_manual_review_decision_enum CHECK (((manual_review_decision IS NULL) OR ((manual_review_decision)::text = ANY ((ARRAY['APPROVE_REASSIGN'::character varying, 'SPECIAL_CLOSE'::character varying, 'TERMINATE_CANCEL'::character varying])::text[])))),
    CONSTRAINT ck_rework_orders_manual_review_fields_consistent CHECK ((((manual_review_decision IS NULL) AND (manual_review_decided_by_account_id IS NULL) AND (manual_review_decided_at IS NULL)) OR ((manual_review_decision IS NOT NULL) AND (manual_review_decided_by_account_id IS NOT NULL) AND (manual_review_decided_at IS NOT NULL) AND ((status)::text = ANY ((ARRAY['CLOSED_BY_REVIEW'::character varying, 'CANCELLED'::character varying])::text[]))))),
    CONSTRAINT ck_rework_orders_manual_review_state_consistent CHECK ((((requires_manual_review = true) AND ((status)::text = ANY ((ARRAY['IN_MANUAL_REVIEW'::character varying, 'CLOSED_BY_REVIEW'::character varying, 'CANCELLED'::character varying])::text[]))) OR ((requires_manual_review = false) AND ((status)::text <> ALL ((ARRAY['IN_MANUAL_REVIEW'::character varying, 'CLOSED_BY_REVIEW'::character varying])::text[]))))),
    CONSTRAINT ck_rework_orders_round_positive CHECK ((round_no >= 1)),
    CONSTRAINT ck_rework_orders_status_enum CHECK (((status)::text = ANY ((ARRAY['PENDING_DISPATCH'::character varying, 'IN_MANUAL_REVIEW'::character varying, 'DISPATCHED'::character varying, 'IN_REPAIR'::character varying, 'PENDING_ACCEPTANCE'::character varying, 'PASSED'::character varying, 'CLOSED_BY_REVIEW'::character varying, 'CANCELLED'::character varying])::text[])))
);


--
-- Name: rework_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rework_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rework_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rework_orders_id_seq OWNED BY public.rework_orders.id;


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id smallint NOT NULL,
    code character varying(32) NOT NULL,
    name character varying(64) NOT NULL,
    description character varying(256),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_roles_code_enum CHECK (((code)::text = ANY ((ARRAY['ADMIN'::character varying, 'INSPECTOR'::character varying, 'TECHNICIAN'::character varying])::text[])))
);


--
-- Name: roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.roles_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.roles_id_seq OWNED BY public.roles.id;


--
-- Name: scope_authorizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scope_authorizations (
    id bigint NOT NULL,
    account_id bigint NOT NULL,
    region_id bigint NOT NULL,
    scope_type character varying(16) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_to timestamp with time zone,
    granted_by_account_id bigint,
    remark character varying(256),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_scope_authorizations_scope_type_enum CHECK (((scope_type)::text = ANY ((ARRAY['INSPECT'::character varying, 'REPAIR'::character varying, 'MANAGE'::character varying])::text[]))),
    CONSTRAINT ck_scope_authorizations_valid_window CHECK (((valid_to IS NULL) OR (valid_to > valid_from)))
);


--
-- Name: scope_authorizations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.scope_authorizations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: scope_authorizations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.scope_authorizations_id_seq OWNED BY public.scope_authorizations.id;


--
-- Name: acceptance_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records ALTER COLUMN id SET DEFAULT nextval('public.acceptance_records_id_seq'::regclass);


--
-- Name: account_roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles ALTER COLUMN id SET DEFAULT nextval('public.account_roles_id_seq'::regclass);


--
-- Name: accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);


--
-- Name: audit_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN id SET DEFAULT nextval('public.audit_logs_id_seq'::regclass);


--
-- Name: business_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_rules ALTER COLUMN id SET DEFAULT nextval('public.business_rules_id_seq'::regclass);


--
-- Name: dispatch_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records ALTER COLUMN id SET DEFAULT nextval('public.dispatch_records_id_seq'::regclass);


--
-- Name: facility_assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_assets ALTER COLUMN id SET DEFAULT nextval('public.facility_assets_id_seq'::regclass);


--
-- Name: fault_evidences id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_evidences ALTER COLUMN id SET DEFAULT nextval('public.fault_evidences_id_seq'::regclass);


--
-- Name: fault_tickets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets ALTER COLUMN id SET DEFAULT nextval('public.fault_tickets_id_seq'::regclass);


--
-- Name: lighting_points id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lighting_points ALTER COLUMN id SET DEFAULT nextval('public.lighting_points_id_seq'::regclass);


--
-- Name: maintenance_regions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_regions ALTER COLUMN id SET DEFAULT nextval('public.maintenance_regions_id_seq'::regclass);


--
-- Name: material_usages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.material_usages ALTER COLUMN id SET DEFAULT nextval('public.material_usages_id_seq'::regclass);


--
-- Name: notification_tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_tasks ALTER COLUMN id SET DEFAULT nextval('public.notification_tasks_id_seq'::regclass);


--
-- Name: repair_processes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repair_processes ALTER COLUMN id SET DEFAULT nextval('public.repair_processes_id_seq'::regclass);


--
-- Name: rework_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders ALTER COLUMN id SET DEFAULT nextval('public.rework_orders_id_seq'::regclass);


--
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- Name: scope_authorizations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scope_authorizations ALTER COLUMN id SET DEFAULT nextval('public.scope_authorizations_id_seq'::regclass);


--
-- Name: acceptance_records pk_acceptance_records; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records
    ADD CONSTRAINT pk_acceptance_records PRIMARY KEY (id);


--
-- Name: account_roles pk_account_roles; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles
    ADD CONSTRAINT pk_account_roles PRIMARY KEY (id);


--
-- Name: accounts pk_accounts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT pk_accounts PRIMARY KEY (id);


--
-- Name: audit_logs pk_audit_logs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT pk_audit_logs PRIMARY KEY (id);


--
-- Name: business_rules pk_business_rules; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_rules
    ADD CONSTRAINT pk_business_rules PRIMARY KEY (id);


--
-- Name: dispatch_records pk_dispatch_records; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT pk_dispatch_records PRIMARY KEY (id);


--
-- Name: facility_assets pk_facility_assets; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_assets
    ADD CONSTRAINT pk_facility_assets PRIMARY KEY (id);


--
-- Name: fault_evidences pk_fault_evidences; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_evidences
    ADD CONSTRAINT pk_fault_evidences PRIMARY KEY (id);


--
-- Name: fault_tickets pk_fault_tickets; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT pk_fault_tickets PRIMARY KEY (id);


--
-- Name: lighting_points pk_lighting_points; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lighting_points
    ADD CONSTRAINT pk_lighting_points PRIMARY KEY (id);


--
-- Name: maintenance_regions pk_maintenance_regions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_regions
    ADD CONSTRAINT pk_maintenance_regions PRIMARY KEY (id);


--
-- Name: material_usages pk_material_usages; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.material_usages
    ADD CONSTRAINT pk_material_usages PRIMARY KEY (id);


--
-- Name: notification_tasks pk_notification_tasks; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_tasks
    ADD CONSTRAINT pk_notification_tasks PRIMARY KEY (id);


--
-- Name: repair_processes pk_repair_processes; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repair_processes
    ADD CONSTRAINT pk_repair_processes PRIMARY KEY (id);


--
-- Name: rework_orders pk_rework_orders; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders
    ADD CONSTRAINT pk_rework_orders PRIMARY KEY (id);


--
-- Name: roles pk_roles; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT pk_roles PRIMARY KEY (id);


--
-- Name: scope_authorizations pk_scope_authorizations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scope_authorizations
    ADD CONSTRAINT pk_scope_authorizations PRIMARY KEY (id);


--
-- Name: acceptance_records uq_acceptance_records_repair_process_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records
    ADD CONSTRAINT uq_acceptance_records_repair_process_id UNIQUE (repair_process_id);


--
-- Name: account_roles uq_account_roles_account_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles
    ADD CONSTRAINT uq_account_roles_account_id UNIQUE (account_id);


--
-- Name: account_roles uq_account_roles_account_id_role_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles
    ADD CONSTRAINT uq_account_roles_account_id_role_id UNIQUE (account_id, role_id);


--
-- Name: accounts uq_accounts_employee_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT uq_accounts_employee_no UNIQUE (employee_no);


--
-- Name: accounts uq_accounts_username; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT uq_accounts_username UNIQUE (username);


--
-- Name: business_rules uq_business_rules_code_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_rules
    ADD CONSTRAINT uq_business_rules_code_version UNIQUE (rule_code, version);


--
-- Name: facility_assets uq_facility_assets_asset_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_assets
    ADD CONSTRAINT uq_facility_assets_asset_code UNIQUE (asset_code);


--
-- Name: fault_evidences uq_fault_evidences_sha256_per_ticket; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_evidences
    ADD CONSTRAINT uq_fault_evidences_sha256_per_ticket UNIQUE (ticket_id, sha256);


--
-- Name: fault_tickets uq_fault_tickets_ticket_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT uq_fault_tickets_ticket_no UNIQUE (ticket_no);


--
-- Name: lighting_points uq_lighting_points_point_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lighting_points
    ADD CONSTRAINT uq_lighting_points_point_code UNIQUE (point_code);


--
-- Name: maintenance_regions uq_maintenance_regions_region_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_regions
    ADD CONSTRAINT uq_maintenance_regions_region_code UNIQUE (region_code);


--
-- Name: material_usages uq_material_usages_repair_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.material_usages
    ADD CONSTRAINT uq_material_usages_repair_code UNIQUE (repair_process_id, material_code);


--
-- Name: repair_processes uq_repair_processes_dispatch_record_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repair_processes
    ADD CONSTRAINT uq_repair_processes_dispatch_record_id UNIQUE (dispatch_record_id);


--
-- Name: rework_orders uq_rework_orders_rework_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders
    ADD CONSTRAINT uq_rework_orders_rework_no UNIQUE (rework_no);


--
-- Name: rework_orders uq_rework_orders_ticket_round; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders
    ADD CONSTRAINT uq_rework_orders_ticket_round UNIQUE (ticket_id, round_no);


--
-- Name: roles uq_roles_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT uq_roles_code UNIQUE (code);


--
-- Name: ix_acceptance_records_inspector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_acceptance_records_inspector ON public.acceptance_records USING btree (inspector_account_id, accepted_at DESC);


--
-- Name: ix_acceptance_records_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_acceptance_records_ticket_id ON public.acceptance_records USING btree (ticket_id, accepted_at DESC);


--
-- Name: ix_account_roles_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_account_roles_role_id ON public.account_roles USING btree (role_id);


--
-- Name: ix_accounts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_accounts_status ON public.accounts USING btree (status) WHERE ((status)::text = 'ACTIVE'::text);


--
-- Name: ix_audit_logs_action_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_action_created ON public.audit_logs USING btree (action, created_at DESC);


--
-- Name: ix_audit_logs_actor_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_actor_created ON public.audit_logs USING btree (actor_account_id, created_at DESC);


--
-- Name: ix_audit_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_created_at ON public.audit_logs USING btree (created_at DESC);


--
-- Name: ix_audit_logs_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_request_id ON public.audit_logs USING btree (request_id);


--
-- Name: ix_audit_logs_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_resource ON public.audit_logs USING btree (resource_type, resource_id);


--
-- Name: ix_audit_logs_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_ticket_id ON public.audit_logs USING btree (ticket_id, created_at) WHERE (ticket_id IS NOT NULL);


--
-- Name: ix_dispatch_records_region_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_records_region_id ON public.dispatch_records USING btree (region_id, dispatched_at DESC);


--
-- Name: ix_dispatch_records_technician_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_records_technician_active ON public.dispatch_records USING btree (technician_account_id, status, accept_deadline_at) WHERE ((status)::text = ANY ((ARRAY['PENDING_ACCEPT'::character varying, 'ACCEPTED'::character varying])::text[]));


--
-- Name: ix_dispatch_records_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_records_ticket_id ON public.dispatch_records USING btree (ticket_id, dispatched_at DESC);


--
-- Name: ix_facility_assets_lighting_point_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_facility_assets_lighting_point_id ON public.facility_assets USING btree (lighting_point_id);


--
-- Name: ix_fault_evidences_ticket_id_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_evidences_ticket_id_type ON public.fault_evidences USING btree (ticket_id, evidence_type);


--
-- Name: ix_fault_tickets_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_created_at ON public.fault_tickets USING btree (created_at DESC);


--
-- Name: ix_fault_tickets_lighting_point_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_lighting_point_id ON public.fault_tickets USING btree (lighting_point_id);


--
-- Name: ix_fault_tickets_manual_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_manual_review ON public.fault_tickets USING btree (region_id, updated_at) WHERE ((status)::text = 'MANUAL_REVIEW'::text);


--
-- Name: ix_fault_tickets_pending_candidates; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_pending_candidates ON public.fault_tickets USING btree (region_id, ai_max_confidence DESC) WHERE ((status)::text = 'AI_CANDIDATE_PENDING'::text);


--
-- Name: ix_fault_tickets_pending_dispatch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_pending_dispatch ON public.fault_tickets USING btree (region_id, priority, created_at) WHERE ((status)::text = ANY ((ARRAY['PENDING_DISPATCH'::character varying, 'REWORK_PENDING_DISPATCH'::character varying])::text[]));


--
-- Name: ix_fault_tickets_reporter_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_reporter_account_id ON public.fault_tickets USING btree (reporter_account_id);


--
-- Name: ix_fault_tickets_sla_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fault_tickets_sla_scan ON public.fault_tickets USING btree (is_sla_overdue, response_deadline_at, repair_deadline_at) WHERE ((status)::text <> ALL ((ARRAY['CLOSED'::character varying, 'CANCELLED'::character varying, 'AI_CANDIDATE_PENDING'::character varying])::text[]));


--
-- Name: ix_lighting_points_region_id_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lighting_points_region_id_status ON public.lighting_points USING btree (region_id, status);


--
-- Name: ix_lighting_points_road_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lighting_points_road_name ON public.lighting_points USING btree (road_name);


--
-- Name: ix_maintenance_regions_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_maintenance_regions_is_active ON public.maintenance_regions USING btree (is_active) WHERE (is_active = true);


--
-- Name: ix_maintenance_regions_parent_region_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_maintenance_regions_parent_region_id ON public.maintenance_regions USING btree (parent_region_id);


--
-- Name: ix_material_usages_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_material_usages_ticket_id ON public.material_usages USING btree (ticket_id);


--
-- Name: ix_notification_tasks_dead_letter; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_tasks_dead_letter ON public.notification_tasks USING btree (created_at DESC) WHERE ((status)::text = 'DEAD_LETTER'::text);


--
-- Name: ix_notification_tasks_pending_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_tasks_pending_due ON public.notification_tasks USING btree (next_retry_at, created_at) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'FAILED'::character varying])::text[]));


--
-- Name: ix_notification_tasks_recipient_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_tasks_recipient_unread ON public.notification_tasks USING btree (recipient_account_id, created_at DESC) WHERE (is_read = false);


--
-- Name: ix_notification_tasks_related_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_tasks_related_ticket_id ON public.notification_tasks USING btree (related_ticket_id);


--
-- Name: ix_repair_processes_technician; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repair_processes_technician ON public.repair_processes USING btree (technician_account_id, created_at DESC);


--
-- Name: ix_repair_processes_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_repair_processes_ticket_id ON public.repair_processes USING btree (ticket_id);


--
-- Name: ix_rework_orders_region_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rework_orders_region_id ON public.rework_orders USING btree (region_id, created_at DESC);


--
-- Name: ix_rework_orders_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rework_orders_status_created ON public.rework_orders USING btree (status, created_at);


--
-- Name: ix_scope_authorizations_account_id_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scope_authorizations_account_id_active ON public.scope_authorizations USING btree (account_id) WHERE (is_active = true);


--
-- Name: ix_scope_authorizations_region_type_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scope_authorizations_region_type_active ON public.scope_authorizations USING btree (region_id, scope_type, is_active);


--
-- Name: uq_audit_logs_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_audit_logs_idempotency ON public.audit_logs USING btree (actor_account_id, method, path, idempotency_key) WHERE ((idempotency_key IS NOT NULL) AND (response_status >= 200) AND (response_status < 300));


--
-- Name: uq_business_rules_code_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_business_rules_code_active ON public.business_rules USING btree (rule_code) WHERE (is_active = true);


--
-- Name: uq_dispatch_records_active_ticket; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_dispatch_records_active_ticket ON public.dispatch_records USING btree (ticket_id) WHERE ((status)::text = ANY ((ARRAY['PENDING_ACCEPT'::character varying, 'ACCEPTED'::character varying])::text[]));


--
-- Name: uq_fault_tickets_active_point_type; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fault_tickets_active_point_type ON public.fault_tickets USING btree (lighting_point_id, fault_type) WHERE ((status)::text <> ALL ((ARRAY['CLOSED'::character varying, 'CANCELLED'::character varying])::text[]));


--
-- Name: uq_rework_orders_active_ticket; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rework_orders_active_ticket ON public.rework_orders USING btree (ticket_id) WHERE ((status)::text <> ALL ((ARRAY['PASSED'::character varying, 'CLOSED_BY_REVIEW'::character varying, 'CANCELLED'::character varying])::text[]));


--
-- Name: uq_scope_authorizations_account_region_type; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_scope_authorizations_account_region_type ON public.scope_authorizations USING btree (account_id, region_id, scope_type) WHERE (is_active = true);


--
-- Name: acceptance_records fk_acceptance_records_inspector_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records
    ADD CONSTRAINT fk_acceptance_records_inspector_account_id_accounts FOREIGN KEY (inspector_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: acceptance_records fk_acceptance_records_repair_process_id_repair_processes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records
    ADD CONSTRAINT fk_acceptance_records_repair_process_id_repair_processes FOREIGN KEY (repair_process_id) REFERENCES public.repair_processes(id) ON DELETE RESTRICT;


--
-- Name: acceptance_records fk_acceptance_records_rework_order_id_rework_orders; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records
    ADD CONSTRAINT fk_acceptance_records_rework_order_id_rework_orders FOREIGN KEY (rework_order_id) REFERENCES public.rework_orders(id) ON DELETE RESTRICT;


--
-- Name: acceptance_records fk_acceptance_records_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_records
    ADD CONSTRAINT fk_acceptance_records_ticket_id_fault_tickets FOREIGN KEY (ticket_id) REFERENCES public.fault_tickets(id) ON DELETE RESTRICT;


--
-- Name: account_roles fk_account_roles_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles
    ADD CONSTRAINT fk_account_roles_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: account_roles fk_account_roles_granted_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles
    ADD CONSTRAINT fk_account_roles_granted_by_account_id_accounts FOREIGN KEY (granted_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: account_roles fk_account_roles_role_id_roles; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_roles
    ADD CONSTRAINT fk_account_roles_role_id_roles FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE RESTRICT;


--
-- Name: accounts fk_accounts_manager_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT fk_accounts_manager_account_id_accounts FOREIGN KEY (manager_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: audit_logs fk_audit_logs_actor_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT fk_audit_logs_actor_account_id_accounts FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: business_rules fk_business_rules_created_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_rules
    ADD CONSTRAINT fk_business_rules_created_by_account_id_accounts FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: dispatch_records fk_dispatch_records_dispatched_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT fk_dispatch_records_dispatched_by_account_id_accounts FOREIGN KEY (dispatched_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: dispatch_records fk_dispatch_records_rework_order_id_rework_orders; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT fk_dispatch_records_rework_order_id_rework_orders FOREIGN KEY (rework_order_id) REFERENCES public.rework_orders(id);


--
-- Name: dispatch_records fk_dispatch_records_scope_authorization_id_scope_authorizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT fk_dispatch_records_scope_authorization_id_scope_authorizations FOREIGN KEY (scope_authorization_id) REFERENCES public.scope_authorizations(id) ON DELETE RESTRICT;


--
-- Name: dispatch_records fk_dispatch_records_technician_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT fk_dispatch_records_technician_account_id_accounts FOREIGN KEY (technician_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: dispatch_records fk_dispatch_records_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT fk_dispatch_records_ticket_id_fault_tickets FOREIGN KEY (ticket_id) REFERENCES public.fault_tickets(id) ON DELETE RESTRICT;


--
-- Name: facility_assets fk_facility_assets_lighting_point_id_lighting_points; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_assets
    ADD CONSTRAINT fk_facility_assets_lighting_point_id_lighting_points FOREIGN KEY (lighting_point_id) REFERENCES public.lighting_points(id) ON DELETE RESTRICT;


--
-- Name: fault_evidences fk_fault_evidences_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_evidences
    ADD CONSTRAINT fk_fault_evidences_ticket_id_fault_tickets FOREIGN KEY (ticket_id) REFERENCES public.fault_tickets(id) ON DELETE CASCADE;


--
-- Name: fault_evidences fk_fault_evidences_uploaded_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_evidences
    ADD CONSTRAINT fk_fault_evidences_uploaded_by_account_id_accounts FOREIGN KEY (uploaded_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: fault_tickets fk_fault_tickets_cancelled_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT fk_fault_tickets_cancelled_by_account_id_accounts FOREIGN KEY (cancelled_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: fault_tickets fk_fault_tickets_confirmed_by_inspector_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT fk_fault_tickets_confirmed_by_inspector_id_accounts FOREIGN KEY (confirmed_by_inspector_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: fault_tickets fk_fault_tickets_facility_asset_id_facility_assets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT fk_fault_tickets_facility_asset_id_facility_assets FOREIGN KEY (facility_asset_id) REFERENCES public.facility_assets(id) ON DELETE SET NULL;


--
-- Name: fault_tickets fk_fault_tickets_lighting_point_id_lighting_points; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT fk_fault_tickets_lighting_point_id_lighting_points FOREIGN KEY (lighting_point_id) REFERENCES public.lighting_points(id) ON DELETE RESTRICT;


--
-- Name: fault_tickets fk_fault_tickets_region_id_maintenance_regions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT fk_fault_tickets_region_id_maintenance_regions FOREIGN KEY (region_id) REFERENCES public.maintenance_regions(id) ON DELETE RESTRICT;


--
-- Name: fault_tickets fk_fault_tickets_reporter_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fault_tickets
    ADD CONSTRAINT fk_fault_tickets_reporter_account_id_accounts FOREIGN KEY (reporter_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: lighting_points fk_lighting_points_region_id_maintenance_regions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lighting_points
    ADD CONSTRAINT fk_lighting_points_region_id_maintenance_regions FOREIGN KEY (region_id) REFERENCES public.maintenance_regions(id) ON DELETE RESTRICT;


--
-- Name: maintenance_regions fk_maintenance_regions_parent_region_id_maintenance_regions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_regions
    ADD CONSTRAINT fk_maintenance_regions_parent_region_id_maintenance_regions FOREIGN KEY (parent_region_id) REFERENCES public.maintenance_regions(id) ON DELETE RESTRICT;


--
-- Name: material_usages fk_material_usages_repair_process_id_repair_processes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.material_usages
    ADD CONSTRAINT fk_material_usages_repair_process_id_repair_processes FOREIGN KEY (repair_process_id) REFERENCES public.repair_processes(id) ON DELETE CASCADE;


--
-- Name: material_usages fk_material_usages_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.material_usages
    ADD CONSTRAINT fk_material_usages_ticket_id_fault_tickets FOREIGN KEY (ticket_id) REFERENCES public.fault_tickets(id) ON DELETE RESTRICT;


--
-- Name: notification_tasks fk_notification_tasks_acknowledged_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_tasks
    ADD CONSTRAINT fk_notification_tasks_acknowledged_by_account_id_accounts FOREIGN KEY (acknowledged_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: notification_tasks fk_notification_tasks_recipient_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_tasks
    ADD CONSTRAINT fk_notification_tasks_recipient_account_id_accounts FOREIGN KEY (recipient_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: notification_tasks fk_notification_tasks_related_dispatch_id_dispatch_records; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_tasks
    ADD CONSTRAINT fk_notification_tasks_related_dispatch_id_dispatch_records FOREIGN KEY (related_dispatch_id) REFERENCES public.dispatch_records(id) ON DELETE CASCADE;


--
-- Name: notification_tasks fk_notification_tasks_related_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_tasks
    ADD CONSTRAINT fk_notification_tasks_related_ticket_id_fault_tickets FOREIGN KEY (related_ticket_id) REFERENCES public.fault_tickets(id) ON DELETE CASCADE;


--
-- Name: repair_processes fk_repair_processes_dispatch_record_id_dispatch_records; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repair_processes
    ADD CONSTRAINT fk_repair_processes_dispatch_record_id_dispatch_records FOREIGN KEY (dispatch_record_id) REFERENCES public.dispatch_records(id) ON DELETE RESTRICT;


--
-- Name: repair_processes fk_repair_processes_technician_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repair_processes
    ADD CONSTRAINT fk_repair_processes_technician_account_id_accounts FOREIGN KEY (technician_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: repair_processes fk_repair_processes_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repair_processes
    ADD CONSTRAINT fk_repair_processes_ticket_id_fault_tickets FOREIGN KEY (ticket_id) REFERENCES public.fault_tickets(id) ON DELETE RESTRICT;


--
-- Name: rework_orders fk_rework_orders_manual_review_decided_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders
    ADD CONSTRAINT fk_rework_orders_manual_review_decided_by_account_id_accounts FOREIGN KEY (manual_review_decided_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: rework_orders fk_rework_orders_source_acceptance_id_acceptance_records; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders
    ADD CONSTRAINT fk_rework_orders_source_acceptance_id_acceptance_records FOREIGN KEY (source_acceptance_id) REFERENCES public.acceptance_records(id) ON DELETE RESTRICT;


--
-- Name: rework_orders fk_rework_orders_ticket_id_fault_tickets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rework_orders
    ADD CONSTRAINT fk_rework_orders_ticket_id_fault_tickets FOREIGN KEY (ticket_id) REFERENCES public.fault_tickets(id) ON DELETE RESTRICT;


--
-- Name: scope_authorizations fk_scope_authorizations_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scope_authorizations
    ADD CONSTRAINT fk_scope_authorizations_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: scope_authorizations fk_scope_authorizations_granted_by_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scope_authorizations
    ADD CONSTRAINT fk_scope_authorizations_granted_by_account_id_accounts FOREIGN KEY (granted_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: scope_authorizations fk_scope_authorizations_region_id_maintenance_regions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scope_authorizations
    ADD CONSTRAINT fk_scope_authorizations_region_id_maintenance_regions FOREIGN KEY (region_id) REFERENCES public.maintenance_regions(id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--

\unrestrict YJH5lFwGQvBWo4etgUi6vqd4G5iXQScquKVUMHhKxnLl9WQAJUzJD4zeRShsP3y

