-- Образец схемы рабочей базы (crm.dobrov.pro) на версии f306149, после migrations/052. Только структура, без данных.
-- Сверено с сервером 26.11.2026 по отпечаткам (D-179): столбцы, внешние ключи и индексы совпадают побайтно:
--   2087|a0efdd0945b1f36094d3e01677ad5e19|0b09b7311b9073fc26cb3bd7d1ec5565|75d8ebd1debc8eee8f6056659eb820c9
-- Используется только тестом tests/test_prod_schema_baseline.py: образец + SQL-файлы новее 052 должны совпасть с кодом.
-- Лежит вне backend/migrations, поэтому update.sh его не применяет. Не редактировать вручную.
-- baseline-last-migration: 052
--
-- PostgreSQL database dump
--

\restrict RVQ8umZi40eY4z850Azhbzt9bVWO9T0mw1r4Od3zLQPuMbp2JbU9uR7XoUWQUPt

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: deal_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.deal_status AS ENUM (
    'DRAFT',
    'LOGISTICS_CALC',
    'SERVICE_CALC',
    'CALC_READY',
    'CD_APPROVAL',
    'FD_VISA',
    'GD_VISA',
    'REWORK',
    'APPROVED',
    'KP_SENT',
    'WON',
    'LOST',
    'CANCELLED',
    'CONTRACT',
    'EXECUTION',
    'CLOSED'
);


--
-- Name: audit_log_immutable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.audit_log_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END; $$;


--
-- Name: deal_versions_immutable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.deal_versions_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF (OLD.calc_input IS DISTINCT FROM NEW.calc_input) OR (OLD.calc_result IS DISTINCT FROM NEW.calc_result) THEN
    RAISE EXCEPTION 'deal_versions: calc_input/calc_result are immutable';
  END IF;
  RETURN NEW;
END; $$;


--
-- Name: security_events_immutable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.security_events_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'security_events is append-only';
END; $$;


--
-- Name: support_messages_immutable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.support_messages_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'support_messages: сообщения обращений не изменяются и не удаляются';
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: acceptance_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.acceptance_units (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    position_id character varying(36) NOT NULL,
    unit_no integer NOT NULL,
    serial character varying(128) NOT NULL,
    engine_no character varying(128) NOT NULL,
    frame_no character varying(128) NOT NULL,
    components jsonb NOT NULL,
    checklist jsonb NOT NULL,
    tests jsonb NOT NULL,
    files jsonb NOT NULL,
    remarks jsonb NOT NULL,
    inspector character varying(255) NOT NULL,
    factory_rep character varying(255) NOT NULL,
    place character varying(255) NOT NULL,
    inspected_on date,
    decision character varying(24) NOT NULL,
    decided_at timestamp with time zone,
    decided_by character varying(36),
    act jsonb NOT NULL,
    comment text NOT NULL,
    machine_id character varying(36),
    mode character varying(16) NOT NULL,
    is_final boolean NOT NULL,
    round_no integer NOT NULL,
    rounds jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: access_review_campaigns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_review_campaigns (
    id character varying(36) NOT NULL,
    title character varying(200) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    due_date date NOT NULL,
    status character varying(16) NOT NULL,
    scope jsonb NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    completed_by character varying(36),
    completion_note text NOT NULL,
    CONSTRAINT ck_access_review_campaign_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'open'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: access_review_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_review_items (
    id character varying(36) NOT NULL,
    campaign_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    snapshot jsonb NOT NULL,
    decision character varying(16) NOT NULL,
    reviewer_id character varying(36),
    reviewed_at timestamp with time zone,
    comment text NOT NULL,
    changes jsonb NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT ck_access_review_item_decision CHECK (((decision)::text = ANY ((ARRAY['pending'::character varying, 'keep'::character varying, 'change'::character varying, 'disable'::character varying, 'skipped'::character varying])::text[])))
);


--
-- Name: access_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_rules (
    id character varying(36) NOT NULL,
    role character varying(32) NOT NULL,
    field_group character varying(64) NOT NULL,
    level integer NOT NULL,
    changed_by character varying(36),
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: access_user_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_user_overrides (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    field_group character varying(64) NOT NULL,
    level integer NOT NULL,
    valid_to timestamp with time zone,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_conversations (
    id character varying(36) NOT NULL,
    owner_id character varying(36) NOT NULL,
    title character varying(120) NOT NULL,
    default_mode character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_message_at timestamp with time zone,
    deleted_at timestamp with time zone
);


--
-- Name: ai_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_feedback (
    id character varying(36) NOT NULL,
    message_id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    rating character varying(16) NOT NULL,
    comment text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ai_feedback_rating CHECK (((rating)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying])::text[])))
);


--
-- Name: ai_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_messages (
    id character varying(36) NOT NULL,
    conversation_id character varying(36) NOT NULL,
    role character varying(16) NOT NULL,
    mode character varying(16) NOT NULL,
    content text NOT NULL,
    status character varying(16) NOT NULL,
    provider character varying(32) NOT NULL,
    model character varying(64) NOT NULL,
    sources jsonb NOT NULL,
    warnings jsonb NOT NULL,
    facts jsonb NOT NULL,
    request_id character varying(40) NOT NULL,
    client_request_id character varying(64),
    reply_to_id character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    purged_at timestamp with time zone,
    CONSTRAINT ck_ai_message_role CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying, 'system_notice'::character varying])::text[]))),
    CONSTRAINT ck_ai_message_status CHECK (((status)::text = ANY ((ARRAY['complete'::character varying, 'fallback'::character varying, 'error'::character varying])::text[])))
);


--
-- Name: ai_tool_audits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_tool_audits (
    id character varying(36) NOT NULL,
    conversation_id character varying(36),
    message_id character varying(36),
    actor_user_id character varying(36) NOT NULL,
    tool_name character varying(48) NOT NULL,
    arguments_sanitized jsonb NOT NULL,
    result_status character varying(16) NOT NULL,
    result_count integer NOT NULL,
    entity_ids jsonb NOT NULL,
    duration_ms integer NOT NULL,
    error_code character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_usage_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_usage_daily (
    user_id character varying(36) NOT NULL,
    usage_date date NOT NULL,
    request_count integer NOT NULL,
    token_in integer NOT NULL,
    token_out integer NOT NULL,
    minute_started_at timestamp with time zone,
    minute_count integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_errors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_errors (
    id character varying(36) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    source character varying(16) NOT NULL,
    kind character varying(120) NOT NULL,
    message text NOT NULL,
    "where" character varying(300) NOT NULL,
    trace text NOT NULL,
    count integer NOT NULL,
    users jsonb NOT NULL,
    last_request_id character varying(64) NOT NULL,
    release character varying(40) NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by character varying(36)
);


--
-- Name: app_markers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_markers (
    key character varying(64) NOT NULL,
    value character varying(64) NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    app_env character varying(32) NOT NULL
);


--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    version_id character varying(36),
    step character varying(32) NOT NULL,
    role character varying(16) NOT NULL,
    user_id character varying(36) NOT NULL,
    approved boolean NOT NULL,
    comment text NOT NULL,
    prices_snapshot jsonb NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    reset_at timestamp with time zone
);


--
-- Name: assignment_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignment_events (
    id character varying(36) NOT NULL,
    assignment_id character varying(36) NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    by_id character varying(36),
    action character varying(24) NOT NULL,
    from_status character varying(16) NOT NULL,
    to_status character varying(16) NOT NULL,
    comment text NOT NULL,
    data jsonb NOT NULL
);


--
-- Name: assignment_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignment_files (
    id character varying(36) NOT NULL,
    assignment_id character varying(36) NOT NULL,
    file_name character varying(255) NOT NULL,
    storage_path character varying(1024) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size integer NOT NULL,
    scan_status character varying(24) NOT NULL,
    scan_id character varying(36),
    uploaded_by character varying(36),
    at timestamp with time zone DEFAULT now() NOT NULL,
    removed_at timestamp with time zone,
    removed_by character varying(36)
);


--
-- Name: assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignments (
    id character varying(36) NOT NULL,
    number character varying(24) NOT NULL,
    seq integer NOT NULL,
    year integer NOT NULL,
    kind character varying(16) NOT NULL,
    title character varying(255) NOT NULL,
    body text NOT NULL,
    expected_result text NOT NULL,
    priority character varying(16) NOT NULL,
    mode character varying(8) NOT NULL,
    direction_key character varying(32) NOT NULL,
    author_id character varying(36) NOT NULL,
    assignee_id character varying(36) NOT NULL,
    executor_id character varying(36),
    head_id character varying(36),
    status character varying(16) NOT NULL,
    due_on date NOT NULL,
    first_due_on date NOT NULL,
    source_url character varying(300) NOT NULL,
    source_key character varying(160) NOT NULL,
    source_title character varying(255) NOT NULL,
    report text NOT NULL,
    block_reason text NOT NULL,
    accepted_at timestamp with time zone,
    submitted_at timestamp with time zone,
    done_at timestamp with time zone,
    done_by character varying(36),
    cancelled_at timestamp with time zone,
    alerts jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: attachment_scans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachment_scans (
    id character varying(36) NOT NULL,
    attachment_id character varying(36),
    quarantine_key character varying(64) NOT NULL,
    source character varying(16) NOT NULL,
    route character varying(160) NOT NULL,
    storage_path character varying(1024) NOT NULL,
    file_name character varying(255) NOT NULL,
    status character varying(20) NOT NULL,
    engine character varying(16) NOT NULL,
    engine_version character varying(32) NOT NULL,
    signature_version character varying(32) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size bigint NOT NULL,
    detected_name character varying(120) NOT NULL,
    attempts integer NOT NULL,
    last_error_code character varying(32) NOT NULL,
    uploaded_by character varying(36),
    scanned_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_attachment_scan_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'clean'::character varying, 'infected'::character varying, 'error'::character varying, 'legacy_unscanned'::character varying])::text[])))
);


--
-- Name: attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachments (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    folder character varying(32) NOT NULL,
    entity character varying(32) NOT NULL,
    entity_id character varying(36),
    category character varying(32) NOT NULL,
    comment text NOT NULL,
    file_name character varying(255) NOT NULL,
    storage_path character varying(1024) NOT NULL,
    storage_url character varying(1024) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size integer NOT NULL,
    uploaded_by character varying(36),
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    scan_status character varying(20) DEFAULT 'legacy_unscanned'::character varying NOT NULL,
    scanned_at timestamp with time zone
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    user_id character varying(36),
    deal_id character varying(36),
    entity character varying(64) NOT NULL,
    entity_id character varying(128) NOT NULL,
    action character varying(32) NOT NULL,
    before jsonb,
    after jsonb,
    comment text NOT NULL
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: auth_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_sessions (
    id character varying(36) NOT NULL,
    sid character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone,
    idle_expires_at timestamp with time zone NOT NULL,
    absolute_expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revoked_by character varying(36),
    revoke_reason character varying(16) NOT NULL,
    remember boolean NOT NULL,
    auth_method character varying(16) NOT NULL,
    mfa_level character varying(16) NOT NULL,
    last_mfa_at timestamp with time zone,
    last_step_up_at timestamp with time zone,
    ip_prefix character varying(64) NOT NULL,
    user_agent_short character varying(120) NOT NULL
);


--
-- Name: backup_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backup_runs (
    id character varying(36) NOT NULL,
    run_key character varying(36) NOT NULL,
    reason character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    snapshot_id character varying(16),
    git_commit character varying(40),
    schema_revision character varying(64),
    storage_provider character varying(16) NOT NULL,
    database_bytes bigint,
    files_bytes bigint,
    files_count integer,
    manifest_sha256 character varying(64),
    check_status character varying(16) NOT NULL,
    checked_at timestamp with time zone,
    retention_status character varying(16) NOT NULL,
    error_code character varying(32) NOT NULL,
    error_summary character varying(300) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_backup_runs_reason CHECK (((reason)::text = ANY ((ARRAY['daily'::character varying, 'pre_deploy'::character varying, 'manual'::character varying, 'restore_check'::character varying])::text[]))),
    CONSTRAINT ck_backup_runs_status CHECK (((status)::text = ANY ((ARRAY['running'::character varying, 'success'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: bank_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bank_profiles (
    id character varying(36) NOT NULL,
    profile_id character varying(36) NOT NULL,
    title character varying(255) NOT NULL,
    currency character varying(3) NOT NULL,
    country character varying(64) NOT NULL,
    beneficiary character varying(255) NOT NULL,
    bank_name character varying(255) NOT NULL,
    bank_address text NOT NULL,
    account character varying(64) NOT NULL,
    bik character varying(16) NOT NULL,
    swift character varying(16) NOT NULL,
    corr_account character varying(64) NOT NULL,
    corr_bank character varying(255) NOT NULL,
    purpose character varying(255) NOT NULL,
    status character varying(16) NOT NULL,
    valid_from date,
    valid_to date,
    verified boolean NOT NULL,
    verified_at timestamp with time zone,
    verified_by character varying(36),
    history jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batches (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    name character varying(64) NOT NULL,
    route character varying(255) NOT NULL,
    transport_units integer NOT NULL,
    weight_kg double precision,
    volume_m3 double precision,
    ready_from date,
    ready_to date,
    transit_days integer NOT NULL,
    customs_days integer NOT NULL,
    rf_delivery_days integer NOT NULL,
    border_planned date,
    border_actual date,
    actual_events jsonb NOT NULL,
    allocation character varying(8) NOT NULL,
    items jsonb NOT NULL,
    payments jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: briefing_item_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.briefing_item_states (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    item_key character varying(120) NOT NULL,
    source_signature character varying(64) NOT NULL,
    pinned_at timestamp with time zone,
    snoozed_until timestamp with time zone,
    hidden_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: client_machines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client_machines (
    id character varying(36) NOT NULL,
    client_id character varying(36) NOT NULL,
    deal_id character varying(36),
    position_id character varying(36),
    unit_no integer NOT NULL,
    equipment_model_id character varying(36),
    other_model_id character varying(36),
    manufacturer character varying(255) NOT NULL,
    model character varying(255) NOT NULL,
    serial character varying(128) NOT NULL,
    commissioned_on date,
    site character varying(255) NOT NULL,
    hours_per_day double precision,
    active boolean NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: client_price_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client_price_history (
    id character varying(36) NOT NULL,
    client_id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    deal_number character varying(32) NOT NULL,
    version_id character varying(36),
    version_no integer NOT NULL,
    kp_no character varying(64) NOT NULL,
    variant_id character varying(36),
    variant_name character varying(255) NOT NULL,
    factory_id character varying(36),
    factory_name character varying(255) NOT NULL,
    model character varying(255) NOT NULL,
    execution_id character varying(36),
    qty integer NOT NULL,
    unit_price_rub numeric(20,6),
    total_rub numeric(20,6),
    currency character varying(3) NOT NULL,
    fx_rate numeric(20,6),
    payment_terms jsonb NOT NULL,
    delivery_term character varying(255) NOT NULL,
    kp_brand character varying(16) NOT NULL,
    kp_path character varying(512) NOT NULL,
    manager_id character varying(36),
    "on" date NOT NULL,
    outcome character varying(16),
    chosen boolean
);


--
-- Name: clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clients (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    inn character varying(12) NOT NULL,
    address text NOT NULL,
    sites jsonb NOT NULL,
    contacts jsonb NOT NULL,
    full_name text NOT NULL,
    kpp character varying(9) NOT NULL,
    ogrn character varying(15) NOT NULL,
    director character varying(255) NOT NULL,
    legal_status character varying(32) NOT NULL,
    okved character varying(255) NOT NULL,
    registry_data jsonb NOT NULL,
    registry_checked_at timestamp with time zone,
    manager_id character varying(36),
    default_payment_scheme_id character varying(36),
    requisites jsonb NOT NULL,
    is_active boolean NOT NULL,
    delivery_addresses jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: competitor_offers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competitor_offers (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    variant_id character varying(36),
    competitor_id character varying(36),
    "on" date NOT NULL,
    equipment character varying(500) NOT NULL,
    qty double precision NOT NULL,
    amount numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    vat_included boolean NOT NULL,
    vat_pct double precision NOT NULL,
    fx_rate numeric(20,6) NOT NULL,
    fx_on date,
    source character varying(255) NOT NULL,
    delivery_term character varying(255) NOT NULL,
    payment_terms character varying(500) NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    deleted_at timestamp with time zone,
    deleted_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: competitors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competitors (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    name_key character varying(255) NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contract_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contract_claims (
    id character varying(36) NOT NULL,
    contract_id character varying(36) NOT NULL,
    deal_id character varying(36),
    direction character varying(8) NOT NULL,
    number character varying(64) NOT NULL,
    seq integer,
    status character varying(16) NOT NULL,
    lines jsonb NOT NULL,
    currency character varying(3) NOT NULL,
    demand text NOT NULL,
    answer_days integer NOT NULL,
    issued_on date,
    signer_id character varying(36),
    signer_role character varying(8) NOT NULL,
    signed_at timestamp with time zone,
    sent_on date,
    sent_via character varying(16) NOT NULL,
    track character varying(128) NOT NULL,
    answer_due date,
    result character varying(16) NOT NULL,
    result_amount double precision,
    answer_on date,
    answer_text text NOT NULL,
    files jsonb NOT NULL,
    history jsonb NOT NULL,
    notified jsonb NOT NULL,
    source character varying(8) NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracts (
    id character varying(36) NOT NULL,
    parent_id character varying(36),
    doc_type character varying(16) NOT NULL,
    kind character varying(32) NOT NULL,
    direction character varying(8) NOT NULL,
    number character varying(64) NOT NULL,
    seq integer,
    cp_number character varying(128) NOT NULL,
    title character varying(255) NOT NULL,
    subject text NOT NULL,
    status character varying(16) NOT NULL,
    own_entity_id character varying(36),
    own_profile_id character varying(36),
    own_bank_id character varying(36),
    signatory jsonb NOT NULL,
    cp_kind character varying(16) NOT NULL,
    cp_id character varying(36),
    cp_name character varying(255) NOT NULL,
    cp_inn character varying(32) NOT NULL,
    cp_profile_id character varying(36),
    deal_id character varying(36),
    deal_ids jsonb NOT NULL,
    package_id character varying(36),
    amount double precision,
    currency character varying(3) NOT NULL,
    vat_mode character varying(16) NOT NULL,
    vat_rate double precision,
    price_type character varying(16) NOT NULL,
    fx_clause text NOT NULL,
    signed_on date,
    effective_from date,
    valid_to date,
    auto_renew boolean NOT NULL,
    payment_terms jsonb NOT NULL,
    performance_terms jsonb NOT NULL,
    penalties jsonb NOT NULL,
    liability_cap_pct double precision,
    security jsonb NOT NULL,
    warranty jsonb NOT NULL,
    law jsonb NOT NULL,
    sign_method character varying(8) NOT NULL,
    edo jsonb NOT NULL,
    original jsonb NOT NULL,
    files jsonb NOT NULL,
    history jsonb NOT NULL,
    notes text NOT NULL,
    curator_id character varying(36),
    lawyer_id character varying(36),
    created_by character varying(36),
    source character varying(16) NOT NULL,
    mirror_kind character varying(16) NOT NULL,
    is_framework boolean NOT NULL,
    client_order boolean NOT NULL,
    approval jsonb NOT NULL,
    notified jsonb NOT NULL,
    facts jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: deal_feed; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_feed (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    kind character varying(16) NOT NULL,
    direction character varying(8) NOT NULL,
    counterparty character varying(255) NOT NULL,
    subject character varying(512) NOT NULL,
    body text NOT NULL,
    attachment_ids jsonb NOT NULL,
    external_id character varying(255),
    at timestamp with time zone DEFAULT now() NOT NULL,
    user_id character varying(36),
    section character varying(16) DEFAULT ''::character varying NOT NULL,
    parent_id character varying(36),
    tags jsonb NOT NULL,
    mentions jsonb NOT NULL,
    awaiting boolean DEFAULT false NOT NULL,
    due_on date,
    resolved_at timestamp with time zone,
    resolved_by character varying(36),
    pinned boolean DEFAULT false NOT NULL,
    pinned_by character varying(36),
    edited_at timestamp with time zone,
    edit_history jsonb NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: deal_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_versions (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    version_no integer NOT NULL,
    reason character varying(32) NOT NULL,
    calc_input jsonb NOT NULL,
    calc_result jsonb NOT NULL,
    excel_path character varying(512) NOT NULL,
    excel_manager_path character varying(512) NOT NULL,
    kp_path character varying(512) NOT NULL,
    kp_meta jsonb NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: deals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deals (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    client_id character varying(36) NOT NULL,
    contact character varying(255) NOT NULL,
    site character varying(255) NOT NULL,
    region character varying(255) NOT NULL,
    requested_at date NOT NULL,
    kp_due date,
    report_date date,
    priority character varying(16) NOT NULL,
    status public.deal_status NOT NULL,
    manager_id character varying(36) NOT NULL,
    logistician_id character varying(36),
    service_head_id character varying(36),
    base_currency character varying(3) NOT NULL,
    deal_currency character varying(3),
    operating_conditions jsonb NOT NULL,
    service_flag boolean NOT NULL,
    target_hours double precision,
    client_payments jsonb NOT NULL,
    client_payment_wishes text NOT NULL,
    bank_guarantee jsonb NOT NULL,
    scenario_override jsonb NOT NULL,
    logistics_info jsonb NOT NULL,
    negotiation_info jsonb NOT NULL,
    logistics_done boolean NOT NULL,
    service_done boolean NOT NULL,
    current_version_no integer NOT NULL,
    approved_version_id character varying(36),
    storage_folder character varying(512) NOT NULL,
    outcome character varying(16),
    outcome_reason text NOT NULL,
    decision_expected_on date,
    status_checked_on date,
    status_check_note text NOT NULL,
    plan_fact_closed boolean NOT NULL,
    has_alternatives boolean NOT NULL,
    won_variant_id character varying(36),
    parent_deal_id character varying(36),
    request_group_id character varying(36),
    kp_brand character varying(16) NOT NULL,
    kp_images_mode character varying(16) NOT NULL,
    contract_number character varying(128) NOT NULL,
    contract_signed_at date,
    factory_contract_signed_at date,
    factory_contract_number character varying(64),
    client_requisites_id character varying(36),
    client_requisites jsonb NOT NULL,
    contract_history jsonb NOT NULL,
    contract_steps jsonb NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(36),
    delete_reason text NOT NULL,
    archived_at timestamp with time zone,
    archived_by character varying(36),
    archive_reason text NOT NULL,
    legal_review jsonb NOT NULL,
    execution_closed_at date,
    contract_terms_changed boolean NOT NULL,
    in_execution boolean NOT NULL,
    execution_started boolean NOT NULL,
    chain_approval jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: equipment_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.equipment_executions (
    id character varying(36) NOT NULL,
    model_id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    brand_name character varying(255) NOT NULL,
    tech_params jsonb NOT NULL,
    local_options jsonb NOT NULL,
    images jsonb NOT NULL,
    fingerprint character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    source_deal_id character varying(36),
    source_position_id character varying(36),
    proposed_by character varying(36),
    approved_by character varying(36),
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: equipment_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.equipment_models (
    id character varying(36) NOT NULL,
    factory_id character varying(36) NOT NULL,
    machine_type character varying(255) NOT NULL,
    machine_type_id character varying(36),
    model character varying(255) NOT NULL,
    trademark character varying(128) NOT NULL,
    brand_name character varying(255) NOT NULL,
    names jsonb NOT NULL,
    tech_params jsonb NOT NULL,
    logistics_params jsonb NOT NULL,
    default_hs_code character varying(10),
    hs_info jsonb NOT NULL,
    maintenance_regulations jsonb NOT NULL,
    default_hours_per_month double precision,
    acceptance_checklist jsonb NOT NULL,
    status character varying(16) NOT NULL,
    proposed_by character varying(36),
    approved_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: execution_stages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.execution_stages (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    position_id character varying(36),
    batch_id character varying(36),
    local_option_id character varying(36),
    code character varying(32) NOT NULL,
    title character varying(255) NOT NULL,
    sort integer NOT NULL,
    planned_on date,
    baseline_on date,
    refined_on date,
    refined_note text NOT NULL,
    actual_on date,
    amount double precision,
    currency character varying(3) NOT NULL,
    actual_amount double precision,
    amount_rub_net numeric(20,6),
    responsible_roles jsonb NOT NULL,
    event character varying(32) NOT NULL,
    done_by character varying(36),
    note text NOT NULL,
    date_history jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factories (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    name_zh character varying(255) NOT NULL,
    address text NOT NULL,
    country character varying(64) NOT NULL,
    contacts jsonb NOT NULL,
    wecom_external_ids jsonb NOT NULL,
    short_name character varying(128) NOT NULL,
    address_zh text NOT NULL,
    province character varying(128) NOT NULL,
    city character varying(128) NOT NULL,
    website character varying(255) NOT NULL,
    uscc character varying(32) NOT NULL,
    supplier_status character varying(32) NOT NULL,
    bank_details jsonb NOT NULL,
    default_currency character varying(3) NOT NULL,
    default_basis character varying(16) NOT NULL,
    machine_types jsonb NOT NULL,
    trademarks jsonb NOT NULL,
    notes text NOT NULL,
    is_active boolean NOT NULL,
    payment_terms jsonb NOT NULL,
    typical_production_days integer,
    rating double precision,
    legal_entity_id character varying(36),
    language character varying(8) NOT NULL,
    default_route_id character varying(36),
    supplies jsonb NOT NULL,
    approval_status character varying(16),
    approved_by character varying(36),
    approved_at timestamp with time zone,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factory_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_changes (
    id character varying(36) NOT NULL,
    factory_id character varying(36) NOT NULL,
    proposed_by character varying(36),
    proposed_at timestamp with time zone DEFAULT now() NOT NULL,
    data jsonb NOT NULL,
    before jsonb NOT NULL,
    comment text NOT NULL,
    status character varying(16) NOT NULL,
    decided_by character varying(36),
    decided_at timestamp with time zone,
    decision_comment text NOT NULL
);


--
-- Name: factory_offers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_offers (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    source_position_id character varying(36),
    factory_id character varying(36) NOT NULL,
    model character varying(255) NOT NULL,
    unit_price numeric(20,6),
    currency character varying(3) NOT NULL,
    basis character varying(32) NOT NULL,
    production_term jsonb NOT NULL,
    factory_payments jsonb NOT NULL,
    params jsonb NOT NULL,
    images jsonb NOT NULL,
    request_sent_at timestamp with time zone,
    answer_received_at timestamp with time zone,
    status character varying(16) NOT NULL,
    selected_deal_id character varying(36),
    selected_by character varying(36),
    comment text NOT NULL,
    mirror_of_id character varying(36),
    end_supplier_id character varying(36),
    route_id character varying(36),
    chain jsonb NOT NULL,
    mirror_state character varying(24) NOT NULL,
    revision integer NOT NULL,
    transfer_price numeric(20,6),
    transfer_note text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: factory_price_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.factory_price_history (
    id character varying(36) NOT NULL,
    factory_id character varying(36) NOT NULL,
    model character varying(255) NOT NULL,
    execution_id character varying(36),
    configuration text NOT NULL,
    unit_price numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    basis character varying(32) NOT NULL,
    production_term jsonb NOT NULL,
    factory_payments jsonb NOT NULL,
    "on" date NOT NULL,
    deal_id character varying(36),
    source character varying(16) NOT NULL
);


--
-- Name: file_download_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_download_requests (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    deal_id character varying(36),
    storage_path character varying(1024) NOT NULL,
    file_name character varying(512) NOT NULL,
    reason text NOT NULL,
    status character varying(16) NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_by character varying(36),
    decided_at timestamp with time zone,
    comment text NOT NULL,
    expires_at timestamp with time zone
);


--
-- Name: finance_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_accounts (
    id character varying(36) NOT NULL,
    legal_entity_id character varying(36) NOT NULL,
    bank_profile_id character varying(36) NOT NULL,
    title character varying(255) NOT NULL,
    currency character varying(3) NOT NULL,
    status character varying(16) NOT NULL,
    opened_on date,
    archived_on date,
    note text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: finance_allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_allocations (
    id character varying(36) NOT NULL,
    transaction_id character varying(36) NOT NULL,
    obligation_id character varying(36) NOT NULL,
    amount_tx numeric(18,2) NOT NULL,
    amount_obligation numeric(18,2) NOT NULL,
    fx_rate numeric(18,8),
    fx_on date,
    fx_source character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    allocated_on date NOT NULL,
    idempotency_key character varying(160) NOT NULL,
    note text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_by character varying(36),
    confirmed_at timestamp with time zone,
    reversed_by character varying(36),
    reversed_at timestamp with time zone,
    reverse_reason text NOT NULL,
    CONSTRAINT ck_fin_alloc_amount CHECK (((amount_tx > (0)::numeric) AND (amount_obligation > (0)::numeric))),
    CONSTRAINT ck_fin_alloc_status CHECK (((status)::text = ANY ((ARRAY['provisional'::character varying, 'confirmed'::character varying, 'reversed'::character varying])::text[])))
);


--
-- Name: finance_balance_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_balance_snapshots (
    id character varying(36) NOT NULL,
    account_id character varying(36) NOT NULL,
    as_of timestamp with time zone NOT NULL,
    amount numeric(18,2) NOT NULL,
    source character varying(32) NOT NULL,
    external_ref character varying(255) NOT NULL,
    attachment_id character varying(36),
    note text NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    confirmed_by character varying(36),
    confirmed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: finance_fx_operations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_fx_operations (
    id character varying(36) NOT NULL,
    sell_transaction_id character varying(36) NOT NULL,
    buy_transaction_id character varying(36) NOT NULL,
    fee_transaction_id character varying(36),
    sell_account_id character varying(36) NOT NULL,
    buy_account_id character varying(36) NOT NULL,
    sell_amount numeric(18,2) NOT NULL,
    sell_currency character varying(3) NOT NULL,
    buy_amount numeric(18,2) NOT NULL,
    buy_currency character varying(3) NOT NULL,
    rate numeric(18,8) NOT NULL,
    rate_source character varying(64) NOT NULL,
    executed_at timestamp with time zone NOT NULL,
    external_ref character varying(128) NOT NULL,
    status character varying(16) NOT NULL,
    reverse_reason text NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fin_fx_amounts CHECK (((sell_amount > (0)::numeric) AND (buy_amount > (0)::numeric) AND (rate > (0)::numeric))),
    CONSTRAINT ck_fin_fx_currencies CHECK (((sell_currency)::text <> (buy_currency)::text))
);


--
-- Name: finance_import_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_import_batches (
    id character varying(36) NOT NULL,
    account_id character varying(36) NOT NULL,
    file_name character varying(255) NOT NULL,
    file_sha256 character varying(64) NOT NULL,
    mapping jsonb NOT NULL,
    rows jsonb NOT NULL,
    rows_total integer NOT NULL,
    rows_valid integer NOT NULL,
    rows_duplicates integer NOT NULL,
    rows_errors integer NOT NULL,
    status character varying(16) NOT NULL,
    result jsonb NOT NULL,
    idempotency_key character varying(128),
    created_by character varying(36),
    confirmed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: finance_obligations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_obligations (
    id character varying(36) NOT NULL,
    source_type character varying(32) NOT NULL,
    source_id character varying(36) NOT NULL,
    source_line_key character varying(128) NOT NULL,
    source_revision character varying(64) NOT NULL,
    source_line_no integer,
    deal_id character varying(36),
    contract_id character varying(36),
    legal_entity_id character varying(36),
    counterparty_kind character varying(16) NOT NULL,
    counterparty_id character varying(36),
    counterparty_name_snapshot character varying(255) NOT NULL,
    superseded_by_id character varying(36),
    direction character varying(3) NOT NULL,
    nature character varying(16) NOT NULL,
    title character varying(255) NOT NULL,
    event_code character varying(32) NOT NULL,
    amount numeric(18,2) NOT NULL,
    currency character varying(3) NOT NULL,
    legal_due_on date,
    legal_due_text text NOT NULL,
    event_on date,
    event_is_fact boolean NOT NULL,
    forecast_on date,
    basis_snapshot jsonb NOT NULL,
    is_active boolean NOT NULL,
    excluded_reason character varying(255) NOT NULL,
    sync_status character varying(16) NOT NULL,
    synced_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fin_obligation_amount CHECK ((amount >= (0)::numeric)),
    CONSTRAINT ck_fin_obligation_direction CHECK (((direction)::text = ANY ((ARRAY['in'::character varying, 'out'::character varying])::text[])))
);


--
-- Name: finance_questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_questions (
    id character varying(36) NOT NULL,
    subject_type character varying(24) NOT NULL,
    subject_id character varying(64) NOT NULL,
    deal_id character varying(36),
    question text NOT NULL,
    context_snapshot jsonb NOT NULL,
    asked_by character varying(36),
    assignee_id character varying(36) NOT NULL,
    due_on date,
    status character varying(16) NOT NULL,
    answer text NOT NULL,
    answered_by character varying(36),
    answered_at timestamp with time zone,
    reviewed_by character varying(36),
    reviewed_at timestamp with time zone,
    review_comment text NOT NULL,
    source_updated_by character varying(36),
    source_updated_at timestamp with time zone,
    source_update_ref character varying(255) NOT NULL,
    files jsonb NOT NULL,
    history jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fin_question_status CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'answered'::character varying, 'verified'::character varying, 'rejected'::character varying, 'closed'::character varying])::text[])))
);


--
-- Name: finance_recognition_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_recognition_events (
    id character varying(36) NOT NULL,
    kind character varying(8) NOT NULL,
    deal_id character varying(36),
    obligation_id character varying(36),
    cost_item_code character varying(32),
    position_id character varying(36),
    amount numeric(18,2) NOT NULL,
    currency character varying(3) NOT NULL,
    fx_rate numeric(18,8),
    fx_source character varying(64) NOT NULL,
    fx_on date,
    amount_rub numeric(18,2) NOT NULL,
    recognized_on date NOT NULL,
    basis_type character varying(32) NOT NULL,
    document_type character varying(64) NOT NULL,
    document_number character varying(64) NOT NULL,
    document_date date,
    attachment_id character varying(36),
    source_type character varying(32) NOT NULL,
    source_id character varying(64) NOT NULL,
    status character varying(16) NOT NULL,
    reversal_reason text NOT NULL,
    idempotency_key character varying(128),
    comment text NOT NULL,
    created_by character varying(36),
    reversed_by character varying(36),
    reversed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fin_recognition_amount CHECK ((amount > (0)::numeric)),
    CONSTRAINT ck_fin_recognition_kind CHECK (((kind)::text = ANY ((ARRAY['revenue'::character varying, 'expense'::character varying])::text[])))
);


--
-- Name: finance_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finance_transactions (
    id character varying(36) NOT NULL,
    account_id character varying(36),
    direction character varying(3) NOT NULL,
    amount numeric(18,2) NOT NULL,
    currency character varying(3) NOT NULL,
    booked_at timestamp with time zone NOT NULL,
    value_date date,
    counterparty_name character varying(255) NOT NULL,
    purpose text NOT NULL,
    external_ref character varying(128) NOT NULL,
    import_batch_id character varying(36),
    status character varying(24) NOT NULL,
    reversal_of_id character varying(36),
    reversed_by_id character varying(36),
    origin_type character varying(24) NOT NULL,
    origin_id character varying(64),
    idempotency_key character varying(128) NOT NULL,
    attachment_id character varying(36),
    note text NOT NULL,
    created_by character varying(36),
    posted_by character varying(36),
    posted_at timestamp with time zone,
    withdrawn_at timestamp with time zone,
    withdrawn_by character varying(36),
    withdrawn_reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_fin_tx_amount CHECK ((amount > (0)::numeric)),
    CONSTRAINT ck_fin_tx_direction CHECK (((direction)::text = ANY ((ARRAY['in'::character varying, 'out'::character varying])::text[]))),
    CONSTRAINT ck_fin_tx_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'needs_reconciliation'::character varying, 'posted'::character varying, 'reversed'::character varying])::text[])))
);


--
-- Name: fx_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fx_rates (
    id character varying(36) NOT NULL,
    "on" date NOT NULL,
    currency character varying(3) NOT NULL,
    rate double precision NOT NULL,
    source character varying(16) NOT NULL,
    comment text NOT NULL
);


--
-- Name: glossary; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.glossary (
    id character varying(36) NOT NULL,
    term_ru character varying(512) NOT NULL,
    term_zh character varying(512) NOT NULL,
    kind character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    changed_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hs_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hs_codes (
    id character varying(36) NOT NULL,
    code character varying(10) NOT NULL,
    description text NOT NULL,
    duty_pct double precision NOT NULL,
    vat_pct double precision NOT NULL,
    util_fee boolean NOT NULL,
    licence_required boolean NOT NULL,
    certification jsonb NOT NULL,
    valid_from date NOT NULL,
    valid_to date,
    version_file character varying(255) NOT NULL
);


--
-- Name: import_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_mappings (
    id character varying(36) NOT NULL,
    scope character varying(80) NOT NULL,
    target character varying(32) NOT NULL,
    header_sig jsonb NOT NULL,
    mapping jsonb NOT NULL,
    updated_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: integration_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_log (
    id integer NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    system character varying(32) NOT NULL,
    operation character varying(64) NOT NULL,
    ok boolean NOT NULL,
    details jsonb NOT NULL
);


--
-- Name: integration_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.integration_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: integration_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.integration_log_id_seq OWNED BY public.integration_log.id;


--
-- Name: job_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_runs (
    job character varying(64) NOT NULL,
    last_start timestamp with time zone,
    last_ok timestamp with time zone,
    last_error text DEFAULT ''::text NOT NULL,
    error_at timestamp with time zone,
    runs integer DEFAULT 0 NOT NULL,
    duration_ms integer,
    host character varying(64) DEFAULT ''::character varying NOT NULL
);


--
-- Name: kp_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kp_revisions (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    version_id character varying(36),
    no integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(36),
    description text NOT NULL,
    changes jsonb NOT NULL,
    price_before_rub numeric(20,6),
    price_rub numeric(20,6),
    margin_pct double precision,
    prices jsonb NOT NULL,
    kp_path character varying(1024) NOT NULL,
    pdf_path character varying(1024) NOT NULL,
    sent_at timestamp with time zone
);


--
-- Name: legal_entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.legal_entities (
    id character varying(36) NOT NULL,
    code character varying(16) NOT NULL,
    name character varying(255) NOT NULL,
    country character varying(64) NOT NULL,
    currency character varying(3) NOT NULL,
    warehouse character varying(16) NOT NULL,
    sells_to_clients boolean NOT NULL,
    channels jsonb NOT NULL,
    is_active boolean NOT NULL,
    notes text NOT NULL,
    doc_prefix character varying(8) DEFAULT ''::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: legal_packages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.legal_packages (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    kind character varying(16) NOT NULL,
    counterparty_kind character varying(16) NOT NULL,
    counterparty_id character varying(36) NOT NULL,
    version_no integer NOT NULL,
    deal_version_id character varying(36),
    deal_version_no integer,
    own_entity_id character varying(36),
    own_profile_id character varying(36),
    own_bank_id character varying(36),
    cp_profile_id character varying(36),
    cp_bank_id character varying(36),
    language character varying(16) NOT NULL,
    templates jsonb NOT NULL,
    rules_version character varying(32) NOT NULL,
    snapshot jsonb NOT NULL,
    checks jsonb NOT NULL,
    files jsonb NOT NULL,
    status character varying(32) NOT NULL,
    stage_code character varying(32) NOT NULL,
    approvals jsonb NOT NULL,
    notes text NOT NULL,
    created_by character varying(36),
    superseded_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: legal_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.legal_profiles (
    id character varying(36) NOT NULL,
    owner_kind character varying(16) NOT NULL,
    owner_id character varying(36) NOT NULL,
    kind character varying(32) NOT NULL,
    title character varying(255) NOT NULL,
    is_default boolean NOT NULL,
    country character varying(64) NOT NULL,
    names jsonb NOT NULL,
    opf character varying(128) NOT NULL,
    reg_number character varying(64) NOT NULL,
    tax_number character varying(64) NOT NULL,
    kpp character varying(16) NOT NULL,
    legal_address text NOT NULL,
    actual_address text NOT NULL,
    postal_address text NOT NULL,
    director character varying(255) NOT NULL,
    signatory_name character varying(255) NOT NULL,
    signatory_position character varying(255) NOT NULL,
    signatory_basis character varying(255) NOT NULL,
    contacts jsonb NOT NULL,
    documents jsonb NOT NULL,
    powers_of_attorney jsonb NOT NULL,
    extra jsonb NOT NULL,
    check_status character varying(16) NOT NULL,
    checked_at timestamp with time zone,
    checked_by character varying(36),
    lawyer_comment text NOT NULL,
    is_active boolean NOT NULL,
    history jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: legal_zip_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.legal_zip_sets (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    factory_id character varying(36) NOT NULL,
    lines jsonb NOT NULL,
    pricing_mode character varying(16) NOT NULL,
    confirmed_at timestamp with time zone,
    confirmed_by character varying(36),
    history jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: local_options; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.local_options (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    position_id character varying(36) NOT NULL,
    tech_param_id character varying(36),
    name character varying(255) NOT NULL,
    qty integer NOT NULL,
    supplier character varying(255) NOT NULL,
    quote_attachment_id character varying(36),
    cost numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    supplier_lead_days integer NOT NULL,
    needs_install boolean NOT NULL,
    install_stage character varying(32) NOT NULL,
    installer character varying(32) NOT NULL,
    install_days integer NOT NULL,
    install_cost_rub numeric(20,6) NOT NULL,
    per_unit boolean NOT NULL,
    status character varying(16) NOT NULL,
    cost_approved_by character varying(36),
    ordered_at date,
    installed_at date,
    comment text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: logistics_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.logistics_history (
    id character varying(36) NOT NULL,
    item_code character varying(32) NOT NULL,
    route character varying(255) NOT NULL,
    machine_type character varying(255) NOT NULL,
    region character varying(255) NOT NULL,
    amount numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    per_unit numeric(20,6),
    "on" date NOT NULL,
    deal_id character varying(36),
    batch_id character varying(36),
    source character varying(16) NOT NULL
);


--
-- Name: machine_hours; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.machine_hours (
    id character varying(36) NOT NULL,
    machine_id character varying(36) NOT NULL,
    "on" date NOT NULL,
    hours double precision NOT NULL,
    entered_by character varying(36),
    comment text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: machine_reminders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.machine_reminders (
    id character varying(36) NOT NULL,
    machine_id character varying(36) NOT NULL,
    to_hours double precision NOT NULL,
    due_on date,
    kit_ids jsonb NOT NULL,
    manager_id character varying(36),
    notified_at timestamp with time zone DEFAULT now() NOT NULL,
    request_id character varying(36),
    dismissed boolean NOT NULL
);


--
-- Name: machine_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.machine_types (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    name_zh character varying(255) NOT NULL,
    status character varying(16) NOT NULL,
    proposed_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: maintenance_kits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_kits (
    id character varying(36) NOT NULL,
    equipment_model_id character varying(36),
    other_model_id character varying(36),
    name character varying(128) NOT NULL,
    interval_hours double precision NOT NULL,
    lines jsonb NOT NULL,
    source character varying(16) NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: markup_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.markup_policies (
    id character varying(36) NOT NULL,
    version_no integer NOT NULL,
    status character varying(16) NOT NULL,
    valid_from date,
    reason text NOT NULL,
    rules jsonb NOT NULL,
    approvals jsonb NOT NULL,
    rejection jsonb NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_at timestamp with time zone
);


--
-- Name: mfa_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_challenges (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    verifier_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    attempts integer NOT NULL,
    consumed_at timestamp with time zone,
    remember boolean NOT NULL,
    auth_method character varying(16) NOT NULL,
    ip_prefix character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mfa_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_credentials (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    secret_ciphertext text NOT NULL,
    key_id character varying(16) NOT NULL,
    enabled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    last_used_step bigint NOT NULL,
    failed_attempts integer NOT NULL,
    locked_until timestamp with time zone,
    pending_secret_ciphertext text NOT NULL,
    pending_key_id character varying(16) NOT NULL,
    pending_expires_at timestamp with time zone
);


--
-- Name: mfa_recovery_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_recovery_codes (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    code_hash character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    used_at timestamp with time zone,
    used_session_id character varying(36)
);


--
-- Name: model_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_documents (
    id character varying(36) NOT NULL,
    model_id character varying(36) NOT NULL,
    kind character varying(24) NOT NULL,
    title character varying(255) NOT NULL,
    language character varying(8) NOT NULL,
    version character varying(64) NOT NULL,
    file_name character varying(255) NOT NULL,
    storage_path character varying(1024) NOT NULL,
    size integer NOT NULL,
    source_deal_id character varying(36),
    comment text NOT NULL,
    active boolean NOT NULL,
    uploaded_by character varying(36),
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: negotiation_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.negotiation_steps (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    version_id character varying(36),
    no integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(36),
    kind character varying(20) NOT NULL,
    reason character varying(30) NOT NULL,
    comment text NOT NULL,
    input jsonb NOT NULL,
    terms jsonb NOT NULL,
    result jsonb NOT NULL,
    price_before_rub numeric(20,6),
    price_rub numeric(20,6),
    margin_pct double precision,
    show_discount boolean NOT NULL,
    kp_revision_id character varying(36),
    sent_at timestamp with time zone,
    sent_to jsonb NOT NULL,
    client_answer text NOT NULL,
    review jsonb NOT NULL
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id character varying(36) NOT NULL,
    deal_id character varying(36),
    user_id character varying(36),
    event character varying(32) NOT NULL,
    channel character varying(16) NOT NULL,
    text text NOT NULL,
    status character varying(16) NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    error text NOT NULL,
    read_at timestamp with time zone,
    link character varying(300)
);


--
-- Name: offer_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offer_prices (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    variant_id character varying(36) NOT NULL,
    version_id character varying(36),
    kind character varying(16) NOT NULL,
    source_id character varying(36) NOT NULL,
    revision_no integer,
    total_rub numeric(20,6) NOT NULL,
    extras_rub numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    fx_rate numeric(20,6),
    units integer NOT NULL,
    unit_amount numeric(20,6),
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    seq bigint NOT NULL
);


--
-- Name: office_counters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.office_counters (
    id character varying(36) NOT NULL,
    entity_id character varying(36) NOT NULL,
    journal character varying(16) NOT NULL,
    year integer NOT NULL,
    seq integer NOT NULL
);


--
-- Name: office_docs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.office_docs (
    id character varying(36) NOT NULL,
    entity_id character varying(36) NOT NULL,
    kind character varying(16) NOT NULL,
    category character varying(16) NOT NULL,
    journal character varying(16) NOT NULL,
    number character varying(48) NOT NULL,
    seq integer,
    year integer,
    reg_date date,
    status character varying(16) NOT NULL,
    subject character varying(512) NOT NULL,
    summary text NOT NULL,
    body text NOT NULL,
    letter_type character varying(16) NOT NULL,
    channel character varying(16) NOT NULL,
    counterparty character varying(255) NOT NULL,
    contact character varying(255) NOT NULL,
    sender_number character varying(64) NOT NULL,
    sender_date date,
    due_on date,
    executor_id character varying(36),
    co_executor_ids jsonb NOT NULL,
    deal_ids jsonb NOT NULL,
    deal_section character varying(16) NOT NULL,
    contract_id character varying(36),
    claim_id character varying(36),
    reply_to_id character varying(36),
    signer_id character varying(36),
    signed_on date,
    sign_method character varying(16) NOT NULL,
    sent_on date,
    effective_on date,
    closed_on date,
    close_note text NOT NULL,
    files jsonb NOT NULL,
    history jsonb NOT NULL,
    created_by character varying(36),
    approval jsonb NOT NULL,
    comments jsonb NOT NULL,
    note text NOT NULL,
    urgent boolean NOT NULL,
    confidential boolean NOT NULL,
    needs_reply boolean NOT NULL,
    pages integer,
    attachments_count integer,
    address character varying(512) NOT NULL,
    tracking character varying(128) NOT NULL,
    delivered_on date,
    reviewer_id character varying(36),
    resolutions jsonb NOT NULL,
    alerts jsonb NOT NULL,
    acks jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: office_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.office_routes (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    kinds jsonb NOT NULL,
    categories jsonb NOT NULL,
    entity_id character varying(36),
    mode character varying(8) NOT NULL,
    steps jsonb NOT NULL,
    required boolean NOT NULL,
    is_active boolean NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: office_signers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.office_signers (
    id character varying(36) NOT NULL,
    entity_id character varying(36) NOT NULL,
    user_id character varying(36),
    full_name character varying(255) NOT NULL,
    "position" character varying(255) NOT NULL,
    basis character varying(255) NOT NULL,
    poa_id character varying(36),
    valid_from date,
    valid_to date,
    methods jsonb NOT NULL,
    is_active boolean NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: org_directions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.org_directions (
    key character varying(32) NOT NULL,
    head_id character varying(36),
    updated_by character varying(36),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: org_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.org_members (
    user_id character varying(36) NOT NULL,
    direction_key character varying(32) NOT NULL,
    updated_by character varying(36),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: other_machine_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.other_machine_models (
    id character varying(36) NOT NULL,
    manufacturer character varying(255) NOT NULL,
    model character varying(255) NOT NULL,
    machine_type character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_applicability; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_applicability (
    id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    equipment_model_id character varying(36),
    other_manufacturer character varying(255) NOT NULL,
    other_model character varying(255) NOT NULL,
    assembly character varying(255) NOT NULL
);


--
-- Name: part_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_batches (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    forwarder character varying(255) NOT NULL,
    route_id character varying(36),
    status character varying(16) NOT NULL,
    shipped_on date,
    eta date,
    costs jsonb NOT NULL,
    allocation character varying(8) NOT NULL,
    tracking character varying(128) NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_client_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_client_prices (
    id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    client_id character varying(36),
    client_name character varying(255) NOT NULL,
    price double precision NOT NULL,
    currency character varying(3) NOT NULL,
    valid_from date,
    valid_to date
);


--
-- Name: part_cross_refs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_cross_refs (
    id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    kind character varying(16) NOT NULL,
    number character varying(128) NOT NULL,
    supplier_id character varying(36),
    analog_part_id character varying(36),
    manufacturer character varying(255) NOT NULL,
    comment text NOT NULL
);


--
-- Name: part_deviations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_deviations (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    po_id character varying(36) NOT NULL,
    po_line_id character varying(36) NOT NULL,
    receipt_line_id character varying(36),
    kind character varying(16) NOT NULL,
    qty double precision NOT NULL,
    decision character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    claim_text text NOT NULL,
    claim_sent_at timestamp with time zone,
    photos jsonb NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_inventories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_inventories (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    warehouse_id character varying(36) NOT NULL,
    cell_prefix character varying(32) NOT NULL,
    part_group character varying(128) NOT NULL,
    status character varying(16) NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    submitted_by character varying(36),
    submitted_at timestamp with time zone,
    approved_by character varying(36),
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_inventory_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_inventory_lines (
    id character varying(36) NOT NULL,
    inventory_id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    cell character varying(32) NOT NULL,
    expected double precision NOT NULL,
    counted double precision,
    added boolean NOT NULL,
    counted_by character varying(36),
    counted_at timestamp with time zone,
    comment text NOT NULL
);


--
-- Name: part_kps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_kps (
    id character varying(36) NOT NULL,
    request_id character varying(36) NOT NULL,
    revision_no integer NOT NULL,
    number character varying(48) NOT NULL,
    currency character varying(3) NOT NULL,
    fx_rate double precision,
    lines jsonb NOT NULL,
    handling_fee double precision NOT NULL,
    handling_fee_removed_by character varying(36),
    total double precision NOT NULL,
    valid_until date NOT NULL,
    changes jsonb NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    sent_by character varying(36)
);


--
-- Name: part_receipt_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_receipt_lines (
    id character varying(36) NOT NULL,
    receipt_id character varying(36) NOT NULL,
    po_line_id character varying(36) NOT NULL,
    part_id character varying(36),
    qty_expected double precision NOT NULL,
    qty double precision NOT NULL,
    qty_bad double precision NOT NULL,
    cell character varying(32) NOT NULL,
    discrepancy character varying(16) NOT NULL,
    reserved_for_line_id character varying(36),
    comment text NOT NULL
);


--
-- Name: part_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_receipts (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    po_id character varying(36) NOT NULL,
    warehouse_id character varying(36) NOT NULL,
    received_on date NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_request_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_request_lines (
    id character varying(36) NOT NULL,
    request_id character varying(36) NOT NULL,
    no integer NOT NULL,
    parent_line_id character varying(36),
    raw_article character varying(128) NOT NULL,
    raw_name character varying(512) NOT NULL,
    qty double precision NOT NULL,
    unit character varying(16) NOT NULL,
    part_id character varying(36),
    match_kind character varying(16) NOT NULL,
    source character varying(16) NOT NULL,
    supplier_id character varying(36),
    route_id character varying(36),
    lead_days integer,
    price double precision,
    currency character varying(3) NOT NULL,
    price_source character varying(16) NOT NULL,
    calc jsonb NOT NULL,
    status character varying(24) NOT NULL,
    comment text NOT NULL,
    rfq_line_id character varying(36),
    quote_price double precision,
    quote_currency character varying(3) NOT NULL,
    eta date,
    shipped_qty double precision NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_requests (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    machine_id character varying(36),
    sla_notified jsonb NOT NULL,
    client_id character varying(36) NOT NULL,
    manager_id character varying(36),
    contact character varying(255) NOT NULL,
    channel character varying(16) NOT NULL,
    machine character varying(255) NOT NULL,
    urgent boolean NOT NULL,
    warranty boolean NOT NULL,
    comment text NOT NULL,
    closed character varying(16) NOT NULL,
    close_reason text NOT NULL,
    approvals jsonb NOT NULL,
    chain_approval jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_reservations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_reservations (
    id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    warehouse_id character varying(36) NOT NULL,
    line_id character varying(36) NOT NULL,
    kp_id character varying(36),
    qty double precision NOT NULL,
    kind character varying(8) NOT NULL,
    expires_at date,
    released_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_shipment_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_shipment_lines (
    id character varying(36) NOT NULL,
    shipment_id character varying(36) NOT NULL,
    request_line_id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    qty double precision NOT NULL
);


--
-- Name: part_shipments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_shipments (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    request_id character varying(36) NOT NULL,
    warehouse_id character varying(36) NOT NULL,
    shipped_on date NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    onec_status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: part_stock; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_stock (
    id character varying(36) NOT NULL,
    part_id character varying(36) NOT NULL,
    warehouse_id character varying(36) NOT NULL,
    qty double precision NOT NULL,
    reserved double precision NOT NULL,
    in_transit double precision NOT NULL,
    min_qty double precision,
    cell character varying(32) NOT NULL,
    as_of timestamp with time zone
);


--
-- Name: part_stock_moves; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_stock_moves (
    id character varying(36) NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    part_id character varying(36) NOT NULL,
    warehouse_id character varying(36) NOT NULL,
    qty double precision NOT NULL,
    kind character varying(16) NOT NULL,
    doc_number character varying(32) NOT NULL,
    receipt_line_id character varying(36),
    shipment_line_id character varying(36),
    user_id character varying(36),
    onec_status character varying(16) NOT NULL
);


--
-- Name: parts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts (
    id character varying(36) NOT NULL,
    article character varying(128) NOT NULL,
    name_ru character varying(512) NOT NULL,
    unit character varying(16) NOT NULL,
    part_group character varying(128) NOT NULL,
    status character varying(16) NOT NULL,
    names jsonb NOT NULL,
    description text NOT NULL,
    manufacturer character varying(255) NOT NULL,
    oem_number character varying(128) NOT NULL,
    supplier_id character varying(36),
    route_id character varying(36),
    purchase_price double precision,
    purchase_currency character varying(3) NOT NULL,
    purchase_on date,
    lead_days integer,
    hs_code character varying(14) NOT NULL,
    origin_country character varying(64) NOT NULL,
    moq double precision,
    multiple double precision,
    weight_kg double precision,
    length_mm double precision,
    width_mm double precision,
    height_mm double precision,
    volume_m3 double precision,
    sale_price double precision,
    sale_currency character varying(3) NOT NULL,
    min_price double precision,
    min_price_currency character varying(3) NOT NULL,
    photos jsonb NOT NULL,
    photo_files jsonb NOT NULL,
    spec_no character varying(128) NOT NULL,
    addendum_no character varying(128) NOT NULL,
    ms_id character varying(36),
    ms_code character varying(64) NOT NULL,
    source character varying(16) NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: password_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_history (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    password_hash character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: password_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_links (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    token_hash character varying(64) NOT NULL,
    purpose character varying(16) NOT NULL,
    sent_to character varying(255) NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    revoked_at timestamp with time zone
);


--
-- Name: payment_schemes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_schemes (
    id character varying(36) NOT NULL,
    name character varying(128) NOT NULL,
    lines jsonb NOT NULL
);


--
-- Name: plan_fact_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plan_fact_entries (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    position_id character varying(36),
    kind character varying(16) NOT NULL,
    cost_item_code character varying(32),
    ref character varying(128) NOT NULL,
    amount numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    fx_rate numeric(20,6),
    "on" date NOT NULL,
    deviation_reason character varying(64) NOT NULL,
    comment text NOT NULL,
    entered_by character varying(36),
    entered_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: po_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.po_lines (
    id character varying(36) NOT NULL,
    po_id character varying(36) NOT NULL,
    no integer NOT NULL,
    request_line_id character varying(36),
    parent_line_id character varying(36),
    part_id character varying(36),
    number character varying(128) NOT NULL,
    name character varying(512) NOT NULL,
    name_foreign character varying(512) NOT NULL,
    qty double precision NOT NULL,
    unit character varying(16) NOT NULL,
    price double precision NOT NULL,
    lead_days integer,
    status character varying(16) NOT NULL,
    received_qty double precision NOT NULL,
    eta date,
    overdue_eta date,
    warranty_claim_id character varying(36),
    batch_id character varying(36),
    extra_cost_rub double precision NOT NULL,
    history jsonb NOT NULL,
    comment text NOT NULL
);


--
-- Name: position_images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.position_images (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    position_id character varying(36) NOT NULL,
    attachment_id character varying(36) NOT NULL,
    kind character varying(16) NOT NULL,
    source character varying(16) NOT NULL,
    caption character varying(512) NOT NULL,
    has_factory_logo boolean NOT NULL,
    in_kp boolean NOT NULL,
    kp_order integer NOT NULL,
    send_to_factory boolean NOT NULL,
    uploaded_by character varying(36),
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: position_tech_params; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.position_tech_params (
    id character varying(36) NOT NULL,
    position_id character varying(36) NOT NULL,
    no integer NOT NULL,
    code character varying(16) NOT NULL,
    name_ru character varying(512) NOT NULL,
    name_zh character varying(512) NOT NULL,
    unit character varying(64) NOT NULL,
    customer_requirement_ru text NOT NULL,
    customer_requirement_zh text NOT NULL,
    value_ru text NOT NULL,
    value_zh text NOT NULL,
    comment_ru text NOT NULL,
    comment_zh text NOT NULL,
    required boolean NOT NULL,
    local_ru boolean NOT NULL,
    source character varying(16) NOT NULL,
    translation_status character varying(16) NOT NULL,
    section character varying(255) NOT NULL,
    filled_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.positions (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    factory_id character varying(36),
    equipment_model_id character varying(36),
    machine_type character varying(255) NOT NULL,
    trademark character varying(128) NOT NULL,
    model character varying(255) NOT NULL,
    qty integer NOT NULL,
    unit_price numeric(20,6) NOT NULL,
    currency character varying(3) NOT NULL,
    options jsonb NOT NULL,
    options_price numeric(20,6) NOT NULL,
    basis character varying(32) NOT NULL,
    delivery_point character varying(255) NOT NULL,
    production_days integer NOT NULL,
    delivery_days integer NOT NULL,
    production_term jsonb NOT NULL,
    delivery_term jsonb NOT NULL,
    warranty_months integer,
    warranty_hours double precision,
    zip_price numeric(20,6),
    factory_payments jsonb NOT NULL,
    factory_payments_offer jsonb NOT NULL,
    tech_params jsonb NOT NULL,
    logistics_params jsonb NOT NULL,
    price_reference jsonb NOT NULL,
    rfq_import_id character varying(36),
    execution_id character varying(36),
    offer_id character varying(36),
    logistics jsonb NOT NULL,
    batch_id character varying(36),
    service_calc jsonb NOT NULL,
    zip_items jsonb NOT NULL,
    warranty_reserve_pct double precision,
    actual_events jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: powers_of_attorney; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.powers_of_attorney (
    id character varying(36) NOT NULL,
    entity_id character varying(36) NOT NULL,
    profile_id character varying(36),
    number character varying(64) NOT NULL,
    seq integer,
    kind character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    issued_on date,
    valid_to date,
    place character varying(255) NOT NULL,
    principal jsonb NOT NULL,
    attorney_user_id character varying(36),
    attorney jsonb NOT NULL,
    powers jsonb NOT NULL,
    limits jsonb NOT NULL,
    documents_text text NOT NULL,
    files jsonb NOT NULL,
    history jsonb NOT NULL,
    revoked_at timestamp with time zone,
    revoked_by character varying(36),
    revoke_reason text NOT NULL,
    notified jsonb NOT NULL,
    facts jsonb NOT NULL,
    notes text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: purchase_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_orders (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    sla_notified jsonb NOT NULL,
    kind character varying(16) NOT NULL,
    parent_po_id character varying(36),
    step_no integer NOT NULL,
    route_id character varying(36),
    buyer_entity_id character varying(36),
    seller_entity_id character varying(36),
    supplier_id character varying(36),
    currency character varying(3) NOT NULL,
    incoterms character varying(8) NOT NULL,
    language character varying(4) NOT NULL,
    status character varying(16) NOT NULL,
    sent_at timestamp with time zone,
    send_log jsonb NOT NULL,
    confirmed_at timestamp with time zone,
    supplier_ref character varying(64) NOT NULL,
    planned_ship_date date,
    actual_ship_date date,
    payments jsonb NOT NULL,
    total double precision NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: quote_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quote_links (
    id character varying(36) NOT NULL,
    token_hash character varying(64) NOT NULL,
    deal_id character varying(36) NOT NULL,
    request_id character varying(36) NOT NULL,
    request_no character varying(64) NOT NULL,
    email character varying(255) NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    answered_at timestamp with time zone,
    quote_ids text NOT NULL,
    answers integer NOT NULL
);


--
-- Name: rate_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rate_settings (
    id character varying(36) NOT NULL,
    key character varying(64) NOT NULL,
    value jsonb NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_to timestamp with time zone,
    changed_by character varying(36),
    comment text NOT NULL
);


--
-- Name: rfq_imports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rfq_imports (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    factory_id character varying(36),
    file_path character varying(512) NOT NULL,
    file_hash character varying(64) NOT NULL,
    template_version character varying(32) NOT NULL,
    attempt integer NOT NULL,
    parsed jsonb NOT NULL,
    errors jsonb NOT NULL,
    imported_by character varying(36),
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: route_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.route_steps (
    id character varying(36) NOT NULL,
    route_id character varying(36) NOT NULL,
    no integer NOT NULL,
    seller_entity_id character varying(36),
    buyer_entity_id character varying(36) NOT NULL,
    incoterms character varying(8) NOT NULL,
    place character varying(255) NOT NULL,
    currency character varying(3) NOT NULL,
    customs_country character varying(64) NOT NULL,
    pay_days integer,
    transit_days integer,
    comment text NOT NULL
);


--
-- Name: security_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.security_alerts (
    id character varying(36) NOT NULL,
    rule_key character varying(48) NOT NULL,
    rule_version character varying(16) NOT NULL,
    severity character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    subject_user_id character varying(36),
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    occurrence_count integer NOT NULL,
    summary character varying(300) NOT NULL,
    details_sanitized jsonb NOT NULL,
    last_event_id character varying(36),
    acknowledged_by character varying(36),
    acknowledged_at timestamp with time zone,
    ack_comment text NOT NULL,
    resolved_by character varying(36),
    resolved_at timestamp with time zone,
    resolution_comment text NOT NULL,
    CONSTRAINT ck_security_alert_severity CHECK (((severity)::text = ANY ((ARRAY['low'::character varying, 'medium'::character varying, 'high'::character varying, 'critical'::character varying])::text[]))),
    CONSTRAINT ck_security_alert_status CHECK (((status)::text = ANY ((ARRAY['new'::character varying, 'acknowledged'::character varying, 'resolved'::character varying])::text[])))
);


--
-- Name: security_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.security_events (
    id character varying(36) NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_user_id character varying(36),
    subject_user_id character varying(36),
    event_type character varying(32) NOT NULL,
    action character varying(48) NOT NULL,
    result character varying(16) NOT NULL,
    request_id character varying(36) NOT NULL,
    session_id character varying(36),
    ip_prefix character varying(64) NOT NULL,
    entity_type character varying(32) NOT NULL,
    entity_id character varying(128) NOT NULL,
    deal_id character varying(36),
    format character varying(16) NOT NULL,
    row_count integer,
    byte_size bigint,
    metadata jsonb NOT NULL,
    risk_score integer,
    alert_id character varying(36)
);


--
-- Name: signal_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_log (
    key character varying(160) NOT NULL,
    priority character varying(16) NOT NULL,
    title character varying(512) NOT NULL,
    object_type character varying(64) NOT NULL,
    object_number character varying(64) NOT NULL,
    direction character varying(64) NOT NULL,
    url character varying(300) NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    times integer NOT NULL
);


--
-- Name: supplier_rfq_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_rfq_lines (
    id character varying(36) NOT NULL,
    rfq_id character varying(36) NOT NULL,
    no integer NOT NULL,
    request_line_id character varying(36),
    part_id character varying(36),
    number character varying(128) NOT NULL,
    name_ru character varying(512) NOT NULL,
    name_foreign character varying(512) NOT NULL,
    machine_translated boolean NOT NULL,
    qty double precision NOT NULL,
    unit character varying(16) NOT NULL,
    availability character varying(16) NOT NULL,
    price double precision,
    currency character varying(3) NOT NULL,
    lead_days integer,
    moq double precision,
    analog_number character varying(128) NOT NULL,
    comment text NOT NULL,
    answered_at timestamp with time zone,
    chain jsonb NOT NULL
);


--
-- Name: supplier_rfqs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_rfqs (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    sla_notified jsonb NOT NULL,
    supplier_id character varying(36) NOT NULL,
    language character varying(4) NOT NULL,
    status character varying(16) NOT NULL,
    due_date date,
    sent_at timestamp with time zone,
    send_log jsonb NOT NULL,
    comment text NOT NULL,
    created_by character varying(36),
    mirror_of_id character varying(36),
    end_supplier_id character varying(36),
    route_id character varying(36),
    mirror_state character varying(24) NOT NULL,
    chain jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: supply_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supply_routes (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    description text NOT NULL,
    supplier_country character varying(64) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: support_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.support_attachments (
    id character varying(36) NOT NULL,
    ticket_id character varying(36) NOT NULL,
    message_id character varying(36),
    file_name character varying(255) NOT NULL,
    storage_path character varying(1024) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size bigint NOT NULL,
    scan_status character varying(20) NOT NULL,
    scan_id character varying(36),
    uploaded_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: support_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.support_messages (
    id character varying(36) NOT NULL,
    ticket_id character varying(36) NOT NULL,
    author_id character varying(36),
    kind character varying(16) NOT NULL,
    body text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_support_message_kind CHECK (((kind)::text = ANY ((ARRAY['user'::character varying, 'specialist'::character varying, 'system'::character varying])::text[])))
);


--
-- Name: support_number_sequences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.support_number_sequences (
    year integer NOT NULL,
    next_value integer NOT NULL
);


--
-- Name: support_number_sequences_year_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.support_number_sequences_year_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: support_number_sequences_year_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.support_number_sequences_year_seq OWNED BY public.support_number_sequences.year;


--
-- Name: support_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.support_tickets (
    id character varying(36) NOT NULL,
    number character varying(16) NOT NULL,
    reporter_id character varying(36) NOT NULL,
    assignee_id character varying(36),
    category character varying(16) NOT NULL,
    requested_urgency character varying(16) NOT NULL,
    priority character varying(16) NOT NULL,
    subject character varying(200) NOT NULL,
    description text NOT NULL,
    current_route character varying(300) NOT NULL,
    app_commit character varying(40) NOT NULL,
    correlation_id character varying(40) NOT NULL,
    client_metadata jsonb NOT NULL,
    status character varying(20) NOT NULL,
    idempotency_key character varying(64),
    first_response_at timestamp with time zone,
    resolved_at timestamp with time zone,
    closed_at timestamp with time zone,
    last_activity_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reopened_from_id character varying(36),
    CONSTRAINT ck_support_category CHECK (((category)::text = ANY ((ARRAY['error'::character varying, 'question'::character varying, 'access'::character varying, 'improvement'::character varying])::text[]))),
    CONSTRAINT ck_support_priority CHECK (((priority)::text = ANY ((ARRAY['low'::character varying, 'normal'::character varying, 'high'::character varying, 'critical'::character varying])::text[]))),
    CONSTRAINT ck_support_status CHECK (((status)::text = ANY ((ARRAY['new'::character varying, 'in_progress'::character varying, 'waiting_reporter'::character varying, 'resolved'::character varying, 'closed'::character varying])::text[]))),
    CONSTRAINT ck_support_urgency CHECK (((requested_urgency)::text = ANY ((ARRAY['normal'::character varying, 'high'::character varying, 'critical'::character varying])::text[])))
);


--
-- Name: tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tasks (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    role character varying(32) NOT NULL,
    assignee_id character varying(36),
    title character varying(255) NOT NULL,
    block character varying(64) NOT NULL,
    due date NOT NULL,
    done_at timestamp with time zone,
    overdue_notified boolean NOT NULL,
    needs_reassignment boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tech_param_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tech_param_templates (
    id character varying(36) NOT NULL,
    machine_type_id character varying(36),
    no integer NOT NULL,
    code character varying(16) NOT NULL,
    name_ru character varying(512) NOT NULL,
    name_zh character varying(512) NOT NULL,
    unit character varying(64) NOT NULL,
    required boolean NOT NULL,
    section character varying(255) NOT NULL
);


--
-- Name: templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.templates (
    id character varying(36) NOT NULL,
    kind character varying(32) NOT NULL,
    version integer NOT NULL,
    file_path character varying(512) NOT NULL,
    is_active boolean NOT NULL,
    uploaded_by character varying(36),
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    title character varying(255) NOT NULL,
    language character varying(16) NOT NULL,
    country character varying(64) NOT NULL,
    deal_kind character varying(16) NOT NULL,
    variables jsonb NOT NULL,
    notes text NOT NULL,
    is_example boolean NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id character varying(36) NOT NULL,
    external_id character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    display_name character varying(255) NOT NULL,
    roles jsonb NOT NULL,
    observer_scope jsonb NOT NULL,
    deputy_id character varying(36),
    deputy_from date,
    deputy_to date,
    visa_limit_rub double precision,
    notify_channels jsonb NOT NULL,
    wecom_user_id character varying(100),
    contacts jsonb NOT NULL,
    is_active boolean NOT NULL,
    last_seen_at timestamp with time zone,
    pending_changes jsonb NOT NULL,
    prefs jsonb NOT NULL,
    password_hash character varying(255),
    password_set_at timestamp with time zone,
    password_must_change boolean NOT NULL,
    offboarded_at timestamp with time zone,
    offboarded_by character varying(36),
    failed_logins integer NOT NULL,
    locked_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: variants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.variants (
    id character varying(36) NOT NULL,
    deal_id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    position_ids jsonb NOT NULL,
    price_rub numeric(20,6),
    price_mode character varying(16) NOT NULL,
    price_currency character varying(3),
    price_amount numeric(20,6),
    price_fx_rate numeric(20,6),
    price_vat_included boolean NOT NULL,
    price_vat_pct double precision,
    price_input_amount numeric(20,6),
    price_input_currency character varying(3),
    price_input_units integer,
    service_in_kp boolean NOT NULL,
    client_payments jsonb NOT NULL,
    client_payments_alt jsonb NOT NULL,
    overhead_pct_override double precision,
    overhead_override_comment text NOT NULL,
    in_kp boolean NOT NULL,
    is_default boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: warehouses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.warehouses (
    id character varying(36) NOT NULL,
    name character varying(255) NOT NULL,
    legal_entity_id character varying(36),
    kind character varying(16) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: warranty_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.warranty_claims (
    id character varying(36) NOT NULL,
    number character varying(32) NOT NULL,
    supplier_id character varying(36) NOT NULL,
    client_id character varying(36),
    machine_id character varying(36),
    deal_id character varying(36),
    request_id character varying(36),
    failure_on date,
    machine_hours double precision,
    description text NOT NULL,
    lines jsonb NOT NULL,
    photos jsonb NOT NULL,
    status character varying(16) NOT NULL,
    answer_due date,
    sent_at timestamp with time zone,
    answered_at timestamp with time zone,
    answer text NOT NULL,
    compensations jsonb NOT NULL,
    closed_at timestamp with time zone,
    close_reason text NOT NULL,
    sla_notified jsonb NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: integration_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_log ALTER COLUMN id SET DEFAULT nextval('public.integration_log_id_seq'::regclass);


--
-- Name: support_number_sequences year; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_number_sequences ALTER COLUMN year SET DEFAULT nextval('public.support_number_sequences_year_seq'::regclass);


--
-- Name: acceptance_units acceptance_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_units
    ADD CONSTRAINT acceptance_units_pkey PRIMARY KEY (id);


--
-- Name: access_review_campaigns access_review_campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_review_campaigns
    ADD CONSTRAINT access_review_campaigns_pkey PRIMARY KEY (id);


--
-- Name: access_review_items access_review_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_review_items
    ADD CONSTRAINT access_review_items_pkey PRIMARY KEY (id);


--
-- Name: access_rules access_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_rules
    ADD CONSTRAINT access_rules_pkey PRIMARY KEY (id);


--
-- Name: access_user_overrides access_user_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_user_overrides
    ADD CONSTRAINT access_user_overrides_pkey PRIMARY KEY (id);


--
-- Name: ai_conversations ai_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_pkey PRIMARY KEY (id);


--
-- Name: ai_feedback ai_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT ai_feedback_pkey PRIMARY KEY (id);


--
-- Name: ai_messages ai_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT ai_messages_pkey PRIMARY KEY (id);


--
-- Name: ai_tool_audits ai_tool_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_tool_audits
    ADD CONSTRAINT ai_tool_audits_pkey PRIMARY KEY (id);


--
-- Name: ai_usage_daily ai_usage_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_usage_daily
    ADD CONSTRAINT ai_usage_daily_pkey PRIMARY KEY (user_id, usage_date);


--
-- Name: app_errors app_errors_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_errors
    ADD CONSTRAINT app_errors_fingerprint_key UNIQUE (fingerprint);


--
-- Name: app_errors app_errors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_errors
    ADD CONSTRAINT app_errors_pkey PRIMARY KEY (id);


--
-- Name: app_markers app_markers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_markers
    ADD CONSTRAINT app_markers_pkey PRIMARY KEY (key);


--
-- Name: approvals approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (id);


--
-- Name: assignment_events assignment_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_pkey PRIMARY KEY (id);


--
-- Name: assignment_files assignment_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_files
    ADD CONSTRAINT assignment_files_pkey PRIMARY KEY (id);


--
-- Name: assignments assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_pkey PRIMARY KEY (id);


--
-- Name: attachment_scans attachment_scans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_scans
    ADD CONSTRAINT attachment_scans_pkey PRIMARY KEY (id);


--
-- Name: attachment_scans attachment_scans_quarantine_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_scans
    ADD CONSTRAINT attachment_scans_quarantine_key_key UNIQUE (quarantine_key);


--
-- Name: attachments attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: auth_sessions auth_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_sessions
    ADD CONSTRAINT auth_sessions_pkey PRIMARY KEY (id);


--
-- Name: auth_sessions auth_sessions_sid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_sessions
    ADD CONSTRAINT auth_sessions_sid_key UNIQUE (sid);


--
-- Name: backup_runs backup_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backup_runs
    ADD CONSTRAINT backup_runs_pkey PRIMARY KEY (id);


--
-- Name: backup_runs backup_runs_run_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backup_runs
    ADD CONSTRAINT backup_runs_run_key_key UNIQUE (run_key);


--
-- Name: bank_profiles bank_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_profiles
    ADD CONSTRAINT bank_profiles_pkey PRIMARY KEY (id);


--
-- Name: batches batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_pkey PRIMARY KEY (id);


--
-- Name: briefing_item_states briefing_item_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_item_states
    ADD CONSTRAINT briefing_item_states_pkey PRIMARY KEY (id);


--
-- Name: client_machines client_machines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_machines
    ADD CONSTRAINT client_machines_pkey PRIMARY KEY (id);


--
-- Name: client_price_history client_price_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_price_history
    ADD CONSTRAINT client_price_history_pkey PRIMARY KEY (id);


--
-- Name: clients clients_inn_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_inn_key UNIQUE (inn);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: competitor_offers competitor_offers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitor_offers
    ADD CONSTRAINT competitor_offers_pkey PRIMARY KEY (id);


--
-- Name: competitors competitors_name_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitors
    ADD CONSTRAINT competitors_name_key_key UNIQUE (name_key);


--
-- Name: competitors competitors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitors
    ADD CONSTRAINT competitors_pkey PRIMARY KEY (id);


--
-- Name: contract_claims contract_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_claims
    ADD CONSTRAINT contract_claims_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_pkey PRIMARY KEY (id);


--
-- Name: deal_feed deal_feed_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_feed
    ADD CONSTRAINT deal_feed_pkey PRIMARY KEY (id);


--
-- Name: deal_versions deal_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_versions
    ADD CONSTRAINT deal_versions_pkey PRIMARY KEY (id);


--
-- Name: deals deals_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_number_key UNIQUE (number);


--
-- Name: deals deals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_pkey PRIMARY KEY (id);


--
-- Name: equipment_executions equipment_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_executions
    ADD CONSTRAINT equipment_executions_pkey PRIMARY KEY (id);


--
-- Name: equipment_models equipment_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_models
    ADD CONSTRAINT equipment_models_pkey PRIMARY KEY (id);


--
-- Name: execution_stages execution_stages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.execution_stages
    ADD CONSTRAINT execution_stages_pkey PRIMARY KEY (id);


--
-- Name: factories factories_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factories
    ADD CONSTRAINT factories_name_key UNIQUE (name);


--
-- Name: factories factories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factories
    ADD CONSTRAINT factories_pkey PRIMARY KEY (id);


--
-- Name: factory_changes factory_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_changes
    ADD CONSTRAINT factory_changes_pkey PRIMARY KEY (id);


--
-- Name: factory_offers factory_offers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_offers
    ADD CONSTRAINT factory_offers_pkey PRIMARY KEY (id);


--
-- Name: factory_price_history factory_price_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_price_history
    ADD CONSTRAINT factory_price_history_pkey PRIMARY KEY (id);


--
-- Name: file_download_requests file_download_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_download_requests
    ADD CONSTRAINT file_download_requests_pkey PRIMARY KEY (id);


--
-- Name: finance_accounts finance_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_accounts
    ADD CONSTRAINT finance_accounts_pkey PRIMARY KEY (id);


--
-- Name: finance_allocations finance_allocations_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_allocations
    ADD CONSTRAINT finance_allocations_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_allocations finance_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_allocations
    ADD CONSTRAINT finance_allocations_pkey PRIMARY KEY (id);


--
-- Name: finance_balance_snapshots finance_balance_snapshots_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_balance_snapshots
    ADD CONSTRAINT finance_balance_snapshots_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_balance_snapshots finance_balance_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_balance_snapshots
    ADD CONSTRAINT finance_balance_snapshots_pkey PRIMARY KEY (id);


--
-- Name: finance_fx_operations finance_fx_operations_buy_transaction_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_buy_transaction_id_key UNIQUE (buy_transaction_id);


--
-- Name: finance_fx_operations finance_fx_operations_fee_transaction_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_fee_transaction_id_key UNIQUE (fee_transaction_id);


--
-- Name: finance_fx_operations finance_fx_operations_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_fx_operations finance_fx_operations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_pkey PRIMARY KEY (id);


--
-- Name: finance_fx_operations finance_fx_operations_sell_transaction_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_sell_transaction_id_key UNIQUE (sell_transaction_id);


--
-- Name: finance_import_batches finance_import_batches_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_import_batches
    ADD CONSTRAINT finance_import_batches_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_import_batches finance_import_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_import_batches
    ADD CONSTRAINT finance_import_batches_pkey PRIMARY KEY (id);


--
-- Name: finance_obligations finance_obligations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_obligations
    ADD CONSTRAINT finance_obligations_pkey PRIMARY KEY (id);


--
-- Name: finance_questions finance_questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_questions
    ADD CONSTRAINT finance_questions_pkey PRIMARY KEY (id);


--
-- Name: finance_recognition_events finance_recognition_events_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_recognition_events
    ADD CONSTRAINT finance_recognition_events_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_recognition_events finance_recognition_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_recognition_events
    ADD CONSTRAINT finance_recognition_events_pkey PRIMARY KEY (id);


--
-- Name: finance_transactions finance_transactions_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: finance_transactions finance_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_pkey PRIMARY KEY (id);


--
-- Name: fx_rates fx_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates
    ADD CONSTRAINT fx_rates_pkey PRIMARY KEY (id);


--
-- Name: glossary glossary_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary
    ADD CONSTRAINT glossary_pkey PRIMARY KEY (id);


--
-- Name: hs_codes hs_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hs_codes
    ADD CONSTRAINT hs_codes_pkey PRIMARY KEY (id);


--
-- Name: import_mappings import_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_mappings
    ADD CONSTRAINT import_mappings_pkey PRIMARY KEY (id);


--
-- Name: integration_log integration_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_log
    ADD CONSTRAINT integration_log_pkey PRIMARY KEY (id);


--
-- Name: job_runs job_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_runs
    ADD CONSTRAINT job_runs_pkey PRIMARY KEY (job);


--
-- Name: kp_revisions kp_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kp_revisions
    ADD CONSTRAINT kp_revisions_pkey PRIMARY KEY (id);


--
-- Name: legal_entities legal_entities_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_entities
    ADD CONSTRAINT legal_entities_code_key UNIQUE (code);


--
-- Name: legal_entities legal_entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_entities
    ADD CONSTRAINT legal_entities_pkey PRIMARY KEY (id);


--
-- Name: legal_packages legal_packages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_packages
    ADD CONSTRAINT legal_packages_pkey PRIMARY KEY (id);


--
-- Name: legal_profiles legal_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_profiles
    ADD CONSTRAINT legal_profiles_pkey PRIMARY KEY (id);


--
-- Name: legal_zip_sets legal_zip_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_zip_sets
    ADD CONSTRAINT legal_zip_sets_pkey PRIMARY KEY (id);


--
-- Name: local_options local_options_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_options
    ADD CONSTRAINT local_options_pkey PRIMARY KEY (id);


--
-- Name: logistics_history logistics_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logistics_history
    ADD CONSTRAINT logistics_history_pkey PRIMARY KEY (id);


--
-- Name: machine_hours machine_hours_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_hours
    ADD CONSTRAINT machine_hours_pkey PRIMARY KEY (id);


--
-- Name: machine_reminders machine_reminders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_reminders
    ADD CONSTRAINT machine_reminders_pkey PRIMARY KEY (id);


--
-- Name: machine_types machine_types_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_types
    ADD CONSTRAINT machine_types_name_key UNIQUE (name);


--
-- Name: machine_types machine_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_types
    ADD CONSTRAINT machine_types_pkey PRIMARY KEY (id);


--
-- Name: maintenance_kits maintenance_kits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_kits
    ADD CONSTRAINT maintenance_kits_pkey PRIMARY KEY (id);


--
-- Name: markup_policies markup_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markup_policies
    ADD CONSTRAINT markup_policies_pkey PRIMARY KEY (id);


--
-- Name: mfa_challenges mfa_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT mfa_challenges_pkey PRIMARY KEY (id);


--
-- Name: mfa_challenges mfa_challenges_verifier_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT mfa_challenges_verifier_hash_key UNIQUE (verifier_hash);


--
-- Name: mfa_credentials mfa_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_credentials
    ADD CONSTRAINT mfa_credentials_pkey PRIMARY KEY (id);


--
-- Name: mfa_credentials mfa_credentials_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_credentials
    ADD CONSTRAINT mfa_credentials_user_id_key UNIQUE (user_id);


--
-- Name: mfa_recovery_codes mfa_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_pkey PRIMARY KEY (id);


--
-- Name: model_documents model_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_documents
    ADD CONSTRAINT model_documents_pkey PRIMARY KEY (id);


--
-- Name: negotiation_steps negotiation_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_steps
    ADD CONSTRAINT negotiation_steps_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: offer_prices offer_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_prices
    ADD CONSTRAINT offer_prices_pkey PRIMARY KEY (id);


--
-- Name: office_counters office_counters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_counters
    ADD CONSTRAINT office_counters_pkey PRIMARY KEY (id);


--
-- Name: office_docs office_docs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_docs
    ADD CONSTRAINT office_docs_pkey PRIMARY KEY (id);


--
-- Name: office_routes office_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_routes
    ADD CONSTRAINT office_routes_pkey PRIMARY KEY (id);


--
-- Name: office_signers office_signers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_signers
    ADD CONSTRAINT office_signers_pkey PRIMARY KEY (id);


--
-- Name: org_directions org_directions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_directions
    ADD CONSTRAINT org_directions_pkey PRIMARY KEY (key);


--
-- Name: org_members org_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_members
    ADD CONSTRAINT org_members_pkey PRIMARY KEY (user_id);


--
-- Name: other_machine_models other_machine_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.other_machine_models
    ADD CONSTRAINT other_machine_models_pkey PRIMARY KEY (id);


--
-- Name: part_applicability part_applicability_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_applicability
    ADD CONSTRAINT part_applicability_pkey PRIMARY KEY (id);


--
-- Name: part_batches part_batches_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_batches
    ADD CONSTRAINT part_batches_number_key UNIQUE (number);


--
-- Name: part_batches part_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_batches
    ADD CONSTRAINT part_batches_pkey PRIMARY KEY (id);


--
-- Name: part_client_prices part_client_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_client_prices
    ADD CONSTRAINT part_client_prices_pkey PRIMARY KEY (id);


--
-- Name: part_cross_refs part_cross_refs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_cross_refs
    ADD CONSTRAINT part_cross_refs_pkey PRIMARY KEY (id);


--
-- Name: part_deviations part_deviations_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_deviations
    ADD CONSTRAINT part_deviations_number_key UNIQUE (number);


--
-- Name: part_deviations part_deviations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_deviations
    ADD CONSTRAINT part_deviations_pkey PRIMARY KEY (id);


--
-- Name: part_inventories part_inventories_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventories
    ADD CONSTRAINT part_inventories_number_key UNIQUE (number);


--
-- Name: part_inventories part_inventories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventories
    ADD CONSTRAINT part_inventories_pkey PRIMARY KEY (id);


--
-- Name: part_inventory_lines part_inventory_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventory_lines
    ADD CONSTRAINT part_inventory_lines_pkey PRIMARY KEY (id);


--
-- Name: part_kps part_kps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_kps
    ADD CONSTRAINT part_kps_pkey PRIMARY KEY (id);


--
-- Name: part_receipt_lines part_receipt_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipt_lines
    ADD CONSTRAINT part_receipt_lines_pkey PRIMARY KEY (id);


--
-- Name: part_receipts part_receipts_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipts
    ADD CONSTRAINT part_receipts_number_key UNIQUE (number);


--
-- Name: part_receipts part_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipts
    ADD CONSTRAINT part_receipts_pkey PRIMARY KEY (id);


--
-- Name: part_request_lines part_request_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_request_lines
    ADD CONSTRAINT part_request_lines_pkey PRIMARY KEY (id);


--
-- Name: part_requests part_requests_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_requests
    ADD CONSTRAINT part_requests_number_key UNIQUE (number);


--
-- Name: part_requests part_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_requests
    ADD CONSTRAINT part_requests_pkey PRIMARY KEY (id);


--
-- Name: part_reservations part_reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_reservations
    ADD CONSTRAINT part_reservations_pkey PRIMARY KEY (id);


--
-- Name: part_shipment_lines part_shipment_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipment_lines
    ADD CONSTRAINT part_shipment_lines_pkey PRIMARY KEY (id);


--
-- Name: part_shipments part_shipments_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipments
    ADD CONSTRAINT part_shipments_number_key UNIQUE (number);


--
-- Name: part_shipments part_shipments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipments
    ADD CONSTRAINT part_shipments_pkey PRIMARY KEY (id);


--
-- Name: part_stock_moves part_stock_moves_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock_moves
    ADD CONSTRAINT part_stock_moves_pkey PRIMARY KEY (id);


--
-- Name: part_stock part_stock_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock
    ADD CONSTRAINT part_stock_pkey PRIMARY KEY (id);


--
-- Name: parts parts_ms_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_ms_id_key UNIQUE (ms_id);


--
-- Name: parts parts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_pkey PRIMARY KEY (id);


--
-- Name: password_history password_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_history
    ADD CONSTRAINT password_history_pkey PRIMARY KEY (id);


--
-- Name: password_links password_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_links
    ADD CONSTRAINT password_links_pkey PRIMARY KEY (id);


--
-- Name: password_links password_links_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_links
    ADD CONSTRAINT password_links_token_hash_key UNIQUE (token_hash);


--
-- Name: payment_schemes payment_schemes_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_schemes
    ADD CONSTRAINT payment_schemes_name_key UNIQUE (name);


--
-- Name: payment_schemes payment_schemes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_schemes
    ADD CONSTRAINT payment_schemes_pkey PRIMARY KEY (id);


--
-- Name: plan_fact_entries plan_fact_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_fact_entries
    ADD CONSTRAINT plan_fact_entries_pkey PRIMARY KEY (id);


--
-- Name: po_lines po_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.po_lines
    ADD CONSTRAINT po_lines_pkey PRIMARY KEY (id);


--
-- Name: position_images position_images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_images
    ADD CONSTRAINT position_images_pkey PRIMARY KEY (id);


--
-- Name: position_tech_params position_tech_params_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_tech_params
    ADD CONSTRAINT position_tech_params_pkey PRIMARY KEY (id);


--
-- Name: positions positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_pkey PRIMARY KEY (id);


--
-- Name: powers_of_attorney powers_of_attorney_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.powers_of_attorney
    ADD CONSTRAINT powers_of_attorney_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_number_key UNIQUE (number);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: quote_links quote_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quote_links
    ADD CONSTRAINT quote_links_pkey PRIMARY KEY (id);


--
-- Name: rate_settings rate_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_settings
    ADD CONSTRAINT rate_settings_pkey PRIMARY KEY (id);


--
-- Name: rfq_imports rfq_imports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_imports
    ADD CONSTRAINT rfq_imports_pkey PRIMARY KEY (id);


--
-- Name: route_steps route_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_steps
    ADD CONSTRAINT route_steps_pkey PRIMARY KEY (id);


--
-- Name: security_alerts security_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_alerts
    ADD CONSTRAINT security_alerts_pkey PRIMARY KEY (id);


--
-- Name: security_events security_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_pkey PRIMARY KEY (id);


--
-- Name: signal_log signal_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_log
    ADD CONSTRAINT signal_log_pkey PRIMARY KEY (key);


--
-- Name: supplier_rfq_lines supplier_rfq_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfq_lines
    ADD CONSTRAINT supplier_rfq_lines_pkey PRIMARY KEY (id);


--
-- Name: supplier_rfqs supplier_rfqs_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfqs
    ADD CONSTRAINT supplier_rfqs_number_key UNIQUE (number);


--
-- Name: supplier_rfqs supplier_rfqs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfqs
    ADD CONSTRAINT supplier_rfqs_pkey PRIMARY KEY (id);


--
-- Name: supply_routes supply_routes_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supply_routes
    ADD CONSTRAINT supply_routes_name_key UNIQUE (name);


--
-- Name: supply_routes supply_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supply_routes
    ADD CONSTRAINT supply_routes_pkey PRIMARY KEY (id);


--
-- Name: support_attachments support_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_attachments
    ADD CONSTRAINT support_attachments_pkey PRIMARY KEY (id);


--
-- Name: support_messages support_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_messages
    ADD CONSTRAINT support_messages_pkey PRIMARY KEY (id);


--
-- Name: support_number_sequences support_number_sequences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_number_sequences
    ADD CONSTRAINT support_number_sequences_pkey PRIMARY KEY (year);


--
-- Name: support_tickets support_tickets_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_number_key UNIQUE (number);


--
-- Name: support_tickets support_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_pkey PRIMARY KEY (id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: tech_param_templates tech_param_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tech_param_templates
    ADD CONSTRAINT tech_param_templates_pkey PRIMARY KEY (id);


--
-- Name: templates templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_pkey PRIMARY KEY (id);


--
-- Name: acceptance_units uq_acceptance_unit; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_units
    ADD CONSTRAINT uq_acceptance_unit UNIQUE (position_id, unit_no);


--
-- Name: access_user_overrides uq_access_override; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_user_overrides
    ADD CONSTRAINT uq_access_override UNIQUE (user_id, field_group);


--
-- Name: access_review_items uq_access_review_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_review_items
    ADD CONSTRAINT uq_access_review_item UNIQUE (campaign_id, user_id);


--
-- Name: access_rules uq_access_rule; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_rules
    ADD CONSTRAINT uq_access_rule UNIQUE (role, field_group);


--
-- Name: ai_feedback uq_ai_feedback; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT uq_ai_feedback UNIQUE (message_id, user_id);


--
-- Name: ai_messages uq_ai_message_request; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT uq_ai_message_request UNIQUE (conversation_id, client_request_id);


--
-- Name: briefing_item_states uq_briefing_state_user_item; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_item_states
    ADD CONSTRAINT uq_briefing_state_user_item UNIQUE (user_id, item_key);


--
-- Name: deal_versions uq_deal_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_versions
    ADD CONSTRAINT uq_deal_version UNIQUE (deal_id, version_no);


--
-- Name: equipment_models uq_factory_model; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_models
    ADD CONSTRAINT uq_factory_model UNIQUE (factory_id, model);


--
-- Name: finance_obligations uq_fin_obligation_source; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_obligations
    ADD CONSTRAINT uq_fin_obligation_source UNIQUE (source_type, source_id, source_line_key);


--
-- Name: finance_recognition_events uq_fin_recognition_source; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_recognition_events
    ADD CONSTRAINT uq_fin_recognition_source UNIQUE (source_type, source_id);


--
-- Name: fx_rates uq_fx_day; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates
    ADD CONSTRAINT uq_fx_day UNIQUE ("on", currency, source);


--
-- Name: glossary uq_glossary_term; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary
    ADD CONSTRAINT uq_glossary_term UNIQUE (term_ru, kind);


--
-- Name: legal_zip_sets uq_legal_zip_set; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_zip_sets
    ADD CONSTRAINT uq_legal_zip_set UNIQUE (deal_id, factory_id);


--
-- Name: machine_reminders uq_machine_reminder; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_reminders
    ADD CONSTRAINT uq_machine_reminder UNIQUE (machine_id, to_hours);


--
-- Name: office_counters uq_office_counter; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_counters
    ADD CONSTRAINT uq_office_counter UNIQUE (entity_id, journal, year);


--
-- Name: other_machine_models uq_other_machine_model; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.other_machine_models
    ADD CONSTRAINT uq_other_machine_model UNIQUE (manufacturer, model);


--
-- Name: part_stock uq_part_stock; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock
    ADD CONSTRAINT uq_part_stock UNIQUE (part_id, warehouse_id);


--
-- Name: password_history uq_password_history; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_history
    ADD CONSTRAINT uq_password_history UNIQUE (user_id, password_hash);


--
-- Name: support_tickets uq_support_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT uq_support_idempotency UNIQUE (reporter_id, idempotency_key);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_external_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_external_id_key UNIQUE (external_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: variants variants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.variants
    ADD CONSTRAINT variants_pkey PRIMARY KEY (id);


--
-- Name: warehouses warehouses_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warehouses
    ADD CONSTRAINT warehouses_name_key UNIQUE (name);


--
-- Name: warehouses warehouses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warehouses
    ADD CONSTRAINT warehouses_pkey PRIMARY KEY (id);


--
-- Name: warranty_claims warranty_claims_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_number_key UNIQUE (number);


--
-- Name: warranty_claims warranty_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_pkey PRIMARY KEY (id);


--
-- Name: ix_acceptance_units_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_acceptance_units_deal_id ON public.acceptance_units USING btree (deal_id);


--
-- Name: ix_acceptance_units_position_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_acceptance_units_position_id ON public.acceptance_units USING btree (position_id);


--
-- Name: ix_access_review_campaigns_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_review_campaigns_due_date ON public.access_review_campaigns USING btree (due_date);


--
-- Name: ix_access_review_items_campaign_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_review_items_campaign_id ON public.access_review_items USING btree (campaign_id);


--
-- Name: ix_access_review_items_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_review_items_user_id ON public.access_review_items USING btree (user_id);


--
-- Name: ix_access_rules_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_rules_role ON public.access_rules USING btree (role);


--
-- Name: ix_access_user_overrides_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_access_user_overrides_user_id ON public.access_user_overrides USING btree (user_id);


--
-- Name: ix_ai_conversations_owner_last; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_conversations_owner_last ON public.ai_conversations USING btree (owner_id, last_message_at);


--
-- Name: ix_ai_messages_conversation_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_messages_conversation_created ON public.ai_messages USING btree (conversation_id, created_at);


--
-- Name: ix_ai_tool_audits_actor_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_tool_audits_actor_user_id ON public.ai_tool_audits USING btree (actor_user_id);


--
-- Name: ix_ai_tool_audits_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_tool_audits_conversation_id ON public.ai_tool_audits USING btree (conversation_id);


--
-- Name: ix_app_errors_fingerprint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_errors_fingerprint ON public.app_errors USING btree (fingerprint);


--
-- Name: ix_app_errors_last_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_errors_last_seen ON public.app_errors USING btree (last_seen);


--
-- Name: ix_approvals_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_deal_id ON public.approvals USING btree (deal_id);


--
-- Name: ix_assignment_events_assignment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_events_assignment_id ON public.assignment_events USING btree (assignment_id);


--
-- Name: ix_assignment_files_assignment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_files_assignment_id ON public.assignment_files USING btree (assignment_id);


--
-- Name: ix_assignments_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_assignee_id ON public.assignments USING btree (assignee_id);


--
-- Name: ix_assignments_author_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_author_id ON public.assignments USING btree (author_id);


--
-- Name: ix_assignments_head_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_head_id ON public.assignments USING btree (head_id);


--
-- Name: ix_assignments_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_number ON public.assignments USING btree (number);


--
-- Name: ix_assignments_status_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_status_due ON public.assignments USING btree (status, due_on);


--
-- Name: ix_attachment_scans_attachment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachment_scans_attachment_id ON public.attachment_scans USING btree (attachment_id);


--
-- Name: ix_attachment_scans_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachment_scans_sha256 ON public.attachment_scans USING btree (sha256);


--
-- Name: ix_attachment_scans_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachment_scans_status_created ON public.attachment_scans USING btree (status, created_at);


--
-- Name: ix_attachments_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachments_deal_id ON public.attachments USING btree (deal_id);


--
-- Name: ix_audit_log_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_at ON public.audit_log USING btree (at);


--
-- Name: ix_audit_log_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_deal_id ON public.audit_log USING btree (deal_id);


--
-- Name: ix_audit_log_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_user_id ON public.audit_log USING btree (user_id);


--
-- Name: ix_auth_sessions_last_seen_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auth_sessions_last_seen_at ON public.auth_sessions USING btree (last_seen_at);


--
-- Name: ix_auth_sessions_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auth_sessions_user_active ON public.auth_sessions USING btree (user_id, revoked_at, absolute_expires_at);


--
-- Name: ix_auth_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auth_sessions_user_id ON public.auth_sessions USING btree (user_id);


--
-- Name: ix_backup_runs_finished_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_backup_runs_finished_at ON public.backup_runs USING btree (finished_at);


--
-- Name: ix_backup_runs_status_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_backup_runs_status_started ON public.backup_runs USING btree (status, started_at);


--
-- Name: ix_bank_profiles_profile_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_profiles_profile_id ON public.bank_profiles USING btree (profile_id);


--
-- Name: ix_batches_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_batches_deal_id ON public.batches USING btree (deal_id);


--
-- Name: ix_briefing_item_states_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_briefing_item_states_user_id ON public.briefing_item_states USING btree (user_id);


--
-- Name: ix_client_machines_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_client_machines_client_id ON public.client_machines USING btree (client_id);


--
-- Name: ix_client_machines_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_client_machines_deal_id ON public.client_machines USING btree (deal_id);


--
-- Name: ix_client_price_history_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_client_price_history_client_id ON public.client_price_history USING btree (client_id);


--
-- Name: ix_client_price_history_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_client_price_history_deal_id ON public.client_price_history USING btree (deal_id);


--
-- Name: ix_client_price_history_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_client_price_history_on ON public.client_price_history USING btree ("on");


--
-- Name: ix_competitor_offers_competitor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_competitor_offers_competitor_id ON public.competitor_offers USING btree (competitor_id);


--
-- Name: ix_competitor_offers_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_competitor_offers_deal_id ON public.competitor_offers USING btree (deal_id);


--
-- Name: ix_contract_claims_answer_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contract_claims_answer_due ON public.contract_claims USING btree (answer_due);


--
-- Name: ix_contract_claims_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contract_claims_contract_id ON public.contract_claims USING btree (contract_id);


--
-- Name: ix_contract_claims_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contract_claims_deal_id ON public.contract_claims USING btree (deal_id);


--
-- Name: ix_contract_claims_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contract_claims_number ON public.contract_claims USING btree (number);


--
-- Name: ix_contracts_cp_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_cp_id ON public.contracts USING btree (cp_id);


--
-- Name: ix_contracts_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_deal_id ON public.contracts USING btree (deal_id);


--
-- Name: ix_contracts_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_number ON public.contracts USING btree (number);


--
-- Name: ix_contracts_own_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_own_entity_id ON public.contracts USING btree (own_entity_id);


--
-- Name: ix_contracts_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_parent_id ON public.contracts USING btree (parent_id);


--
-- Name: ix_contracts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contracts_status ON public.contracts USING btree (status);


--
-- Name: ix_deal_feed_awaiting; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deal_feed_awaiting ON public.deal_feed USING btree (deal_id) WHERE (awaiting AND (resolved_at IS NULL) AND (deleted_at IS NULL));


--
-- Name: ix_deal_feed_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deal_feed_deal_id ON public.deal_feed USING btree (deal_id);


--
-- Name: ix_deal_feed_external_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deal_feed_external_id ON public.deal_feed USING btree (external_id);


--
-- Name: ix_deal_feed_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deal_feed_fts ON public.deal_feed USING gin (to_tsvector('russian'::regconfig, (((COALESCE(subject, ''::character varying))::text || ' '::text) || COALESCE(body, ''::text))));


--
-- Name: ix_deal_feed_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deal_feed_parent_id ON public.deal_feed USING btree (parent_id);


--
-- Name: ix_deal_versions_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deal_versions_deal_id ON public.deal_versions USING btree (deal_id);


--
-- Name: ix_deals_archived_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_archived_at ON public.deals USING btree (archived_at);


--
-- Name: ix_deals_deleted_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_deleted_at ON public.deals USING btree (deleted_at);


--
-- Name: ix_deals_manager_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_manager_id ON public.deals USING btree (manager_id);


--
-- Name: ix_deals_parent_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_parent_deal_id ON public.deals USING btree (parent_deal_id);


--
-- Name: ix_deals_request_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_request_group_id ON public.deals USING btree (request_group_id);


--
-- Name: ix_deals_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_status ON public.deals USING btree (status);


--
-- Name: ix_equipment_executions_fingerprint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equipment_executions_fingerprint ON public.equipment_executions USING btree (fingerprint);


--
-- Name: ix_equipment_executions_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equipment_executions_model_id ON public.equipment_executions USING btree (model_id);


--
-- Name: ix_execution_stages_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_execution_stages_deal_id ON public.execution_stages USING btree (deal_id);


--
-- Name: ix_factory_changes_factory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_factory_changes_factory_id ON public.factory_changes USING btree (factory_id);


--
-- Name: ix_factory_offers_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_factory_offers_deal_id ON public.factory_offers USING btree (deal_id);


--
-- Name: ix_factory_offers_mirror_of_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_factory_offers_mirror_of_id ON public.factory_offers USING btree (mirror_of_id);


--
-- Name: ix_factory_price_history_factory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_factory_price_history_factory_id ON public.factory_price_history USING btree (factory_id);


--
-- Name: ix_factory_price_history_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_factory_price_history_model ON public.factory_price_history USING btree (model);


--
-- Name: ix_file_download_requests_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_download_requests_deal_id ON public.file_download_requests USING btree (deal_id);


--
-- Name: ix_file_download_requests_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_download_requests_user_id ON public.file_download_requests USING btree (user_id);


--
-- Name: ix_fin_alloc_obligation_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_alloc_obligation_status ON public.finance_allocations USING btree (obligation_id, status);


--
-- Name: ix_fin_alloc_tx_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_alloc_tx_status ON public.finance_allocations USING btree (transaction_id, status);


--
-- Name: ix_fin_obl_deal_forecast; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_obl_deal_forecast ON public.finance_obligations USING btree (deal_id, forecast_on);


--
-- Name: ix_fin_obl_entity_dir; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_obl_entity_dir ON public.finance_obligations USING btree (legal_entity_id, direction);


--
-- Name: ix_fin_question_assignee_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_question_assignee_status ON public.finance_questions USING btree (assignee_id, status);


--
-- Name: ix_fin_question_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_question_subject ON public.finance_questions USING btree (subject_type, subject_id);


--
-- Name: ix_fin_recognition_deal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_recognition_deal ON public.finance_recognition_events USING btree (deal_id, kind, recognized_on);


--
-- Name: ix_fin_snapshot_account_asof; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_snapshot_account_asof ON public.finance_balance_snapshots USING btree (account_id, as_of);


--
-- Name: ix_fin_tx_account_booked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fin_tx_account_booked ON public.finance_transactions USING btree (account_id, booked_at);


--
-- Name: ix_finance_accounts_bank_profile_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_accounts_bank_profile_id ON public.finance_accounts USING btree (bank_profile_id);


--
-- Name: ix_finance_accounts_legal_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_accounts_legal_entity_id ON public.finance_accounts USING btree (legal_entity_id);


--
-- Name: ix_finance_fx_operations_buy_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_fx_operations_buy_account_id ON public.finance_fx_operations USING btree (buy_account_id);


--
-- Name: ix_finance_fx_operations_sell_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_fx_operations_sell_account_id ON public.finance_fx_operations USING btree (sell_account_id);


--
-- Name: ix_finance_import_batches_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_import_batches_account_id ON public.finance_import_batches USING btree (account_id);


--
-- Name: ix_finance_import_batches_file_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_import_batches_file_sha256 ON public.finance_import_batches USING btree (file_sha256);


--
-- Name: ix_finance_obligations_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_contract_id ON public.finance_obligations USING btree (contract_id);


--
-- Name: ix_finance_obligations_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_deal_id ON public.finance_obligations USING btree (deal_id);


--
-- Name: ix_finance_obligations_forecast_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_forecast_on ON public.finance_obligations USING btree (forecast_on);


--
-- Name: ix_finance_obligations_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_is_active ON public.finance_obligations USING btree (is_active);


--
-- Name: ix_finance_obligations_legal_due_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_legal_due_on ON public.finance_obligations USING btree (legal_due_on);


--
-- Name: ix_finance_obligations_legal_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_legal_entity_id ON public.finance_obligations USING btree (legal_entity_id);


--
-- Name: ix_finance_obligations_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_obligations_source_id ON public.finance_obligations USING btree (source_id);


--
-- Name: ix_finance_questions_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_questions_deal_id ON public.finance_questions USING btree (deal_id);


--
-- Name: ix_finance_recognition_events_cost_item_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_recognition_events_cost_item_code ON public.finance_recognition_events USING btree (cost_item_code);


--
-- Name: ix_finance_recognition_events_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_recognition_events_deal_id ON public.finance_recognition_events USING btree (deal_id);


--
-- Name: ix_finance_transactions_external_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_transactions_external_ref ON public.finance_transactions USING btree (external_ref);


--
-- Name: ix_finance_transactions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finance_transactions_status ON public.finance_transactions USING btree (status);


--
-- Name: ix_fx_rates_cur_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fx_rates_cur_on ON public.fx_rates USING btree (currency, "on" DESC);


--
-- Name: ix_fx_rates_currency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fx_rates_currency ON public.fx_rates USING btree (currency);


--
-- Name: ix_fx_rates_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fx_rates_on ON public.fx_rates USING btree ("on");


--
-- Name: ix_glossary_term_ru; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_term_ru ON public.glossary USING btree (term_ru);


--
-- Name: ix_glossary_term_zh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_term_zh ON public.glossary USING btree (term_zh);


--
-- Name: ix_hs_codes_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hs_codes_code ON public.hs_codes USING btree (code);


--
-- Name: ix_import_mappings_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_import_mappings_scope ON public.import_mappings USING btree (scope);


--
-- Name: ix_integration_log_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integration_log_at ON public.integration_log USING btree (at);


--
-- Name: ix_integration_log_system; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integration_log_system ON public.integration_log USING btree (system);


--
-- Name: ix_kp_revisions_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kp_revisions_deal_id ON public.kp_revisions USING btree (deal_id);


--
-- Name: ix_legal_packages_counterparty_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_legal_packages_counterparty_id ON public.legal_packages USING btree (counterparty_id);


--
-- Name: ix_legal_packages_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_legal_packages_deal_id ON public.legal_packages USING btree (deal_id);


--
-- Name: ix_legal_profiles_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_legal_profiles_owner_id ON public.legal_profiles USING btree (owner_id);


--
-- Name: ix_legal_profiles_owner_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_legal_profiles_owner_kind ON public.legal_profiles USING btree (owner_kind);


--
-- Name: ix_legal_zip_sets_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_legal_zip_sets_deal_id ON public.legal_zip_sets USING btree (deal_id);


--
-- Name: ix_legal_zip_sets_factory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_legal_zip_sets_factory_id ON public.legal_zip_sets USING btree (factory_id);


--
-- Name: ix_local_options_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_local_options_deal_id ON public.local_options USING btree (deal_id);


--
-- Name: ix_local_options_position_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_local_options_position_id ON public.local_options USING btree (position_id);


--
-- Name: ix_logistics_history_item_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logistics_history_item_code ON public.logistics_history USING btree (item_code);


--
-- Name: ix_logistics_history_item_route; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logistics_history_item_route ON public.logistics_history USING btree (item_code, route, "on" DESC);


--
-- Name: ix_machine_hours_machine_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_machine_hours_machine_id ON public.machine_hours USING btree (machine_id);


--
-- Name: ix_machine_reminders_machine_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_machine_reminders_machine_id ON public.machine_reminders USING btree (machine_id);


--
-- Name: ix_maintenance_kits_equipment_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_maintenance_kits_equipment_model_id ON public.maintenance_kits USING btree (equipment_model_id);


--
-- Name: ix_maintenance_kits_other_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_maintenance_kits_other_model_id ON public.maintenance_kits USING btree (other_model_id);


--
-- Name: ix_mfa_challenges_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mfa_challenges_user_id ON public.mfa_challenges USING btree (user_id);


--
-- Name: ix_mfa_recovery_codes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mfa_recovery_codes_user_id ON public.mfa_recovery_codes USING btree (user_id);


--
-- Name: ix_model_documents_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_model_documents_model_id ON public.model_documents USING btree (model_id);


--
-- Name: ix_negotiation_steps_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_negotiation_steps_deal_id ON public.negotiation_steps USING btree (deal_id);


--
-- Name: ix_notifications_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_deal_id ON public.notifications USING btree (deal_id);


--
-- Name: ix_notifications_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_id ON public.notifications USING btree (user_id);


--
-- Name: ix_offer_prices_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_offer_prices_created_at ON public.offer_prices USING btree (created_at);


--
-- Name: ix_offer_prices_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_offer_prices_deal_id ON public.offer_prices USING btree (deal_id);


--
-- Name: ix_offer_prices_variant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_offer_prices_variant_id ON public.offer_prices USING btree (variant_id);


--
-- Name: ix_office_docs_claim_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_docs_claim_id ON public.office_docs USING btree (claim_id);


--
-- Name: ix_office_docs_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_docs_contract_id ON public.office_docs USING btree (contract_id);


--
-- Name: ix_office_docs_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_docs_entity_id ON public.office_docs USING btree (entity_id);


--
-- Name: ix_office_docs_kind_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_docs_kind_status ON public.office_docs USING btree (kind, status);


--
-- Name: ix_office_docs_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_docs_number ON public.office_docs USING btree (number);


--
-- Name: ix_office_docs_reply_to_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_docs_reply_to_id ON public.office_docs USING btree (reply_to_id);


--
-- Name: ix_office_signers_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_office_signers_entity_id ON public.office_signers USING btree (entity_id);


--
-- Name: ix_part_applicability_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_applicability_part_id ON public.part_applicability USING btree (part_id);


--
-- Name: ix_part_client_prices_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_client_prices_part_id ON public.part_client_prices USING btree (part_id);


--
-- Name: ix_part_cross_refs_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_cross_refs_number ON public.part_cross_refs USING btree (number);


--
-- Name: ix_part_cross_refs_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_cross_refs_part_id ON public.part_cross_refs USING btree (part_id);


--
-- Name: ix_part_deviations_po_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_deviations_po_id ON public.part_deviations USING btree (po_id);


--
-- Name: ix_part_deviations_po_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_deviations_po_line_id ON public.part_deviations USING btree (po_line_id);


--
-- Name: ix_part_inventory_lines_inventory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_inventory_lines_inventory_id ON public.part_inventory_lines USING btree (inventory_id);


--
-- Name: ix_part_kps_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_kps_request_id ON public.part_kps USING btree (request_id);


--
-- Name: ix_part_receipt_lines_po_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_receipt_lines_po_line_id ON public.part_receipt_lines USING btree (po_line_id);


--
-- Name: ix_part_receipt_lines_receipt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_receipt_lines_receipt_id ON public.part_receipt_lines USING btree (receipt_id);


--
-- Name: ix_part_receipts_po_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_receipts_po_id ON public.part_receipts USING btree (po_id);


--
-- Name: ix_part_request_lines_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_request_lines_request_id ON public.part_request_lines USING btree (request_id);


--
-- Name: ix_part_requests_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_requests_client_id ON public.part_requests USING btree (client_id);


--
-- Name: ix_part_requests_machine_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_requests_machine_id ON public.part_requests USING btree (machine_id);


--
-- Name: ix_part_reservations_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_reservations_line_id ON public.part_reservations USING btree (line_id);


--
-- Name: ix_part_reservations_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_reservations_part_id ON public.part_reservations USING btree (part_id);


--
-- Name: ix_part_reservations_warehouse_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_reservations_warehouse_id ON public.part_reservations USING btree (warehouse_id);


--
-- Name: ix_part_shipment_lines_request_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_shipment_lines_request_line_id ON public.part_shipment_lines USING btree (request_line_id);


--
-- Name: ix_part_shipment_lines_shipment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_shipment_lines_shipment_id ON public.part_shipment_lines USING btree (shipment_id);


--
-- Name: ix_part_shipments_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_shipments_request_id ON public.part_shipments USING btree (request_id);


--
-- Name: ix_part_stock_moves_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_stock_moves_at ON public.part_stock_moves USING btree (at);


--
-- Name: ix_part_stock_moves_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_stock_moves_part_id ON public.part_stock_moves USING btree (part_id);


--
-- Name: ix_part_stock_moves_warehouse_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_stock_moves_warehouse_id ON public.part_stock_moves USING btree (warehouse_id);


--
-- Name: ix_part_stock_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_stock_part_id ON public.part_stock USING btree (part_id);


--
-- Name: ix_part_stock_warehouse_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_part_stock_warehouse_id ON public.part_stock USING btree (warehouse_id);


--
-- Name: ix_parts_article; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parts_article ON public.parts USING btree (article);


--
-- Name: ix_parts_oem_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parts_oem_number ON public.parts USING btree (oem_number);


--
-- Name: ix_parts_part_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parts_part_group ON public.parts USING btree (part_group);


--
-- Name: ix_password_history_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_password_history_user_id ON public.password_history USING btree (user_id);


--
-- Name: ix_password_links_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_password_links_user_id ON public.password_links USING btree (user_id);


--
-- Name: ix_plan_fact_entries_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plan_fact_entries_deal_id ON public.plan_fact_entries USING btree (deal_id);


--
-- Name: ix_po_lines_batch_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_po_lines_batch_id ON public.po_lines USING btree (batch_id);


--
-- Name: ix_po_lines_po_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_po_lines_po_id ON public.po_lines USING btree (po_id);


--
-- Name: ix_po_lines_request_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_po_lines_request_line_id ON public.po_lines USING btree (request_line_id);


--
-- Name: ix_position_images_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_position_images_deal_id ON public.position_images USING btree (deal_id);


--
-- Name: ix_position_images_position_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_position_images_position_id ON public.position_images USING btree (position_id);


--
-- Name: ix_position_tech_params_position_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_position_tech_params_position_id ON public.position_tech_params USING btree (position_id);


--
-- Name: ix_positions_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_positions_deal_id ON public.positions USING btree (deal_id);


--
-- Name: ix_powers_of_attorney_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_powers_of_attorney_entity_id ON public.powers_of_attorney USING btree (entity_id);


--
-- Name: ix_powers_of_attorney_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_powers_of_attorney_status ON public.powers_of_attorney USING btree (status);


--
-- Name: ix_price_history_factory_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_price_history_factory_model ON public.factory_price_history USING btree (factory_id, model, "on" DESC);


--
-- Name: ix_purchase_orders_parent_po_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_orders_parent_po_id ON public.purchase_orders USING btree (parent_po_id);


--
-- Name: ix_purchase_orders_supplier_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_orders_supplier_id ON public.purchase_orders USING btree (supplier_id);


--
-- Name: ix_quote_links_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quote_links_deal_id ON public.quote_links USING btree (deal_id);


--
-- Name: ix_quote_links_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_quote_links_token_hash ON public.quote_links USING btree (token_hash);


--
-- Name: ix_rate_settings_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rate_settings_key ON public.rate_settings USING btree (key);


--
-- Name: ix_rfq_imports_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rfq_imports_deal_id ON public.rfq_imports USING btree (deal_id);


--
-- Name: ix_route_steps_route_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_route_steps_route_id ON public.route_steps USING btree (route_id);


--
-- Name: ix_security_alerts_status_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_alerts_status_seen ON public.security_alerts USING btree (status, last_seen_at);


--
-- Name: ix_security_alerts_subject_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_alerts_subject_user_id ON public.security_alerts USING btree (subject_user_id);


--
-- Name: ix_security_events_actor_type_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_events_actor_type_at ON public.security_events USING btree (actor_user_id, event_type, occurred_at);


--
-- Name: ix_security_events_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_events_deal_id ON public.security_events USING btree (deal_id);


--
-- Name: ix_security_events_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_events_occurred_at ON public.security_events USING btree (occurred_at);


--
-- Name: ix_security_events_subject_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_events_subject_at ON public.security_events USING btree (subject_user_id, occurred_at);


--
-- Name: ix_signal_log_resolved_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_log_resolved_at ON public.signal_log USING btree (resolved_at);


--
-- Name: ix_supplier_rfq_lines_request_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supplier_rfq_lines_request_line_id ON public.supplier_rfq_lines USING btree (request_line_id);


--
-- Name: ix_supplier_rfq_lines_rfq_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supplier_rfq_lines_rfq_id ON public.supplier_rfq_lines USING btree (rfq_id);


--
-- Name: ix_supplier_rfqs_mirror_of_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supplier_rfqs_mirror_of_id ON public.supplier_rfqs USING btree (mirror_of_id);


--
-- Name: ix_supplier_rfqs_supplier_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supplier_rfqs_supplier_id ON public.supplier_rfqs USING btree (supplier_id);


--
-- Name: ix_support_assignee_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_support_assignee_status ON public.support_tickets USING btree (assignee_id, status);


--
-- Name: ix_support_attachments_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_support_attachments_ticket_id ON public.support_attachments USING btree (ticket_id);


--
-- Name: ix_support_messages_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_support_messages_ticket_id ON public.support_messages USING btree (ticket_id);


--
-- Name: ix_support_reporter_activity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_support_reporter_activity ON public.support_tickets USING btree (reporter_id, last_activity_at);


--
-- Name: ix_support_status_priority_activity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_support_status_priority_activity ON public.support_tickets USING btree (status, priority, last_activity_at);


--
-- Name: ix_tasks_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_assignee_id ON public.tasks USING btree (assignee_id);


--
-- Name: ix_tasks_assignee_reassign; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_assignee_reassign ON public.tasks USING btree (assignee_id, needs_reassignment);


--
-- Name: ix_tasks_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_deal_id ON public.tasks USING btree (deal_id);


--
-- Name: ix_tech_param_templates_machine_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tech_param_templates_machine_type_id ON public.tech_param_templates USING btree (machine_type_id);


--
-- Name: ix_templates_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_templates_kind ON public.templates USING btree (kind);


--
-- Name: ix_variants_deal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_variants_deal_id ON public.variants USING btree (deal_id);


--
-- Name: ix_warranty_claims_supplier_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_warranty_claims_supplier_id ON public.warranty_claims USING btree (supplier_id);


--
-- Name: uq_fin_account_active_bank; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fin_account_active_bank ON public.finance_accounts USING btree (bank_profile_id) WHERE ((status)::text = 'active'::text);


--
-- Name: uq_fin_tx_account_extref; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fin_tx_account_extref ON public.finance_transactions USING btree (account_id, external_ref) WHERE (((external_ref)::text <> ''::text) AND ((status)::text = ANY ((ARRAY['posted'::character varying, 'reversed'::character varying])::text[])) AND ((origin_type)::text <> 'reversal'::text));


--
-- Name: uq_office_docs_number; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_office_docs_number ON public.office_docs USING btree (entity_id, journal, year, seq) WHERE (seq IS NOT NULL);


--
-- Name: uq_security_alert_open; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_security_alert_open ON public.security_alerts USING btree (rule_key, COALESCE(subject_user_id, ''::character varying)) WHERE ((status)::text <> 'resolved'::text);


--
-- Name: audit_log trg_audit_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_immutable BEFORE DELETE OR UPDATE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION public.audit_log_immutable();


--
-- Name: security_events trg_security_events_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_security_events_immutable BEFORE DELETE OR UPDATE ON public.security_events FOR EACH ROW EXECUTE FUNCTION public.security_events_immutable();


--
-- Name: support_messages trg_support_messages_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_support_messages_immutable BEFORE DELETE OR UPDATE ON public.support_messages FOR EACH ROW EXECUTE FUNCTION public.support_messages_immutable();


--
-- Name: deal_versions trg_versions_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_versions_immutable BEFORE UPDATE ON public.deal_versions FOR EACH ROW EXECUTE FUNCTION public.deal_versions_immutable();


--
-- Name: acceptance_units acceptance_units_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_units
    ADD CONSTRAINT acceptance_units_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: acceptance_units acceptance_units_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_units
    ADD CONSTRAINT acceptance_units_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: acceptance_units acceptance_units_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acceptance_units
    ADD CONSTRAINT acceptance_units_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id) ON DELETE CASCADE;


--
-- Name: access_review_items access_review_items_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_review_items
    ADD CONSTRAINT access_review_items_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.access_review_campaigns(id);


--
-- Name: access_review_items access_review_items_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_review_items
    ADD CONSTRAINT access_review_items_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: access_rules access_rules_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_rules
    ADD CONSTRAINT access_rules_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.users(id);


--
-- Name: access_user_overrides access_user_overrides_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_user_overrides
    ADD CONSTRAINT access_user_overrides_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: access_user_overrides access_user_overrides_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_user_overrides
    ADD CONSTRAINT access_user_overrides_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: ai_conversations ai_conversations_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);


--
-- Name: ai_feedback ai_feedback_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT ai_feedback_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.ai_messages(id);


--
-- Name: ai_feedback ai_feedback_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT ai_feedback_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: ai_messages ai_messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT ai_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.ai_conversations(id);


--
-- Name: approvals approvals_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: approvals approvals_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: approvals approvals_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.deal_versions(id);


--
-- Name: assignment_events assignment_events_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.assignments(id);


--
-- Name: assignment_files assignment_files_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_files
    ADD CONSTRAINT assignment_files_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.assignments(id);


--
-- Name: assignments assignments_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id);


--
-- Name: assignments assignments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.users(id);


--
-- Name: assignments assignments_executor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_executor_id_fkey FOREIGN KEY (executor_id) REFERENCES public.users(id);


--
-- Name: assignments assignments_head_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_head_id_fkey FOREIGN KEY (head_id) REFERENCES public.users(id);


--
-- Name: attachments attachments_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: attachments attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: auth_sessions auth_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_sessions
    ADD CONSTRAINT auth_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: bank_profiles bank_profiles_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_profiles
    ADD CONSTRAINT bank_profiles_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.legal_profiles(id);


--
-- Name: bank_profiles bank_profiles_verified_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_profiles
    ADD CONSTRAINT bank_profiles_verified_by_fkey FOREIGN KEY (verified_by) REFERENCES public.users(id);


--
-- Name: batches batches_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: briefing_item_states briefing_item_states_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.briefing_item_states
    ADD CONSTRAINT briefing_item_states_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: client_machines client_machines_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_machines
    ADD CONSTRAINT client_machines_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: client_machines client_machines_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_machines
    ADD CONSTRAINT client_machines_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: client_machines client_machines_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_machines
    ADD CONSTRAINT client_machines_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: client_machines client_machines_equipment_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_machines
    ADD CONSTRAINT client_machines_equipment_model_id_fkey FOREIGN KEY (equipment_model_id) REFERENCES public.equipment_models(id);


--
-- Name: client_machines client_machines_other_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_machines
    ADD CONSTRAINT client_machines_other_model_id_fkey FOREIGN KEY (other_model_id) REFERENCES public.other_machine_models(id);


--
-- Name: client_price_history client_price_history_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_price_history
    ADD CONSTRAINT client_price_history_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: client_price_history client_price_history_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_price_history
    ADD CONSTRAINT client_price_history_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: clients clients_default_payment_scheme_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_default_payment_scheme_id_fkey FOREIGN KEY (default_payment_scheme_id) REFERENCES public.payment_schemes(id);


--
-- Name: clients clients_manager_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_manager_id_fkey FOREIGN KEY (manager_id) REFERENCES public.users(id);


--
-- Name: competitor_offers competitor_offers_competitor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitor_offers
    ADD CONSTRAINT competitor_offers_competitor_id_fkey FOREIGN KEY (competitor_id) REFERENCES public.competitors(id);


--
-- Name: competitor_offers competitor_offers_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitor_offers
    ADD CONSTRAINT competitor_offers_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: contracts contracts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: contracts contracts_curator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_curator_id_fkey FOREIGN KEY (curator_id) REFERENCES public.users(id);


--
-- Name: contracts contracts_own_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_own_entity_id_fkey FOREIGN KEY (own_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: deal_feed deal_feed_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_feed
    ADD CONSTRAINT deal_feed_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: deal_feed deal_feed_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_feed
    ADD CONSTRAINT deal_feed_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: deal_versions deal_versions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_versions
    ADD CONSTRAINT deal_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: deal_versions deal_versions_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_versions
    ADD CONSTRAINT deal_versions_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: deals deals_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: deals deals_logistician_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_logistician_id_fkey FOREIGN KEY (logistician_id) REFERENCES public.users(id);


--
-- Name: deals deals_manager_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_manager_id_fkey FOREIGN KEY (manager_id) REFERENCES public.users(id);


--
-- Name: deals deals_service_head_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_service_head_id_fkey FOREIGN KEY (service_head_id) REFERENCES public.users(id);


--
-- Name: equipment_executions equipment_executions_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_executions
    ADD CONSTRAINT equipment_executions_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id);


--
-- Name: equipment_executions equipment_executions_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_executions
    ADD CONSTRAINT equipment_executions_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.equipment_models(id);


--
-- Name: equipment_executions equipment_executions_proposed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_executions
    ADD CONSTRAINT equipment_executions_proposed_by_fkey FOREIGN KEY (proposed_by) REFERENCES public.users(id);


--
-- Name: equipment_models equipment_models_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_models
    ADD CONSTRAINT equipment_models_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id);


--
-- Name: equipment_models equipment_models_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_models
    ADD CONSTRAINT equipment_models_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id);


--
-- Name: equipment_models equipment_models_machine_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_models
    ADD CONSTRAINT equipment_models_machine_type_id_fkey FOREIGN KEY (machine_type_id) REFERENCES public.machine_types(id);


--
-- Name: equipment_models equipment_models_proposed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_models
    ADD CONSTRAINT equipment_models_proposed_by_fkey FOREIGN KEY (proposed_by) REFERENCES public.users(id);


--
-- Name: execution_stages execution_stages_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.execution_stages
    ADD CONSTRAINT execution_stages_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: execution_stages execution_stages_done_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.execution_stages
    ADD CONSTRAINT execution_stages_done_by_fkey FOREIGN KEY (done_by) REFERENCES public.users(id);


--
-- Name: factories factories_default_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factories
    ADD CONSTRAINT factories_default_route_id_fkey FOREIGN KEY (default_route_id) REFERENCES public.supply_routes(id);


--
-- Name: factories factories_legal_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factories
    ADD CONSTRAINT factories_legal_entity_id_fkey FOREIGN KEY (legal_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: factory_changes factory_changes_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_changes
    ADD CONSTRAINT factory_changes_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: factory_changes factory_changes_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_changes
    ADD CONSTRAINT factory_changes_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id) ON DELETE CASCADE;


--
-- Name: factory_changes factory_changes_proposed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_changes
    ADD CONSTRAINT factory_changes_proposed_by_fkey FOREIGN KEY (proposed_by) REFERENCES public.users(id);


--
-- Name: factory_offers factory_offers_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_offers
    ADD CONSTRAINT factory_offers_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: factory_offers factory_offers_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_offers
    ADD CONSTRAINT factory_offers_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id);


--
-- Name: factory_offers factory_offers_selected_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_offers
    ADD CONSTRAINT factory_offers_selected_by_fkey FOREIGN KEY (selected_by) REFERENCES public.users(id);


--
-- Name: factory_price_history factory_price_history_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.factory_price_history
    ADD CONSTRAINT factory_price_history_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id);


--
-- Name: file_download_requests file_download_requests_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_download_requests
    ADD CONSTRAINT file_download_requests_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: finance_accounts finance_accounts_bank_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_accounts
    ADD CONSTRAINT finance_accounts_bank_profile_id_fkey FOREIGN KEY (bank_profile_id) REFERENCES public.bank_profiles(id);


--
-- Name: finance_accounts finance_accounts_legal_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_accounts
    ADD CONSTRAINT finance_accounts_legal_entity_id_fkey FOREIGN KEY (legal_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: finance_allocations finance_allocations_obligation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_allocations
    ADD CONSTRAINT finance_allocations_obligation_id_fkey FOREIGN KEY (obligation_id) REFERENCES public.finance_obligations(id);


--
-- Name: finance_allocations finance_allocations_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_allocations
    ADD CONSTRAINT finance_allocations_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.finance_transactions(id);


--
-- Name: finance_balance_snapshots finance_balance_snapshots_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_balance_snapshots
    ADD CONSTRAINT finance_balance_snapshots_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.finance_accounts(id);


--
-- Name: finance_fx_operations finance_fx_operations_buy_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_buy_account_id_fkey FOREIGN KEY (buy_account_id) REFERENCES public.finance_accounts(id);


--
-- Name: finance_fx_operations finance_fx_operations_buy_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_buy_transaction_id_fkey FOREIGN KEY (buy_transaction_id) REFERENCES public.finance_transactions(id);


--
-- Name: finance_fx_operations finance_fx_operations_fee_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_fee_transaction_id_fkey FOREIGN KEY (fee_transaction_id) REFERENCES public.finance_transactions(id);


--
-- Name: finance_fx_operations finance_fx_operations_sell_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_sell_account_id_fkey FOREIGN KEY (sell_account_id) REFERENCES public.finance_accounts(id);


--
-- Name: finance_fx_operations finance_fx_operations_sell_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_fx_operations
    ADD CONSTRAINT finance_fx_operations_sell_transaction_id_fkey FOREIGN KEY (sell_transaction_id) REFERENCES public.finance_transactions(id);


--
-- Name: finance_import_batches finance_import_batches_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_import_batches
    ADD CONSTRAINT finance_import_batches_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.finance_accounts(id);


--
-- Name: finance_obligations finance_obligations_superseded_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_obligations
    ADD CONSTRAINT finance_obligations_superseded_by_id_fkey FOREIGN KEY (superseded_by_id) REFERENCES public.finance_obligations(id);


--
-- Name: finance_recognition_events finance_recognition_events_obligation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_recognition_events
    ADD CONSTRAINT finance_recognition_events_obligation_id_fkey FOREIGN KEY (obligation_id) REFERENCES public.finance_obligations(id);


--
-- Name: finance_transactions finance_transactions_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.finance_accounts(id);


--
-- Name: finance_transactions finance_transactions_reversal_of_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finance_transactions
    ADD CONSTRAINT finance_transactions_reversal_of_id_fkey FOREIGN KEY (reversal_of_id) REFERENCES public.finance_transactions(id);


--
-- Name: glossary glossary_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary
    ADD CONSTRAINT glossary_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.users(id);


--
-- Name: kp_revisions kp_revisions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kp_revisions
    ADD CONSTRAINT kp_revisions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: kp_revisions kp_revisions_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kp_revisions
    ADD CONSTRAINT kp_revisions_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: legal_packages legal_packages_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_packages
    ADD CONSTRAINT legal_packages_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: legal_packages legal_packages_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_packages
    ADD CONSTRAINT legal_packages_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: legal_packages legal_packages_own_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_packages
    ADD CONSTRAINT legal_packages_own_entity_id_fkey FOREIGN KEY (own_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: legal_profiles legal_profiles_checked_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_profiles
    ADD CONSTRAINT legal_profiles_checked_by_fkey FOREIGN KEY (checked_by) REFERENCES public.users(id);


--
-- Name: legal_zip_sets legal_zip_sets_confirmed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_zip_sets
    ADD CONSTRAINT legal_zip_sets_confirmed_by_fkey FOREIGN KEY (confirmed_by) REFERENCES public.users(id);


--
-- Name: legal_zip_sets legal_zip_sets_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_zip_sets
    ADD CONSTRAINT legal_zip_sets_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: legal_zip_sets legal_zip_sets_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_zip_sets
    ADD CONSTRAINT legal_zip_sets_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id);


--
-- Name: local_options local_options_cost_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_options
    ADD CONSTRAINT local_options_cost_approved_by_fkey FOREIGN KEY (cost_approved_by) REFERENCES public.users(id);


--
-- Name: local_options local_options_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_options
    ADD CONSTRAINT local_options_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: local_options local_options_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_options
    ADD CONSTRAINT local_options_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id);


--
-- Name: machine_hours machine_hours_entered_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_hours
    ADD CONSTRAINT machine_hours_entered_by_fkey FOREIGN KEY (entered_by) REFERENCES public.users(id);


--
-- Name: machine_hours machine_hours_machine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_hours
    ADD CONSTRAINT machine_hours_machine_id_fkey FOREIGN KEY (machine_id) REFERENCES public.client_machines(id) ON DELETE CASCADE;


--
-- Name: machine_reminders machine_reminders_machine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_reminders
    ADD CONSTRAINT machine_reminders_machine_id_fkey FOREIGN KEY (machine_id) REFERENCES public.client_machines(id) ON DELETE CASCADE;


--
-- Name: machine_reminders machine_reminders_manager_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_reminders
    ADD CONSTRAINT machine_reminders_manager_id_fkey FOREIGN KEY (manager_id) REFERENCES public.users(id);


--
-- Name: machine_types machine_types_proposed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.machine_types
    ADD CONSTRAINT machine_types_proposed_by_fkey FOREIGN KEY (proposed_by) REFERENCES public.users(id);


--
-- Name: maintenance_kits maintenance_kits_equipment_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_kits
    ADD CONSTRAINT maintenance_kits_equipment_model_id_fkey FOREIGN KEY (equipment_model_id) REFERENCES public.equipment_models(id);


--
-- Name: maintenance_kits maintenance_kits_other_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_kits
    ADD CONSTRAINT maintenance_kits_other_model_id_fkey FOREIGN KEY (other_model_id) REFERENCES public.other_machine_models(id);


--
-- Name: markup_policies markup_policies_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markup_policies
    ADD CONSTRAINT markup_policies_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: mfa_challenges mfa_challenges_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT mfa_challenges_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: mfa_credentials mfa_credentials_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_credentials
    ADD CONSTRAINT mfa_credentials_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: mfa_recovery_codes mfa_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: model_documents model_documents_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_documents
    ADD CONSTRAINT model_documents_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.equipment_models(id) ON DELETE CASCADE;


--
-- Name: model_documents model_documents_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_documents
    ADD CONSTRAINT model_documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: negotiation_steps negotiation_steps_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_steps
    ADD CONSTRAINT negotiation_steps_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: negotiation_steps negotiation_steps_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_steps
    ADD CONSTRAINT negotiation_steps_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: offer_prices offer_prices_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_prices
    ADD CONSTRAINT offer_prices_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: office_counters office_counters_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_counters
    ADD CONSTRAINT office_counters_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.legal_entities(id);


--
-- Name: office_docs office_docs_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_docs
    ADD CONSTRAINT office_docs_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.legal_entities(id);


--
-- Name: office_docs office_docs_executor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_docs
    ADD CONSTRAINT office_docs_executor_id_fkey FOREIGN KEY (executor_id) REFERENCES public.users(id);


--
-- Name: office_docs office_docs_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_docs
    ADD CONSTRAINT office_docs_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES public.users(id);


--
-- Name: office_docs office_docs_signer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_docs
    ADD CONSTRAINT office_docs_signer_id_fkey FOREIGN KEY (signer_id) REFERENCES public.office_signers(id);


--
-- Name: office_routes office_routes_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_routes
    ADD CONSTRAINT office_routes_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.legal_entities(id);


--
-- Name: office_signers office_signers_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_signers
    ADD CONSTRAINT office_signers_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.legal_entities(id);


--
-- Name: office_signers office_signers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_signers
    ADD CONSTRAINT office_signers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: org_directions org_directions_head_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_directions
    ADD CONSTRAINT org_directions_head_id_fkey FOREIGN KEY (head_id) REFERENCES public.users(id);


--
-- Name: org_members org_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_members
    ADD CONSTRAINT org_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: part_applicability part_applicability_equipment_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_applicability
    ADD CONSTRAINT part_applicability_equipment_model_id_fkey FOREIGN KEY (equipment_model_id) REFERENCES public.equipment_models(id);


--
-- Name: part_applicability part_applicability_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_applicability
    ADD CONSTRAINT part_applicability_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE CASCADE;


--
-- Name: part_batches part_batches_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_batches
    ADD CONSTRAINT part_batches_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: part_batches part_batches_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_batches
    ADD CONSTRAINT part_batches_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.supply_routes(id);


--
-- Name: part_client_prices part_client_prices_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_client_prices
    ADD CONSTRAINT part_client_prices_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: part_client_prices part_client_prices_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_client_prices
    ADD CONSTRAINT part_client_prices_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE CASCADE;


--
-- Name: part_cross_refs part_cross_refs_analog_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_cross_refs
    ADD CONSTRAINT part_cross_refs_analog_part_id_fkey FOREIGN KEY (analog_part_id) REFERENCES public.parts(id);


--
-- Name: part_cross_refs part_cross_refs_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_cross_refs
    ADD CONSTRAINT part_cross_refs_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE CASCADE;


--
-- Name: part_cross_refs part_cross_refs_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_cross_refs
    ADD CONSTRAINT part_cross_refs_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.factories(id);


--
-- Name: part_deviations part_deviations_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_deviations
    ADD CONSTRAINT part_deviations_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: part_deviations part_deviations_po_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_deviations
    ADD CONSTRAINT part_deviations_po_id_fkey FOREIGN KEY (po_id) REFERENCES public.purchase_orders(id);


--
-- Name: part_deviations part_deviations_po_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_deviations
    ADD CONSTRAINT part_deviations_po_line_id_fkey FOREIGN KEY (po_line_id) REFERENCES public.po_lines(id);


--
-- Name: part_deviations part_deviations_receipt_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_deviations
    ADD CONSTRAINT part_deviations_receipt_line_id_fkey FOREIGN KEY (receipt_line_id) REFERENCES public.part_receipt_lines(id) ON DELETE SET NULL;


--
-- Name: part_inventories part_inventories_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventories
    ADD CONSTRAINT part_inventories_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id);


--
-- Name: part_inventories part_inventories_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventories
    ADD CONSTRAINT part_inventories_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: part_inventories part_inventories_submitted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventories
    ADD CONSTRAINT part_inventories_submitted_by_fkey FOREIGN KEY (submitted_by) REFERENCES public.users(id);


--
-- Name: part_inventories part_inventories_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventories
    ADD CONSTRAINT part_inventories_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.warehouses(id);


--
-- Name: part_inventory_lines part_inventory_lines_counted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventory_lines
    ADD CONSTRAINT part_inventory_lines_counted_by_fkey FOREIGN KEY (counted_by) REFERENCES public.users(id);


--
-- Name: part_inventory_lines part_inventory_lines_inventory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventory_lines
    ADD CONSTRAINT part_inventory_lines_inventory_id_fkey FOREIGN KEY (inventory_id) REFERENCES public.part_inventories(id) ON DELETE CASCADE;


--
-- Name: part_inventory_lines part_inventory_lines_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_inventory_lines
    ADD CONSTRAINT part_inventory_lines_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: part_kps part_kps_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_kps
    ADD CONSTRAINT part_kps_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: part_kps part_kps_handling_fee_removed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_kps
    ADD CONSTRAINT part_kps_handling_fee_removed_by_fkey FOREIGN KEY (handling_fee_removed_by) REFERENCES public.users(id);


--
-- Name: part_kps part_kps_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_kps
    ADD CONSTRAINT part_kps_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.part_requests(id) ON DELETE CASCADE;


--
-- Name: part_kps part_kps_sent_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_kps
    ADD CONSTRAINT part_kps_sent_by_fkey FOREIGN KEY (sent_by) REFERENCES public.users(id);


--
-- Name: part_receipt_lines part_receipt_lines_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipt_lines
    ADD CONSTRAINT part_receipt_lines_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: part_receipt_lines part_receipt_lines_po_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipt_lines
    ADD CONSTRAINT part_receipt_lines_po_line_id_fkey FOREIGN KEY (po_line_id) REFERENCES public.po_lines(id);


--
-- Name: part_receipt_lines part_receipt_lines_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipt_lines
    ADD CONSTRAINT part_receipt_lines_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.part_receipts(id) ON DELETE CASCADE;


--
-- Name: part_receipts part_receipts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipts
    ADD CONSTRAINT part_receipts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: part_receipts part_receipts_po_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipts
    ADD CONSTRAINT part_receipts_po_id_fkey FOREIGN KEY (po_id) REFERENCES public.purchase_orders(id);


--
-- Name: part_receipts part_receipts_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_receipts
    ADD CONSTRAINT part_receipts_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.warehouses(id);


--
-- Name: part_request_lines part_request_lines_parent_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_request_lines
    ADD CONSTRAINT part_request_lines_parent_line_id_fkey FOREIGN KEY (parent_line_id) REFERENCES public.part_request_lines(id);


--
-- Name: part_request_lines part_request_lines_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_request_lines
    ADD CONSTRAINT part_request_lines_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: part_request_lines part_request_lines_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_request_lines
    ADD CONSTRAINT part_request_lines_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.part_requests(id) ON DELETE CASCADE;


--
-- Name: part_request_lines part_request_lines_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_request_lines
    ADD CONSTRAINT part_request_lines_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.supply_routes(id);


--
-- Name: part_request_lines part_request_lines_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_request_lines
    ADD CONSTRAINT part_request_lines_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.factories(id);


--
-- Name: part_requests part_requests_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_requests
    ADD CONSTRAINT part_requests_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: part_requests part_requests_manager_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_requests
    ADD CONSTRAINT part_requests_manager_id_fkey FOREIGN KEY (manager_id) REFERENCES public.users(id);


--
-- Name: part_reservations part_reservations_kp_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_reservations
    ADD CONSTRAINT part_reservations_kp_id_fkey FOREIGN KEY (kp_id) REFERENCES public.part_kps(id) ON DELETE SET NULL;


--
-- Name: part_reservations part_reservations_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_reservations
    ADD CONSTRAINT part_reservations_line_id_fkey FOREIGN KEY (line_id) REFERENCES public.part_request_lines(id) ON DELETE CASCADE;


--
-- Name: part_reservations part_reservations_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_reservations
    ADD CONSTRAINT part_reservations_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: part_reservations part_reservations_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_reservations
    ADD CONSTRAINT part_reservations_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.warehouses(id);


--
-- Name: part_shipment_lines part_shipment_lines_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipment_lines
    ADD CONSTRAINT part_shipment_lines_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: part_shipment_lines part_shipment_lines_request_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipment_lines
    ADD CONSTRAINT part_shipment_lines_request_line_id_fkey FOREIGN KEY (request_line_id) REFERENCES public.part_request_lines(id);


--
-- Name: part_shipment_lines part_shipment_lines_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipment_lines
    ADD CONSTRAINT part_shipment_lines_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.part_shipments(id) ON DELETE CASCADE;


--
-- Name: part_shipments part_shipments_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipments
    ADD CONSTRAINT part_shipments_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: part_shipments part_shipments_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipments
    ADD CONSTRAINT part_shipments_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.part_requests(id);


--
-- Name: part_shipments part_shipments_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_shipments
    ADD CONSTRAINT part_shipments_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.warehouses(id);


--
-- Name: part_stock_moves part_stock_moves_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock_moves
    ADD CONSTRAINT part_stock_moves_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: part_stock_moves part_stock_moves_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock_moves
    ADD CONSTRAINT part_stock_moves_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: part_stock_moves part_stock_moves_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock_moves
    ADD CONSTRAINT part_stock_moves_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.warehouses(id);


--
-- Name: part_stock part_stock_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock
    ADD CONSTRAINT part_stock_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE CASCADE;


--
-- Name: part_stock part_stock_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_stock
    ADD CONSTRAINT part_stock_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.warehouses(id);


--
-- Name: parts parts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: parts parts_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.supply_routes(id);


--
-- Name: parts parts_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.factories(id);


--
-- Name: password_history password_history_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_history
    ADD CONSTRAINT password_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: password_links password_links_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_links
    ADD CONSTRAINT password_links_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: plan_fact_entries plan_fact_entries_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_fact_entries
    ADD CONSTRAINT plan_fact_entries_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: plan_fact_entries plan_fact_entries_entered_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_fact_entries
    ADD CONSTRAINT plan_fact_entries_entered_by_fkey FOREIGN KEY (entered_by) REFERENCES public.users(id);


--
-- Name: plan_fact_entries plan_fact_entries_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_fact_entries
    ADD CONSTRAINT plan_fact_entries_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id);


--
-- Name: po_lines po_lines_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.po_lines
    ADD CONSTRAINT po_lines_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: po_lines po_lines_po_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.po_lines
    ADD CONSTRAINT po_lines_po_id_fkey FOREIGN KEY (po_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: po_lines po_lines_request_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.po_lines
    ADD CONSTRAINT po_lines_request_line_id_fkey FOREIGN KEY (request_line_id) REFERENCES public.part_request_lines(id) ON DELETE SET NULL;


--
-- Name: position_images position_images_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_images
    ADD CONSTRAINT position_images_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.attachments(id);


--
-- Name: position_images position_images_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_images
    ADD CONSTRAINT position_images_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: position_images position_images_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_images
    ADD CONSTRAINT position_images_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id);


--
-- Name: position_images position_images_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_images
    ADD CONSTRAINT position_images_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: position_tech_params position_tech_params_filled_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_tech_params
    ADD CONSTRAINT position_tech_params_filled_by_fkey FOREIGN KEY (filled_by) REFERENCES public.users(id);


--
-- Name: position_tech_params position_tech_params_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_tech_params
    ADD CONSTRAINT position_tech_params_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id);


--
-- Name: positions positions_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.batches(id);


--
-- Name: positions positions_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: positions positions_equipment_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_equipment_model_id_fkey FOREIGN KEY (equipment_model_id) REFERENCES public.equipment_models(id);


--
-- Name: positions positions_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id);


--
-- Name: positions positions_rfq_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_rfq_import_id_fkey FOREIGN KEY (rfq_import_id) REFERENCES public.rfq_imports(id);


--
-- Name: powers_of_attorney powers_of_attorney_attorney_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.powers_of_attorney
    ADD CONSTRAINT powers_of_attorney_attorney_user_id_fkey FOREIGN KEY (attorney_user_id) REFERENCES public.users(id);


--
-- Name: powers_of_attorney powers_of_attorney_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.powers_of_attorney
    ADD CONSTRAINT powers_of_attorney_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: powers_of_attorney powers_of_attorney_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.powers_of_attorney
    ADD CONSTRAINT powers_of_attorney_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.legal_entities(id);


--
-- Name: powers_of_attorney powers_of_attorney_revoked_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.powers_of_attorney
    ADD CONSTRAINT powers_of_attorney_revoked_by_fkey FOREIGN KEY (revoked_by) REFERENCES public.users(id);


--
-- Name: purchase_orders purchase_orders_buyer_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_buyer_entity_id_fkey FOREIGN KEY (buyer_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: purchase_orders purchase_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: purchase_orders purchase_orders_parent_po_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_parent_po_id_fkey FOREIGN KEY (parent_po_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.supply_routes(id);


--
-- Name: purchase_orders purchase_orders_seller_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_seller_entity_id_fkey FOREIGN KEY (seller_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: purchase_orders purchase_orders_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.factories(id);


--
-- Name: quote_links quote_links_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quote_links
    ADD CONSTRAINT quote_links_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: rate_settings rate_settings_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_settings
    ADD CONSTRAINT rate_settings_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.users(id);


--
-- Name: rfq_imports rfq_imports_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_imports
    ADD CONSTRAINT rfq_imports_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: rfq_imports rfq_imports_factory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_imports
    ADD CONSTRAINT rfq_imports_factory_id_fkey FOREIGN KEY (factory_id) REFERENCES public.factories(id);


--
-- Name: rfq_imports rfq_imports_imported_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rfq_imports
    ADD CONSTRAINT rfq_imports_imported_by_fkey FOREIGN KEY (imported_by) REFERENCES public.users(id);


--
-- Name: route_steps route_steps_buyer_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_steps
    ADD CONSTRAINT route_steps_buyer_entity_id_fkey FOREIGN KEY (buyer_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: route_steps route_steps_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_steps
    ADD CONSTRAINT route_steps_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.supply_routes(id) ON DELETE CASCADE;


--
-- Name: route_steps route_steps_seller_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_steps
    ADD CONSTRAINT route_steps_seller_entity_id_fkey FOREIGN KEY (seller_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: supplier_rfq_lines supplier_rfq_lines_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfq_lines
    ADD CONSTRAINT supplier_rfq_lines_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: supplier_rfq_lines supplier_rfq_lines_request_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfq_lines
    ADD CONSTRAINT supplier_rfq_lines_request_line_id_fkey FOREIGN KEY (request_line_id) REFERENCES public.part_request_lines(id) ON DELETE SET NULL;


--
-- Name: supplier_rfq_lines supplier_rfq_lines_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfq_lines
    ADD CONSTRAINT supplier_rfq_lines_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.supplier_rfqs(id) ON DELETE CASCADE;


--
-- Name: supplier_rfqs supplier_rfqs_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfqs
    ADD CONSTRAINT supplier_rfqs_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: supplier_rfqs supplier_rfqs_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_rfqs
    ADD CONSTRAINT supplier_rfqs_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.factories(id);


--
-- Name: support_attachments support_attachments_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_attachments
    ADD CONSTRAINT support_attachments_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.support_messages(id);


--
-- Name: support_attachments support_attachments_ticket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_attachments
    ADD CONSTRAINT support_attachments_ticket_id_fkey FOREIGN KEY (ticket_id) REFERENCES public.support_tickets(id);


--
-- Name: support_attachments support_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_attachments
    ADD CONSTRAINT support_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: support_messages support_messages_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_messages
    ADD CONSTRAINT support_messages_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.users(id);


--
-- Name: support_messages support_messages_ticket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_messages
    ADD CONSTRAINT support_messages_ticket_id_fkey FOREIGN KEY (ticket_id) REFERENCES public.support_tickets(id);


--
-- Name: support_tickets support_tickets_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id);


--
-- Name: support_tickets support_tickets_reporter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_reporter_id_fkey FOREIGN KEY (reporter_id) REFERENCES public.users(id);


--
-- Name: tasks tasks_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id);


--
-- Name: tasks tasks_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: tech_param_templates tech_param_templates_machine_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tech_param_templates
    ADD CONSTRAINT tech_param_templates_machine_type_id_fkey FOREIGN KEY (machine_type_id) REFERENCES public.machine_types(id);


--
-- Name: templates templates_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: variants variants_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.variants
    ADD CONSTRAINT variants_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: warehouses warehouses_legal_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warehouses
    ADD CONSTRAINT warehouses_legal_entity_id_fkey FOREIGN KEY (legal_entity_id) REFERENCES public.legal_entities(id);


--
-- Name: warranty_claims warranty_claims_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: warranty_claims warranty_claims_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: warranty_claims warranty_claims_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deals(id);


--
-- Name: warranty_claims warranty_claims_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.factories(id);


--
-- PostgreSQL database dump complete
--

\unrestrict RVQ8umZi40eY4z850Azhbzt9bVWO9T0mw1r4Od3zLQPuMbp2JbU9uR7XoUWQUPt

