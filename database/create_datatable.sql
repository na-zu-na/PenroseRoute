-- ============================================================
-- PernoseRoute / Delivery Planning Database
-- PostgreSQL SQL script cleaned for Navicat Query Console
-- Source: pg_dump 18.4
--
-- Notes:
-- 1. Run against PostgreSQL (not MySQL).
-- 2. Requires permission to CREATE EXTENSION for pgcrypto and btree_gist.
-- 3. Intended for a fresh schema/database. Existing objects with the same
--    names may cause "already exists" errors.
-- ============================================================

--

-- PostgreSQL database dump

--


-- Dumped from database version 18.4

-- Dumped by pg_dump version 18.4


--

-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -

--


CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--

-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: -

--


COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--

-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -

--


CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--

-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -

--


COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--

-- Name: prevent_completed_order_regression(); Type: FUNCTION; Schema: public; Owner: -

--


CREATE FUNCTION public.prevent_completed_order_regression() RETURNS trigger

    LANGUAGE plpgsql

    AS $$

        BEGIN

            IF OLD.execution_status = 'COMPLETED' AND NEW.execution_status <> 'COMPLETED' THEN

                RAISE EXCEPTION 'completed order execution status is immutable';

            END IF;

            RETURN NEW;

        END;

        $$;


--

-- Name: prevent_completed_stop_regression(); Type: FUNCTION; Schema: public; Owner: -

--


CREATE FUNCTION public.prevent_completed_stop_regression() RETURNS trigger

    LANGUAGE plpgsql

    AS $$

        BEGIN

            IF OLD.status = 'COMPLETED' AND NEW.status <> 'COMPLETED' THEN

                RAISE EXCEPTION 'completed route stop status is immutable';

            END IF;

            RETURN NEW;

        END;

        $$;


--

-- Name: validate_route_stop_relationships(); Type: FUNCTION; Schema: public; Owner: -

--


CREATE FUNCTION public.validate_route_stop_relationships() RETURNS trigger

    LANGUAGE plpgsql

    AS $$

        DECLARE

            preceding_order_id UUID;

            preceding_type VARCHAR(16);

            preceding_sequence INTEGER;

            preceding_plan_id UUID;

            current_plan_id UUID;

            expected_location_id UUID;

        BEGIN

            SELECT delivery_plan_id INTO current_plan_id

            FROM vehicle_routes WHERE id = NEW.vehicle_route_id;


            IF NEW.stop_type = 'DELIVERY' THEN

                SELECT ps.order_id, ps.stop_type, ps.sequence_no, pr.delivery_plan_id

                INTO preceding_order_id, preceding_type, preceding_sequence, preceding_plan_id

                FROM route_stops ps

                JOIN vehicle_routes pr ON pr.id = ps.vehicle_route_id

                WHERE ps.id = NEW.precedence_stop_id;


                IF preceding_order_id IS DISTINCT FROM NEW.order_id

                   OR preceding_type NOT IN ('PICKUP', 'HANDOVER')

                   OR preceding_sequence >= NEW.sequence_no

                   OR preceding_plan_id IS DISTINCT FROM current_plan_id THEN

                    RAISE EXCEPTION 'invalid delivery stop precedence relationship';

                END IF;

            END IF;


            IF NEW.stop_type = 'PICKUP' THEN

                SELECT pickup_location_id INTO expected_location_id

                FROM orders WHERE id = NEW.order_id;

            ELSIF NEW.stop_type = 'DELIVERY' THEN

                SELECT delivery_location_id INTO expected_location_id

                FROM orders WHERE id = NEW.order_id;

            ELSE

                SELECT incident_location_id INTO expected_location_id

                FROM incidents WHERE id = NEW.source_incident_id;

            END IF;


            IF expected_location_id IS DISTINCT FROM NEW.location_id THEN

                RAISE EXCEPTION 'route stop location does not match its business source';

            END IF;

            RETURN NEW;

        END;

        $$;


--

-- Name: customers; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.customers (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    customer_code character varying(64) NOT NULL,

    name character varying(160) NOT NULL,

    default_delivery_location_id uuid NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_customers_code CHECK ((btrim((customer_code)::text) <> ''::text)),

    CONSTRAINT ck_customers_name CHECK ((btrim((name)::text) <> ''::text))

);


--

-- Name: delivery_plan_orders; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.delivery_plan_orders (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    delivery_plan_id uuid NOT NULL,

    order_id uuid NOT NULL,

    assignment_status character varying(16) NOT NULL,

    vehicle_route_id uuid,

    unassigned_reason_code character varying(32),

    unassigned_reason_detail text,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_delivery_plan_orders_assignment CHECK (((((assignment_status)::text = 'ASSIGNED'::text) AND (vehicle_route_id IS NOT NULL) AND (unassigned_reason_code IS NULL) AND (unassigned_reason_detail IS NULL)) OR (((assignment_status)::text = 'UNASSIGNED'::text) AND (vehicle_route_id IS NULL) AND (unassigned_reason_code IS NOT NULL)))),

    CONSTRAINT ck_delivery_plan_orders_other_reason CHECK ((((unassigned_reason_code)::text <> 'OTHER'::text) OR (btrim(COALESCE(unassigned_reason_detail, ''::text)) <> ''::text))),

    CONSTRAINT ck_delivery_plan_orders_reason CHECK (((unassigned_reason_code IS NULL) OR ((unassigned_reason_code)::text = ANY ((ARRAY['CAPACITY_INFEASIBLE'::character varying, 'TIME_WINDOW_INFEASIBLE'::character varying, 'RESOURCE_UNAVAILABLE'::character varying, 'NO_FEASIBLE_ROUTE'::character varying, 'INVALID_INPUT'::character varying, 'OTHER'::character varying])::text[])))),

    CONSTRAINT ck_delivery_plan_orders_status CHECK (((assignment_status)::text = ANY ((ARRAY['ASSIGNED'::character varying, 'UNASSIGNED'::character varying])::text[])))

);


--

-- Name: delivery_plans; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.delivery_plans (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    plan_code character varying(64) NOT NULL,

    plan_group_id uuid NOT NULL,

    business_date date NOT NULL,

    version_no integer NOT NULL,

    parent_plan_id uuid,

    status character varying(16) DEFAULT 'DRAFT'::character varying NOT NULL,

    solver_engine character varying(32) DEFAULT 'OR_TOOLS'::character varying NOT NULL,

    validation_status character varying(16) DEFAULT 'PENDING'::character varying NOT NULL,

    total_distance_meters bigint,

    total_duration_seconds bigint,

    vehicle_count integer DEFAULT 0 NOT NULL,

    assigned_order_count integer DEFAULT 0 NOT NULL,

    unassigned_order_count integer DEFAULT 0 NOT NULL,

    validation_summary jsonb,

    activated_at timestamp with time zone,

    superseded_at timestamp with time zone,

    created_by character varying(128) NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_delivery_plans_counts CHECK (((vehicle_count >= 0) AND (assigned_order_count >= 0) AND (unassigned_order_count >= 0))),

    CONSTRAINT ck_delivery_plans_current CHECK ((((status)::text <> 'CURRENT'::text) OR (((validation_status)::text = 'VALID'::text) AND (activated_at IS NOT NULL)))),

    CONSTRAINT ck_delivery_plans_metrics CHECK ((((total_distance_meters IS NULL) OR (total_distance_meters >= 0)) AND ((total_duration_seconds IS NULL) OR (total_duration_seconds >= 0)))),

    CONSTRAINT ck_delivery_plans_parent CHECK ((((version_no = 1) AND (parent_plan_id IS NULL)) OR ((version_no > 1) AND (parent_plan_id IS NOT NULL)))),

    CONSTRAINT ck_delivery_plans_solver CHECK (((solver_engine)::text = 'OR_TOOLS'::text)),

    CONSTRAINT ck_delivery_plans_status CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'CANDIDATE'::character varying, 'CURRENT'::character varying, 'SUPERSEDED'::character varying, 'CANCELLED'::character varying])::text[]))),

    CONSTRAINT ck_delivery_plans_superseded CHECK ((((status)::text <> 'SUPERSEDED'::text) OR (superseded_at IS NOT NULL))),

    CONSTRAINT ck_delivery_plans_validation CHECK (((validation_status)::text = ANY ((ARRAY['PENDING'::character varying, 'VALID'::character varying, 'INVALID'::character varying])::text[]))),

    CONSTRAINT ck_delivery_plans_version CHECK ((version_no > 0))

);


--

-- Name: drivers; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.drivers (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    driver_code character varying(64) NOT NULL,

    name character varying(160) NOT NULL,

    status character varying(16) DEFAULT 'AVAILABLE'::character varying NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_drivers_code CHECK ((btrim((driver_code)::text) <> ''::text)),

    CONSTRAINT ck_drivers_name CHECK ((btrim((name)::text) <> ''::text)),

    CONSTRAINT ck_drivers_status CHECK (((status)::text = ANY ((ARRAY['AVAILABLE'::character varying, 'ACTIVE'::character varying, 'UNAVAILABLE'::character varying])::text[])))

);


--

-- Name: incident_affected_orders; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.incident_affected_orders (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    incident_id uuid NOT NULL,

    order_id uuid NOT NULL,

    original_vehicle_route_id uuid,

    execution_status_snapshot character varying(32) NOT NULL,

    risk_status_snapshot character varying(16) NOT NULL,

    was_picked_up boolean NOT NULL,

    was_completed boolean NOT NULL,

    requires_replanning boolean NOT NULL,

    handover_required boolean DEFAULT false NOT NULL,

    impact_type character varying(32) NOT NULL,

    impact_reason text NOT NULL,

    assessed_at timestamp with time zone NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_affected_orders_completed CHECK ((was_completed = ((execution_status_snapshot)::text = 'COMPLETED'::text))),

    CONSTRAINT ck_affected_orders_execution CHECK (((execution_status_snapshot)::text = ANY ((ARRAY['PLANNED'::character varying, 'PICKUP_IN_PROGRESS'::character varying, 'PICKED_UP'::character varying, 'DELIVERING'::character varying, 'COMPLETED'::character varying])::text[]))),

    CONSTRAINT ck_affected_orders_frozen CHECK ((((impact_type)::text <> 'COMPLETED_FROZEN'::text) OR (was_completed AND (NOT requires_replanning) AND (NOT handover_required)))),

    CONSTRAINT ck_affected_orders_handover CHECK (((NOT handover_required) OR (was_picked_up AND (NOT was_completed) AND requires_replanning))),

    CONSTRAINT ck_affected_orders_impact CHECK (((impact_type)::text = ANY ((ARRAY['COMPLETED_FROZEN'::character varying, 'HANDOVER_REQUIRED'::character varying, 'PICKUP_REPLAN'::character varying, 'WAITING_TIME_UPDATE'::character varying, 'DOWNSTREAM_ROUTE_IMPACT'::character varying])::text[]))),

    CONSTRAINT ck_affected_orders_picked_up CHECK ((was_picked_up = ((execution_status_snapshot)::text = ANY ((ARRAY['PICKED_UP'::character varying, 'DELIVERING'::character varying, 'COMPLETED'::character varying])::text[])))),

    CONSTRAINT ck_affected_orders_risk CHECK (((risk_status_snapshot)::text = ANY ((ARRAY['NORMAL'::character varying, 'AT_RISK'::character varying])::text[])))

);


--

-- Name: incidents; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.incidents (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    incident_code character varying(64) NOT NULL,

    incident_type character varying(32) NOT NULL,

    status character varying(16) DEFAULT 'DETECTED'::character varying NOT NULL,

    delivery_plan_id uuid NOT NULL,

    vehicle_route_id uuid,

    vehicle_id uuid,

    merchant_id uuid,

    incident_location_id uuid,

    original_ready_at timestamp with time zone,

    updated_ready_at timestamp with time zone,

    delay_seconds integer,

    detected_at timestamp with time zone NOT NULL,

    resolved_at timestamp with time zone,

    detected_by character varying(128) NOT NULL,

    details jsonb,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_incidents_delay CHECK (((delay_seconds IS NULL) OR (delay_seconds >= 0))),

    CONSTRAINT ck_incidents_delay_value CHECK ((((incident_type)::text <> 'MERCHANT_DELAY'::text) OR (delay_seconds = (EXTRACT(epoch FROM (updated_ready_at - original_ready_at)))::integer))),

    CONSTRAINT ck_incidents_resolution CHECK ((((status)::text = 'RESOLVED'::text) = (resolved_at IS NOT NULL))),

    CONSTRAINT ck_incidents_resolution_time CHECK (((resolved_at IS NULL) OR (resolved_at >= detected_at))),

    CONSTRAINT ck_incidents_status CHECK (((status)::text = ANY ((ARRAY['DETECTED'::character varying, 'ASSESSING'::character varying, 'REPLANNING'::character varying, 'REVIEW'::character varying, 'RESOLVED'::character varying])::text[]))),

    CONSTRAINT ck_incidents_type CHECK (((incident_type)::text = ANY ((ARRAY['VEHICLE_UNAVAILABLE'::character varying, 'MERCHANT_DELAY'::character varying])::text[]))),

    CONSTRAINT ck_incidents_type_fields CHECK (((((incident_type)::text = 'VEHICLE_UNAVAILABLE'::text) AND (vehicle_route_id IS NOT NULL) AND (vehicle_id IS NOT NULL) AND (incident_location_id IS NOT NULL) AND (merchant_id IS NULL) AND (original_ready_at IS NULL) AND (updated_ready_at IS NULL) AND (delay_seconds IS NULL)) OR (((incident_type)::text = 'MERCHANT_DELAY'::text) AND (merchant_id IS NOT NULL) AND (vehicle_route_id IS NULL) AND (vehicle_id IS NULL) AND (incident_location_id IS NULL) AND (original_ready_at IS NOT NULL) AND (updated_ready_at IS NOT NULL) AND (delay_seconds IS NOT NULL))))

);


--

-- Name: locations; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.locations (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    location_code character varying(64),

    display_name character varying(160) NOT NULL,

    address_text text,

    latitude numeric(9,6) NOT NULL,

    longitude numeric(9,6) NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_locations_display_name CHECK ((btrim((display_name)::text) <> ''::text)),

    CONSTRAINT ck_locations_latitude CHECK (((latitude IS NULL) OR ((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric)))),

    CONSTRAINT ck_locations_longitude CHECK (((longitude IS NULL) OR ((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric))))

);


--

-- Name: merchants; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.merchants (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    merchant_code character varying(64) NOT NULL,

    name character varying(160) NOT NULL,

    pickup_location_id uuid NOT NULL,

    preparation_status character varying(16) DEFAULT 'PREPARING'::character varying NOT NULL,

    operational_ready_at timestamp with time zone,

    default_pickup_service_seconds integer DEFAULT 0 NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_merchants_code CHECK ((btrim((merchant_code)::text) <> ''::text)),

    CONSTRAINT ck_merchants_name CHECK ((btrim((name)::text) <> ''::text)),

    CONSTRAINT ck_merchants_pickup_service CHECK ((default_pickup_service_seconds >= 0)),

    CONSTRAINT ck_merchants_preparation_status CHECK (((preparation_status)::text = ANY ((ARRAY['PREPARING'::character varying, 'READY'::character varying, 'DELAYED'::character varying])::text[])))

);


--

-- Name: orders; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.orders (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    order_code character varying(64) NOT NULL,

    business_date date NOT NULL,

    merchant_id uuid NOT NULL,

    customer_id uuid NOT NULL,

    pickup_location_id uuid NOT NULL,

    delivery_location_id uuid NOT NULL,

    pickup_ready_at timestamp with time zone NOT NULL,

    pickup_service_seconds integer DEFAULT 0 NOT NULL,

    delivery_window_start_at timestamp with time zone NOT NULL,

    delivery_window_end_at timestamp with time zone NOT NULL,

    delivery_service_seconds integer DEFAULT 0 NOT NULL,

    demand_load_units integer NOT NULL,

    execution_status character varying(32) DEFAULT 'PLANNED'::character varying NOT NULL,

    risk_status character varying(16) DEFAULT 'NORMAL'::character varying NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_orders_code CHECK ((btrim((order_code)::text) <> ''::text)),

    CONSTRAINT ck_orders_delivery_service CHECK ((delivery_service_seconds >= 0)),

    CONSTRAINT ck_orders_delivery_window CHECK ((delivery_window_end_at >= delivery_window_start_at)),

    CONSTRAINT ck_orders_demand CHECK ((demand_load_units > 0)),

    CONSTRAINT ck_orders_execution_status CHECK (((execution_status)::text = ANY ((ARRAY['PLANNED'::character varying, 'PICKUP_IN_PROGRESS'::character varying, 'PICKED_UP'::character varying, 'DELIVERING'::character varying, 'COMPLETED'::character varying])::text[]))),

    CONSTRAINT ck_orders_pickup_service CHECK ((pickup_service_seconds >= 0)),

    CONSTRAINT ck_orders_risk_status CHECK (((risk_status)::text = ANY ((ARRAY['NORMAL'::character varying, 'AT_RISK'::character varying])::text[])))

);


--

-- Name: recovery_plans; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.recovery_plans (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    recovery_code character varying(64) NOT NULL,

    incident_id uuid NOT NULL,

    attempt_no integer NOT NULL,

    previous_recovery_plan_id uuid,

    base_delivery_plan_id uuid NOT NULL,

    candidate_delivery_plan_id uuid,

    status character varying(24) DEFAULT 'DRAFT'::character varying NOT NULL,

    replanning_scope character varying(24) NOT NULL,

    scope_description text NOT NULL,

    agent_explanation text,

    solver_status character varying(16),

    validation_status character varying(16),

    solver_validation_summary jsonb,

    dispatcher_decision character varying(16),

    decision_reason text,

    reviewed_by character varying(128),

    reviewed_at timestamp with time zone,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_recovery_plans_attempt CHECK ((attempt_no > 0)),

    CONSTRAINT ck_recovery_plans_candidate CHECK (((candidate_delivery_plan_id IS NULL) OR (((solver_status)::text = 'FEASIBLE'::text) AND ((validation_status)::text = 'VALID'::text)))),

    CONSTRAINT ck_recovery_plans_decided CHECK ((((status)::text <> 'DECIDED'::text) OR ((candidate_delivery_plan_id IS NOT NULL) AND (btrim(COALESCE(agent_explanation, ''::text)) <> ''::text) AND ((solver_status)::text = 'FEASIBLE'::text) AND ((validation_status)::text = 'VALID'::text) AND (dispatcher_decision IS NOT NULL) AND (btrim(COALESCE(decision_reason, ''::text)) <> ''::text) AND (reviewed_by IS NOT NULL) AND (reviewed_at IS NOT NULL)))),

    CONSTRAINT ck_recovery_plans_decision CHECK (((dispatcher_decision IS NULL) OR ((dispatcher_decision)::text = ANY ((ARRAY['APPROVE'::character varying, 'REJECT'::character varying, 'MODIFY'::character varying])::text[])))),

    CONSTRAINT ck_recovery_plans_distinct_plan CHECK (((candidate_delivery_plan_id IS NULL) OR (candidate_delivery_plan_id <> base_delivery_plan_id))),

    CONSTRAINT ck_recovery_plans_draft_decision CHECK ((((status)::text <> 'DRAFT'::text) OR ((dispatcher_decision IS NULL) AND (decision_reason IS NULL) AND (reviewed_by IS NULL) AND (reviewed_at IS NULL)))),

    CONSTRAINT ck_recovery_plans_pending_review CHECK ((((status)::text <> 'PENDING_REVIEW'::text) OR ((candidate_delivery_plan_id IS NOT NULL) AND (btrim(COALESCE(agent_explanation, ''::text)) <> ''::text) AND ((solver_status)::text = 'FEASIBLE'::text) AND ((validation_status)::text = 'VALID'::text) AND (dispatcher_decision IS NULL) AND (decision_reason IS NULL) AND (reviewed_by IS NULL) AND (reviewed_at IS NULL)))),

    CONSTRAINT ck_recovery_plans_previous CHECK ((((attempt_no = 1) AND (previous_recovery_plan_id IS NULL)) OR ((attempt_no > 1) AND (previous_recovery_plan_id IS NOT NULL)))),

    CONSTRAINT ck_recovery_plans_scope CHECK (((replanning_scope)::text = ANY ((ARRAY['AFFECTED_ROUTE'::character varying, 'CROSS_ROUTE'::character varying, 'ALL_REMAINING'::character varying])::text[]))),

    CONSTRAINT ck_recovery_plans_solver CHECK (((solver_status IS NULL) OR ((solver_status)::text = ANY ((ARRAY['FEASIBLE'::character varying, 'INFEASIBLE'::character varying, 'ERROR'::character varying])::text[])))),

    CONSTRAINT ck_recovery_plans_solver_outcome CHECK ((((solver_status IS NULL) AND (validation_status IS NULL) AND (candidate_delivery_plan_id IS NULL)) OR (((solver_status)::text = ANY ((ARRAY['INFEASIBLE'::character varying, 'ERROR'::character varying])::text[])) AND (validation_status IS NULL) AND (candidate_delivery_plan_id IS NULL) AND ((status)::text = 'DRAFT'::text)) OR (((solver_status)::text = 'FEASIBLE'::text) AND ((validation_status)::text = ANY ((ARRAY['PENDING'::character varying, 'INVALID'::character varying])::text[])) AND (candidate_delivery_plan_id IS NULL)) OR (((solver_status)::text = 'FEASIBLE'::text) AND ((validation_status)::text = 'VALID'::text)))),

    CONSTRAINT ck_recovery_plans_status CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'PENDING_REVIEW'::character varying, 'DECIDED'::character varying])::text[]))),

    CONSTRAINT ck_recovery_plans_validation CHECK (((validation_status IS NULL) OR ((validation_status)::text = ANY ((ARRAY['PENDING'::character varying, 'VALID'::character varying, 'INVALID'::character varying])::text[]))))

);


--

-- Name: route_stops; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.route_stops (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    vehicle_route_id uuid NOT NULL,

    order_id uuid NOT NULL,

    location_id uuid NOT NULL,

    stop_type character varying(16) NOT NULL,

    sequence_no integer NOT NULL,

    precedence_stop_id uuid,

    source_incident_id uuid,

    planned_arrival_at timestamp with time zone NOT NULL,

    planned_departure_at timestamp with time zone NOT NULL,

    actual_arrival_at timestamp with time zone,

    actual_departure_at timestamp with time zone,

    service_seconds integer DEFAULT 0 NOT NULL,

    time_window_start_at timestamp with time zone,

    time_window_end_at timestamp with time zone,

    demand_load_units_snapshot integer NOT NULL,

    status character varying(16) DEFAULT 'PLANNED'::character varying NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_route_stops_actual_time CHECK (((actual_departure_at IS NULL) OR ((actual_arrival_at IS NOT NULL) AND (actual_departure_at >= actual_arrival_at)))),

    CONSTRAINT ck_route_stops_demand CHECK ((demand_load_units_snapshot > 0)),

    CONSTRAINT ck_route_stops_handover_incident CHECK (((((stop_type)::text = 'HANDOVER'::text) AND (source_incident_id IS NOT NULL)) OR (((stop_type)::text <> 'HANDOVER'::text) AND (source_incident_id IS NULL)))),

    CONSTRAINT ck_route_stops_planned_time CHECK ((planned_departure_at >= planned_arrival_at)),

    CONSTRAINT ck_route_stops_precedence CHECK (((((stop_type)::text = 'DELIVERY'::text) AND (precedence_stop_id IS NOT NULL)) OR (((stop_type)::text <> 'DELIVERY'::text) AND (precedence_stop_id IS NULL)))),

    CONSTRAINT ck_route_stops_sequence CHECK ((sequence_no > 0)),

    CONSTRAINT ck_route_stops_service CHECK ((service_seconds >= 0)),

    CONSTRAINT ck_route_stops_status CHECK (((status)::text = ANY ((ARRAY['PLANNED'::character varying, 'ARRIVED'::character varying, 'IN_SERVICE'::character varying, 'COMPLETED'::character varying])::text[]))),

    CONSTRAINT ck_route_stops_type CHECK (((stop_type)::text = ANY ((ARRAY['PICKUP'::character varying, 'DELIVERY'::character varying, 'HANDOVER'::character varying])::text[]))),

    CONSTRAINT ck_route_stops_window CHECK (((time_window_end_at IS NULL) OR ((time_window_start_at IS NOT NULL) AND (time_window_end_at >= time_window_start_at))))

);


--

-- Name: vehicle_driver_assignments; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.vehicle_driver_assignments (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    vehicle_id uuid NOT NULL,

    driver_id uuid NOT NULL,

    assigned_from_at timestamp with time zone NOT NULL,

    assigned_until_at timestamp with time zone,

    status character varying(16) DEFAULT 'PLANNED'::character varying NOT NULL,

    activated_at timestamp with time zone,

    ended_at timestamp with time zone,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_assignments_active_time CHECK ((((status)::text <> 'ACTIVE'::text) OR (activated_at IS NOT NULL))),

    CONSTRAINT ck_assignments_ended_time CHECK ((((status)::text <> 'ENDED'::text) OR ((ended_at IS NOT NULL) AND ((activated_at IS NULL) OR (ended_at >= activated_at))))),

    CONSTRAINT ck_assignments_status CHECK (((status)::text = ANY ((ARRAY['PLANNED'::character varying, 'ACTIVE'::character varying, 'ENDED'::character varying, 'CANCELLED'::character varying])::text[]))),

    CONSTRAINT ck_assignments_window CHECK (((assigned_until_at IS NULL) OR (assigned_until_at > assigned_from_at)))

);


--

-- Name: vehicle_routes; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.vehicle_routes (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    delivery_plan_id uuid NOT NULL,

    route_no integer NOT NULL,

    vehicle_id uuid NOT NULL,

    driver_id uuid NOT NULL,

    vehicle_driver_assignment_id uuid NOT NULL,

    start_location_id uuid NOT NULL,

    end_location_id uuid NOT NULL,

    status character varying(16) DEFAULT 'PLANNED'::character varying NOT NULL,

    planned_start_at timestamp with time zone NOT NULL,

    planned_end_at timestamp with time zone NOT NULL,

    actual_start_at timestamp with time zone,

    actual_end_at timestamp with time zone,

    distance_meters bigint DEFAULT 0 NOT NULL,

    duration_seconds bigint DEFAULT 0 NOT NULL,

    vehicle_capacity_load_units_snapshot integer NOT NULL,

    route_geometry jsonb,

    route_metrics jsonb,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_vehicle_routes_actual_window CHECK (((actual_end_at IS NULL) OR ((actual_start_at IS NOT NULL) AND (actual_end_at >= actual_start_at)))),

    CONSTRAINT ck_vehicle_routes_capacity CHECK ((vehicle_capacity_load_units_snapshot > 0)),

    CONSTRAINT ck_vehicle_routes_metrics CHECK (((distance_meters >= 0) AND (duration_seconds >= 0))),

    CONSTRAINT ck_vehicle_routes_planned_window CHECK ((planned_end_at >= planned_start_at)),

    CONSTRAINT ck_vehicle_routes_route_no CHECK ((route_no > 0)),

    CONSTRAINT ck_vehicle_routes_status CHECK (((status)::text = ANY ((ARRAY['PLANNED'::character varying, 'ACTIVE'::character varying, 'COMPLETED'::character varying, 'CANCELLED'::character varying])::text[])))

);


--

-- Name: vehicles; Type: TABLE; Schema: public; Owner: -

--


CREATE TABLE public.vehicles (

    id uuid DEFAULT gen_random_uuid() NOT NULL,

    vehicle_code character varying(64) NOT NULL,

    name character varying(160) NOT NULL,

    capacity_load_units integer NOT NULL,

    status character varying(16) DEFAULT 'AVAILABLE'::character varying NOT NULL,

    current_location_id uuid NOT NULL,

    current_location_recorded_at timestamp with time zone NOT NULL,

    created_at timestamp with time zone DEFAULT now() NOT NULL,

    updated_at timestamp with time zone DEFAULT now() NOT NULL,

    CONSTRAINT ck_vehicles_capacity CHECK ((capacity_load_units > 0)),

    CONSTRAINT ck_vehicles_code CHECK ((btrim((vehicle_code)::text) <> ''::text)),

    CONSTRAINT ck_vehicles_name CHECK ((btrim((name)::text) <> ''::text)),

    CONSTRAINT ck_vehicles_status CHECK (((status)::text = ANY ((ARRAY['AVAILABLE'::character varying, 'ACTIVE'::character varying, 'UNAVAILABLE'::character varying])::text[])))

);


--

-- Name: vehicle_driver_assignments excl_assignments_driver_time; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_driver_assignments

    ADD CONSTRAINT excl_assignments_driver_time EXCLUDE USING gist (driver_id WITH =, tstzrange(assigned_from_at, assigned_until_at, '[)'::text) WITH &&) WHERE (((status)::text = ANY ((ARRAY['PLANNED'::character varying, 'ACTIVE'::character varying])::text[])));


--

-- Name: vehicle_driver_assignments excl_assignments_vehicle_time; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_driver_assignments

    ADD CONSTRAINT excl_assignments_vehicle_time EXCLUDE USING gist (vehicle_id WITH =, tstzrange(assigned_from_at, assigned_until_at, '[)'::text) WITH &&) WHERE (((status)::text = ANY ((ARRAY['PLANNED'::character varying, 'ACTIVE'::character varying])::text[])));


--

-- Name: customers pk_customers; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.customers

    ADD CONSTRAINT pk_customers PRIMARY KEY (id);


--

-- Name: delivery_plan_orders pk_delivery_plan_orders; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plan_orders

    ADD CONSTRAINT pk_delivery_plan_orders PRIMARY KEY (id);


--

-- Name: delivery_plans pk_delivery_plans; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plans

    ADD CONSTRAINT pk_delivery_plans PRIMARY KEY (id);


--

-- Name: drivers pk_drivers; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.drivers

    ADD CONSTRAINT pk_drivers PRIMARY KEY (id);


--

-- Name: incident_affected_orders pk_incident_affected_orders; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incident_affected_orders

    ADD CONSTRAINT pk_incident_affected_orders PRIMARY KEY (id);


--

-- Name: incidents pk_incidents; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT pk_incidents PRIMARY KEY (id);


--

-- Name: locations pk_locations; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.locations

    ADD CONSTRAINT pk_locations PRIMARY KEY (id);


--

-- Name: merchants pk_merchants; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.merchants

    ADD CONSTRAINT pk_merchants PRIMARY KEY (id);


--

-- Name: orders pk_orders; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.orders

    ADD CONSTRAINT pk_orders PRIMARY KEY (id);


--

-- Name: recovery_plans pk_recovery_plans; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT pk_recovery_plans PRIMARY KEY (id);


--

-- Name: route_stops pk_route_stops; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT pk_route_stops PRIMARY KEY (id);


--

-- Name: vehicle_driver_assignments pk_vehicle_driver_assignments; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_driver_assignments

    ADD CONSTRAINT pk_vehicle_driver_assignments PRIMARY KEY (id);


--

-- Name: vehicle_routes pk_vehicle_routes; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT pk_vehicle_routes PRIMARY KEY (id);


--

-- Name: vehicles pk_vehicles; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicles

    ADD CONSTRAINT pk_vehicles PRIMARY KEY (id);


--

-- Name: vehicle_driver_assignments uq_assignments_identity; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_driver_assignments

    ADD CONSTRAINT uq_assignments_identity UNIQUE (id, vehicle_id, driver_id);


--

-- Name: customers uq_customers_customer_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.customers

    ADD CONSTRAINT uq_customers_customer_code UNIQUE (customer_code);


--

-- Name: delivery_plan_orders uq_delivery_plan_orders_plan_order; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plan_orders

    ADD CONSTRAINT uq_delivery_plan_orders_plan_order UNIQUE (delivery_plan_id, order_id);


--

-- Name: delivery_plans uq_delivery_plans_group_version; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plans

    ADD CONSTRAINT uq_delivery_plans_group_version UNIQUE (plan_group_id, version_no);


--

-- Name: delivery_plans uq_delivery_plans_plan_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plans

    ADD CONSTRAINT uq_delivery_plans_plan_code UNIQUE (plan_code);


--

-- Name: drivers uq_drivers_driver_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.drivers

    ADD CONSTRAINT uq_drivers_driver_code UNIQUE (driver_code);


--

-- Name: incident_affected_orders uq_incident_affected_orders_incident_order; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incident_affected_orders

    ADD CONSTRAINT uq_incident_affected_orders_incident_order UNIQUE (incident_id, order_id);


--

-- Name: incidents uq_incidents_incident_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT uq_incidents_incident_code UNIQUE (incident_code);


--

-- Name: locations uq_locations_location_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.locations

    ADD CONSTRAINT uq_locations_location_code UNIQUE (location_code);


--

-- Name: merchants uq_merchants_merchant_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.merchants

    ADD CONSTRAINT uq_merchants_merchant_code UNIQUE (merchant_code);


--

-- Name: orders uq_orders_order_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.orders

    ADD CONSTRAINT uq_orders_order_code UNIQUE (order_code);


--

-- Name: recovery_plans uq_recovery_plans_candidate_plan; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT uq_recovery_plans_candidate_plan UNIQUE (candidate_delivery_plan_id);


--

-- Name: recovery_plans uq_recovery_plans_incident_attempt; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT uq_recovery_plans_incident_attempt UNIQUE (incident_id, attempt_no);


--

-- Name: recovery_plans uq_recovery_plans_recovery_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT uq_recovery_plans_recovery_code UNIQUE (recovery_code);


--

-- Name: route_stops uq_route_stops_route_order_type; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT uq_route_stops_route_order_type UNIQUE (vehicle_route_id, order_id, stop_type);


--

-- Name: route_stops uq_route_stops_route_sequence; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT uq_route_stops_route_sequence UNIQUE (vehicle_route_id, sequence_no);


--

-- Name: vehicle_routes uq_vehicle_routes_plan_assignment; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT uq_vehicle_routes_plan_assignment UNIQUE (delivery_plan_id, vehicle_driver_assignment_id);


--

-- Name: vehicle_routes uq_vehicle_routes_plan_identity; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT uq_vehicle_routes_plan_identity UNIQUE (id, delivery_plan_id);


--

-- Name: vehicle_routes uq_vehicle_routes_plan_route_no; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT uq_vehicle_routes_plan_route_no UNIQUE (delivery_plan_id, route_no);


--

-- Name: vehicle_routes uq_vehicle_routes_plan_vehicle; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT uq_vehicle_routes_plan_vehicle UNIQUE (delivery_plan_id, vehicle_id);


--

-- Name: vehicles uq_vehicles_vehicle_code; Type: CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicles

    ADD CONSTRAINT uq_vehicles_vehicle_code UNIQUE (vehicle_code);


--

-- Name: ix_affected_orders_handover; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_affected_orders_handover ON public.incident_affected_orders USING btree (incident_id, handover_required) WHERE handover_required;


--

-- Name: ix_affected_orders_incident_replanning; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_affected_orders_incident_replanning ON public.incident_affected_orders USING btree (incident_id, requires_replanning);


--

-- Name: ix_affected_orders_order_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_affected_orders_order_id ON public.incident_affected_orders USING btree (order_id);


--

-- Name: ix_assignments_driver_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_assignments_driver_status ON public.vehicle_driver_assignments USING btree (driver_id, status);


--

-- Name: ix_assignments_vehicle_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_assignments_vehicle_status ON public.vehicle_driver_assignments USING btree (vehicle_id, status);


--

-- Name: ix_customers_default_delivery_location_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_customers_default_delivery_location_id ON public.customers USING btree (default_delivery_location_id);


--

-- Name: ix_delivery_plan_orders_order_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_delivery_plan_orders_order_id ON public.delivery_plan_orders USING btree (order_id);


--

-- Name: ix_delivery_plan_orders_plan_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_delivery_plan_orders_plan_status ON public.delivery_plan_orders USING btree (delivery_plan_id, assignment_status);


--

-- Name: ix_delivery_plans_business_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_delivery_plans_business_status ON public.delivery_plans USING btree (business_date, status);


--

-- Name: ix_delivery_plans_parent_plan_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_delivery_plans_parent_plan_id ON public.delivery_plans USING btree (parent_plan_id);


--

-- Name: ix_drivers_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_drivers_status ON public.drivers USING btree (status);


--

-- Name: ix_incidents_material_merchant_delay; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_incidents_material_merchant_delay ON public.incidents USING btree (merchant_id, detected_at DESC) WHERE (((incident_type)::text = 'MERCHANT_DELAY'::text) AND (delay_seconds > 600));


--

-- Name: ix_incidents_merchant; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_incidents_merchant ON public.incidents USING btree (merchant_id) WHERE (merchant_id IS NOT NULL);


--

-- Name: ix_incidents_plan_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_incidents_plan_status ON public.incidents USING btree (delivery_plan_id, status);


--

-- Name: ix_incidents_type_status_detected; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_incidents_type_status_detected ON public.incidents USING btree (incident_type, status, detected_at DESC);


--

-- Name: ix_incidents_vehicle; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_incidents_vehicle ON public.incidents USING btree (vehicle_id) WHERE (vehicle_id IS NOT NULL);


--

-- Name: ix_merchants_pickup_location_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_merchants_pickup_location_id ON public.merchants USING btree (pickup_location_id);


--

-- Name: ix_merchants_preparation_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_merchants_preparation_status ON public.merchants USING btree (preparation_status);


--

-- Name: ix_orders_business_execution; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_orders_business_execution ON public.orders USING btree (business_date, execution_status);


--

-- Name: ix_orders_business_risk; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_orders_business_risk ON public.orders USING btree (business_date, risk_status);


--

-- Name: ix_orders_customer_business; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_orders_customer_business ON public.orders USING btree (customer_id, business_date);


--

-- Name: ix_orders_merchant_business; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_orders_merchant_business ON public.orders USING btree (merchant_id, business_date);


--

-- Name: ix_recovery_plans_base_delivery_plan_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_recovery_plans_base_delivery_plan_id ON public.recovery_plans USING btree (base_delivery_plan_id);


--

-- Name: ix_recovery_plans_incident_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_recovery_plans_incident_status ON public.recovery_plans USING btree (incident_id, status);


--

-- Name: ix_route_stops_order_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_route_stops_order_id ON public.route_stops USING btree (order_id);


--

-- Name: ix_route_stops_precedence_stop_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_route_stops_precedence_stop_id ON public.route_stops USING btree (precedence_stop_id);


--

-- Name: ix_route_stops_route_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_route_stops_route_status ON public.route_stops USING btree (vehicle_route_id, status);


--

-- Name: ix_route_stops_source_incident_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_route_stops_source_incident_id ON public.route_stops USING btree (source_incident_id);


--

-- Name: ix_vehicle_routes_driver_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_vehicle_routes_driver_status ON public.vehicle_routes USING btree (driver_id, status);


--

-- Name: ix_vehicle_routes_vehicle_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_vehicle_routes_vehicle_status ON public.vehicle_routes USING btree (vehicle_id, status);


--

-- Name: ix_vehicles_current_location_id; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_vehicles_current_location_id ON public.vehicles USING btree (current_location_id);


--

-- Name: ix_vehicles_status; Type: INDEX; Schema: public; Owner: -

--


CREATE INDEX ix_vehicles_status ON public.vehicles USING btree (status);


--

-- Name: uq_delivery_plans_current_business_date; Type: INDEX; Schema: public; Owner: -

--


CREATE UNIQUE INDEX uq_delivery_plans_current_business_date ON public.delivery_plans USING btree (business_date) WHERE ((status)::text = 'CURRENT'::text);


--

-- Name: uq_delivery_plans_current_group; Type: INDEX; Schema: public; Owner: -

--


CREATE UNIQUE INDEX uq_delivery_plans_current_group ON public.delivery_plans USING btree (plan_group_id) WHERE ((status)::text = 'CURRENT'::text);


--

-- Name: uq_incidents_open_merchant; Type: INDEX; Schema: public; Owner: -

--


CREATE UNIQUE INDEX uq_incidents_open_merchant ON public.incidents USING btree (delivery_plan_id, merchant_id, incident_type) WHERE (((incident_type)::text = 'MERCHANT_DELAY'::text) AND ((status)::text <> 'RESOLVED'::text));


--

-- Name: uq_incidents_open_vehicle; Type: INDEX; Schema: public; Owner: -

--


CREATE UNIQUE INDEX uq_incidents_open_vehicle ON public.incidents USING btree (delivery_plan_id, vehicle_id, incident_type) WHERE (((incident_type)::text = 'VEHICLE_UNAVAILABLE'::text) AND ((status)::text <> 'RESOLVED'::text));


--

-- Name: orders trg_orders_prevent_completed_regression; Type: TRIGGER; Schema: public; Owner: -

--


CREATE TRIGGER trg_orders_prevent_completed_regression BEFORE UPDATE OF execution_status ON public.orders FOR EACH ROW EXECUTE FUNCTION public.prevent_completed_order_regression();


--

-- Name: route_stops trg_route_stops_prevent_completed_regression; Type: TRIGGER; Schema: public; Owner: -

--


CREATE TRIGGER trg_route_stops_prevent_completed_regression BEFORE UPDATE OF status ON public.route_stops FOR EACH ROW EXECUTE FUNCTION public.prevent_completed_stop_regression();


--

-- Name: route_stops trg_route_stops_validate_relationships; Type: TRIGGER; Schema: public; Owner: -

--


CREATE CONSTRAINT TRIGGER trg_route_stops_validate_relationships AFTER INSERT OR UPDATE ON public.route_stops DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.validate_route_stop_relationships();


--

-- Name: customers customers_default_delivery_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.customers

    ADD CONSTRAINT customers_default_delivery_location_id_fkey FOREIGN KEY (default_delivery_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: delivery_plan_orders delivery_plan_orders_delivery_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plan_orders

    ADD CONSTRAINT delivery_plan_orders_delivery_plan_id_fkey FOREIGN KEY (delivery_plan_id) REFERENCES public.delivery_plans(id) ON DELETE CASCADE;


--

-- Name: delivery_plan_orders delivery_plan_orders_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plan_orders

    ADD CONSTRAINT delivery_plan_orders_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE RESTRICT;


--

-- Name: delivery_plans delivery_plans_parent_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plans

    ADD CONSTRAINT delivery_plans_parent_plan_id_fkey FOREIGN KEY (parent_plan_id) REFERENCES public.delivery_plans(id) ON DELETE RESTRICT;


--

-- Name: delivery_plan_orders fk_delivery_plan_orders_route; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.delivery_plan_orders

    ADD CONSTRAINT fk_delivery_plan_orders_route FOREIGN KEY (vehicle_route_id, delivery_plan_id) REFERENCES public.vehicle_routes(id, delivery_plan_id) ON DELETE CASCADE;


--

-- Name: vehicle_routes fk_vehicle_routes_assignment; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT fk_vehicle_routes_assignment FOREIGN KEY (vehicle_driver_assignment_id, vehicle_id, driver_id) REFERENCES public.vehicle_driver_assignments(id, vehicle_id, driver_id) ON DELETE RESTRICT;


--

-- Name: incident_affected_orders incident_affected_orders_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incident_affected_orders

    ADD CONSTRAINT incident_affected_orders_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--

-- Name: incident_affected_orders incident_affected_orders_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incident_affected_orders

    ADD CONSTRAINT incident_affected_orders_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE RESTRICT;


--

-- Name: incident_affected_orders incident_affected_orders_original_vehicle_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incident_affected_orders

    ADD CONSTRAINT incident_affected_orders_original_vehicle_route_id_fkey FOREIGN KEY (original_vehicle_route_id) REFERENCES public.vehicle_routes(id) ON DELETE RESTRICT;


--

-- Name: incidents incidents_delivery_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT incidents_delivery_plan_id_fkey FOREIGN KEY (delivery_plan_id) REFERENCES public.delivery_plans(id) ON DELETE RESTRICT;


--

-- Name: incidents incidents_incident_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT incidents_incident_location_id_fkey FOREIGN KEY (incident_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: incidents incidents_merchant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT incidents_merchant_id_fkey FOREIGN KEY (merchant_id) REFERENCES public.merchants(id) ON DELETE RESTRICT;


--

-- Name: incidents incidents_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT incidents_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id) ON DELETE RESTRICT;


--

-- Name: incidents incidents_vehicle_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.incidents

    ADD CONSTRAINT incidents_vehicle_route_id_fkey FOREIGN KEY (vehicle_route_id) REFERENCES public.vehicle_routes(id) ON DELETE RESTRICT;


--

-- Name: merchants merchants_pickup_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.merchants

    ADD CONSTRAINT merchants_pickup_location_id_fkey FOREIGN KEY (pickup_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: orders orders_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.orders

    ADD CONSTRAINT orders_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE RESTRICT;


--

-- Name: orders orders_delivery_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.orders

    ADD CONSTRAINT orders_delivery_location_id_fkey FOREIGN KEY (delivery_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: orders orders_merchant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.orders

    ADD CONSTRAINT orders_merchant_id_fkey FOREIGN KEY (merchant_id) REFERENCES public.merchants(id) ON DELETE RESTRICT;


--

-- Name: orders orders_pickup_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.orders

    ADD CONSTRAINT orders_pickup_location_id_fkey FOREIGN KEY (pickup_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: recovery_plans recovery_plans_base_delivery_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT recovery_plans_base_delivery_plan_id_fkey FOREIGN KEY (base_delivery_plan_id) REFERENCES public.delivery_plans(id) ON DELETE RESTRICT;


--

-- Name: recovery_plans recovery_plans_candidate_delivery_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT recovery_plans_candidate_delivery_plan_id_fkey FOREIGN KEY (candidate_delivery_plan_id) REFERENCES public.delivery_plans(id) ON DELETE RESTRICT;


--

-- Name: recovery_plans recovery_plans_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT recovery_plans_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE RESTRICT;


--

-- Name: recovery_plans recovery_plans_previous_recovery_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.recovery_plans

    ADD CONSTRAINT recovery_plans_previous_recovery_plan_id_fkey FOREIGN KEY (previous_recovery_plan_id) REFERENCES public.recovery_plans(id) ON DELETE RESTRICT;


--

-- Name: route_stops route_stops_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT route_stops_location_id_fkey FOREIGN KEY (location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: route_stops route_stops_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT route_stops_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE RESTRICT;


--

-- Name: route_stops route_stops_precedence_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT route_stops_precedence_stop_id_fkey FOREIGN KEY (precedence_stop_id) REFERENCES public.route_stops(id) ON DELETE RESTRICT;


--

-- Name: route_stops route_stops_source_incident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT route_stops_source_incident_id_fkey FOREIGN KEY (source_incident_id) REFERENCES public.incidents(id) ON DELETE RESTRICT;


--

-- Name: route_stops route_stops_vehicle_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.route_stops

    ADD CONSTRAINT route_stops_vehicle_route_id_fkey FOREIGN KEY (vehicle_route_id) REFERENCES public.vehicle_routes(id) ON DELETE CASCADE;


--

-- Name: vehicle_driver_assignments vehicle_driver_assignments_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_driver_assignments

    ADD CONSTRAINT vehicle_driver_assignments_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE RESTRICT;


--

-- Name: vehicle_driver_assignments vehicle_driver_assignments_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_driver_assignments

    ADD CONSTRAINT vehicle_driver_assignments_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id) ON DELETE RESTRICT;


--

-- Name: vehicle_routes vehicle_routes_delivery_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT vehicle_routes_delivery_plan_id_fkey FOREIGN KEY (delivery_plan_id) REFERENCES public.delivery_plans(id) ON DELETE CASCADE;


--

-- Name: vehicle_routes vehicle_routes_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT vehicle_routes_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE RESTRICT;


--

-- Name: vehicle_routes vehicle_routes_end_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT vehicle_routes_end_location_id_fkey FOREIGN KEY (end_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: vehicle_routes vehicle_routes_start_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT vehicle_routes_start_location_id_fkey FOREIGN KEY (start_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- Name: vehicle_routes vehicle_routes_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicle_routes

    ADD CONSTRAINT vehicle_routes_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id) ON DELETE RESTRICT;


--

-- Name: vehicles vehicles_current_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

--


ALTER TABLE ONLY public.vehicles

    ADD CONSTRAINT vehicles_current_location_id_fkey FOREIGN KEY (current_location_id) REFERENCES public.locations(id) ON DELETE RESTRICT;


--

-- PostgreSQL database dump complete

--
