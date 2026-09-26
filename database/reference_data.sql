-- ============================================================
-- PenroseRoute - Reference / Demo Data
-- Target database: penrose_route
-- Prerequisite: run create_datatable.sql first on a fresh database
--
-- Demo scenario:
--   - 8 locations
--   - 2 merchants
--   - 4 customers
--   - 6 orders
--   - 3 vehicles + 3 drivers + 3 assignments
--   - 1 current delivery plan + 1 replanned candidate
--   - 5 vehicle routes
--   - 2 incidents (vehicle unavailable + merchant delay)
--   - plan/order assignments
--   - route stops including a HANDOVER stop
--   - incident affected orders
--   - 2 recovery plans
--   - 1 active P1 risk alert + 1 change event
--
-- Intended for a fresh/demo database.
-- ============================================================

SET search_path TO public;

BEGIN;

-- ============================================================
-- 1. LOCATIONS
-- ============================================================
INSERT INTO locations (
    id, location_code, display_name, address_text, latitude, longitude
) VALUES
('10000000-0000-0000-0000-000000000001', 'LOC-HUB-001', 'PenroseRoute West Hub', 'Jurong West, Singapore', 1.339700, 103.706600),
('10000000-0000-0000-0000-000000000002', 'LOC-MER-001', 'Jurong Merchant Pickup', 'Jurong East, Singapore', 1.332900, 103.743600),
('10000000-0000-0000-0000-000000000003', 'LOC-MER-002', 'Clementi Merchant Pickup', 'Clementi, Singapore', 1.315100, 103.765100),
('10000000-0000-0000-0000-000000000004', 'LOC-CUS-001', 'Customer A Delivery Point', 'Bukit Batok, Singapore', 1.349600, 103.749000),
('10000000-0000-0000-0000-000000000005', 'LOC-CUS-002', 'Customer B Delivery Point', 'Choa Chu Kang, Singapore', 1.385400, 103.744300),
('10000000-0000-0000-0000-000000000006', 'LOC-CUS-003', 'Customer C Delivery Point', 'Queenstown, Singapore', 1.294200, 103.786100),
('10000000-0000-0000-0000-000000000007', 'LOC-CUS-004', 'Customer D Delivery Point', 'Bukit Panjang, Singapore', 1.377400, 103.771900),
('10000000-0000-0000-0000-000000000008', 'LOC-INC-001', 'Vehicle Breakdown Point', 'West Coast Road, Singapore', 1.303900, 103.759600);

-- ============================================================
-- 2. MERCHANTS
-- ============================================================
INSERT INTO merchants (
    id, merchant_code, name, pickup_location_id,
    preparation_status, operational_ready_at, default_pickup_service_seconds
) VALUES
(
    '20000000-0000-0000-0000-000000000001',
    'MER-001',
    'Jurong Fresh Kitchen',
    '10000000-0000-0000-0000-000000000002',
    'READY',
    '2026-09-25 09:00:00+08',
    300
),
(
    '20000000-0000-0000-0000-000000000002',
    'MER-002',
    'Clementi Daily Goods',
    '10000000-0000-0000-0000-000000000003',
    'DELAYED',
    '2026-09-25 09:25:00+08',
    300
);

-- ============================================================
-- 3. CUSTOMERS
-- ============================================================
INSERT INTO customers (
    id, customer_code, name, default_delivery_location_id
) VALUES
('30000000-0000-0000-0000-000000000001', 'CUS-001', 'Customer A', '10000000-0000-0000-0000-000000000004'),
('30000000-0000-0000-0000-000000000002', 'CUS-002', 'Customer B', '10000000-0000-0000-0000-000000000005'),
('30000000-0000-0000-0000-000000000003', 'CUS-003', 'Customer C', '10000000-0000-0000-0000-000000000006'),
('30000000-0000-0000-0000-000000000004', 'CUS-004', 'Customer D', '10000000-0000-0000-0000-000000000007');

-- ============================================================
-- 4. ORDERS
-- ============================================================
INSERT INTO orders (
    id, order_code, business_date,
    merchant_id, customer_id,
    pickup_location_id, delivery_location_id,
    pickup_ready_at, pickup_service_seconds,
    delivery_window_start_at, delivery_window_end_at,
    delivery_service_seconds, demand_load_units,
    execution_status, risk_status
) VALUES
(
    '40000000-0000-0000-0000-000000000001',
    'ORD-20260925-001',
    '2026-09-25',
    '20000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000004',
    '2026-09-25 09:00:00+08',
    300,
    '2026-09-25 09:30:00+08',
    '2026-09-25 10:30:00+08',
    300,
    2,
    'COMPLETED',
    'NORMAL'
),
(
    '40000000-0000-0000-0000-000000000002',
    'ORD-20260925-002',
    '2026-09-25',
    '20000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000005',
    '2026-09-25 09:00:00+08',
    300,
    '2026-09-25 10:00:00+08',
    '2026-09-25 11:30:00+08',
    300,
    1,
    'PICKED_UP',
    'AT_RISK'
),
(
    '40000000-0000-0000-0000-000000000003',
    'ORD-20260925-003',
    '2026-09-25',
    '20000000-0000-0000-0000-000000000002',
    '30000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000006',
    '2026-09-25 09:10:00+08',
    300,
    '2026-09-25 10:15:00+08',
    '2026-09-25 11:30:00+08',
    300,
    2,
    'PLANNED',
    'AT_RISK'
),
(
    '40000000-0000-0000-0000-000000000004',
    'ORD-20260925-004',
    '2026-09-25',
    '20000000-0000-0000-0000-000000000002',
    '30000000-0000-0000-0000-000000000004',
    '10000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000007',
    '2026-09-25 09:10:00+08',
    300,
    '2026-09-25 11:00:00+08',
    '2026-09-25 12:00:00+08',
    300,
    1,
    'PLANNED',
    'AT_RISK'
),
(
    '40000000-0000-0000-0000-000000000005',
    'ORD-20260925-005',
    '2026-09-25',
    '20000000-0000-0000-0000-000000000001',
    '30000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000006',
    '2026-09-25 09:15:00+08',
    300,
    '2026-09-25 11:00:00+08',
    '2026-09-25 12:30:00+08',
    300,
    2,
    'PLANNED',
    'NORMAL'
),
(
    '40000000-0000-0000-0000-000000000006',
    'ORD-20260925-006',
    '2026-09-25',
    '20000000-0000-0000-0000-000000000002',
    '30000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000005',
    '2026-09-25 09:15:00+08',
    300,
    '2026-09-25 11:30:00+08',
    '2026-09-25 13:00:00+08',
    300,
    1,
    'PLANNED',
    'NORMAL'
);

-- ============================================================
-- 5. VEHICLES
-- ============================================================
INSERT INTO vehicles (
    id, vehicle_code, name, capacity_load_units,
    status, current_location_id, current_location_recorded_at
) VALUES
(
    '50000000-0000-0000-0000-000000000001',
    'VEH-001',
    'Van Alpha',
    4,
    'UNAVAILABLE',
    '10000000-0000-0000-0000-000000000008',
    '2026-09-25 10:05:00+08'
),
(
    '50000000-0000-0000-0000-000000000002',
    'VEH-002',
    'Van Bravo',
    5,
    'ACTIVE',
    '10000000-0000-0000-0000-000000000003',
    '2026-09-25 09:25:00+08'
),
(
    '50000000-0000-0000-0000-000000000003',
    'VEH-003',
    'Van Charlie',
    4,
    'AVAILABLE',
    '10000000-0000-0000-0000-000000000001',
    '2026-09-25 10:05:00+08'
);

-- ============================================================
-- 6. DRIVERS
-- ============================================================
INSERT INTO drivers (
    id, driver_code, name, status
) VALUES
('60000000-0000-0000-0000-000000000001', 'DRV-001', 'Driver Alex', 'ACTIVE'),
('60000000-0000-0000-0000-000000000002', 'DRV-002', 'Driver Ben', 'ACTIVE'),
('60000000-0000-0000-0000-000000000003', 'DRV-003', 'Driver Chris', 'AVAILABLE');

-- ============================================================
-- 7. VEHICLE / DRIVER ASSIGNMENTS
-- ============================================================
INSERT INTO vehicle_driver_assignments (
    id, vehicle_id, driver_id,
    assigned_from_at, assigned_until_at,
    status, activated_at
) VALUES
(
    '70000000-0000-0000-0000-000000000001',
    '50000000-0000-0000-0000-000000000001',
    '60000000-0000-0000-0000-000000000001',
    '2026-09-25 08:00:00+08',
    '2026-09-25 18:00:00+08',
    'ACTIVE',
    '2026-09-25 08:00:00+08'
),
(
    '70000000-0000-0000-0000-000000000002',
    '50000000-0000-0000-0000-000000000002',
    '60000000-0000-0000-0000-000000000002',
    '2026-09-25 08:00:00+08',
    '2026-09-25 18:00:00+08',
    'ACTIVE',
    '2026-09-25 08:00:00+08'
),
(
    '70000000-0000-0000-0000-000000000003',
    '50000000-0000-0000-0000-000000000003',
    '60000000-0000-0000-0000-000000000003',
    '2026-09-25 08:00:00+08',
    '2026-09-25 18:00:00+08',
    'PLANNED',
    NULL
);

-- ============================================================
-- 8. DELIVERY PLANS
-- ============================================================
INSERT INTO delivery_plans (
    id, plan_code, plan_group_id, business_date, version_no,
    parent_plan_id, status, solver_engine, validation_status,
    total_distance_meters, total_duration_seconds,
    vehicle_count, assigned_order_count, unassigned_order_count,
    validation_summary, activated_at, superseded_at, created_by
) VALUES
(
    '80000000-0000-0000-0000-000000000001',
    'PLAN-20260925-V1',
    '8f000000-0000-0000-0000-000000000001',
    '2026-09-25',
    1,
    NULL,
    'CURRENT',
    'OR_TOOLS',
    'VALID',
    42800,
    12600,
    2,
    5,
    1,
    '{"feasible": true, "capacity_ok": true, "time_windows_ok": true, "note": "Initial morning plan"}'::jsonb,
    '2026-09-25 08:50:00+08',
    NULL,
    'dispatcher_demo'
),
(
    '80000000-0000-0000-0000-000000000002',
    'PLAN-20260925-V2',
    '8f000000-0000-0000-0000-000000000001',
    '2026-09-25',
    2,
    '80000000-0000-0000-0000-000000000001',
    'CANDIDATE',
    'OR_TOOLS',
    'VALID',
    46100,
    13500,
    3,
    5,
    1,
    '{"feasible": true, "capacity_ok": true, "time_windows_ok": true, "reason": "Vehicle VEH-001 became unavailable; ORD-002 requires handover"}'::jsonb,
    NULL,
    NULL,
    'replanning_agent'
);

-- ============================================================
-- 9. VEHICLE ROUTES
-- ============================================================

-- Current plan routes
INSERT INTO vehicle_routes (
    id, delivery_plan_id, route_no,
    vehicle_id, driver_id, vehicle_driver_assignment_id,
    start_location_id, end_location_id,
    status, planned_start_at, planned_end_at,
    actual_start_at, actual_end_at,
    distance_meters, duration_seconds,
    vehicle_capacity_load_units_snapshot,
    route_geometry, route_metrics
) VALUES
(
    '90000000-0000-0000-0000-000000000001',
    '80000000-0000-0000-0000-000000000001',
    1,
    '50000000-0000-0000-0000-000000000001',
    '60000000-0000-0000-0000-000000000001',
    '70000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'ACTIVE',
    '2026-09-25 08:55:00+08',
    '2026-09-25 11:30:00+08',
    '2026-09-25 08:58:00+08',
    NULL,
    16800,
    5100,
    4,
    '{"type":"LineString","coordinates":[[103.7066,1.3397],[103.7436,1.3329],[103.7490,1.3496],[103.7596,1.3039]]}'::jsonb,
    '{"planned_orders":2,"completed_orders":1,"at_risk_orders":1}'::jsonb
),
(
    '90000000-0000-0000-0000-000000000002',
    '80000000-0000-0000-0000-000000000001',
    2,
    '50000000-0000-0000-0000-000000000002',
    '60000000-0000-0000-0000-000000000002',
    '70000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'ACTIVE',
    '2026-09-25 09:00:00+08',
    '2026-09-25 13:00:00+08',
    '2026-09-25 09:02:00+08',
    NULL,
    26000,
    7500,
    5,
    '{"type":"LineString","coordinates":[[103.7066,1.3397],[103.7651,1.3151],[103.7861,1.2942],[103.7719,1.3774]]}'::jsonb,
    '{"planned_orders":3,"merchant_delay":true}'::jsonb
);

-- Candidate replanned routes
INSERT INTO vehicle_routes (
    id, delivery_plan_id, route_no,
    vehicle_id, driver_id, vehicle_driver_assignment_id,
    start_location_id, end_location_id,
    status, planned_start_at, planned_end_at,
    actual_start_at, actual_end_at,
    distance_meters, duration_seconds,
    vehicle_capacity_load_units_snapshot,
    route_geometry, route_metrics
) VALUES
(
    '90000000-0000-0000-0000-000000000011',
    '80000000-0000-0000-0000-000000000002',
    1,
    '50000000-0000-0000-0000-000000000001',
    '60000000-0000-0000-0000-000000000001',
    '70000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000004',
    'COMPLETED',
    '2026-09-25 08:55:00+08',
    '2026-09-25 09:50:00+08',
    '2026-09-25 08:58:00+08',
    '2026-09-25 09:45:00+08',
    9200,
    3000,
    4,
    '{"type":"LineString","coordinates":[[103.7066,1.3397],[103.7436,1.3329],[103.7490,1.3496]]}'::jsonb,
    '{"frozen_completed_segment":true,"orders":1}'::jsonb
),
(
    '90000000-0000-0000-0000-000000000012',
    '80000000-0000-0000-0000-000000000002',
    2,
    '50000000-0000-0000-0000-000000000003',
    '60000000-0000-0000-0000-000000000003',
    '70000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000005',
    'PLANNED',
    '2026-09-25 10:05:00+08',
    '2026-09-25 11:15:00+08',
    NULL,
    NULL,
    11800,
    3300,
    4,
    '{"type":"LineString","coordinates":[[103.7066,1.3397],[103.7596,1.3039],[103.7443,1.3854]]}'::jsonb,
    '{"handover_orders":1,"replacement_vehicle":true}'::jsonb
),
(
    '90000000-0000-0000-0000-000000000013',
    '80000000-0000-0000-0000-000000000002',
    3,
    '50000000-0000-0000-0000-000000000002',
    '60000000-0000-0000-0000-000000000002',
    '70000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000001',
    'PLANNED',
    '2026-09-25 09:20:00+08',
    '2026-09-25 13:00:00+08',
    NULL,
    NULL,
    25100,
    7200,
    5,
    '{"type":"LineString","coordinates":[[103.7066,1.3397],[103.7651,1.3151],[103.7861,1.2942],[103.7436,1.3329],[103.7719,1.3774]]}'::jsonb,
    '{"orders":3,"merchant_delay_adjusted":true}'::jsonb
);

-- ============================================================
-- 10. INCIDENTS
-- ============================================================
INSERT INTO incidents (
    id, incident_code, incident_type, status,
    delivery_plan_id, vehicle_route_id, vehicle_id,
    merchant_id, incident_location_id,
    original_ready_at, updated_ready_at, delay_seconds,
    detected_at, resolved_at, detected_by, details
) VALUES
(
    'a0000000-0000-0000-0000-000000000001',
    'INC-20260925-VEH-001',
    'VEHICLE_UNAVAILABLE',
    'REVIEW',
    '80000000-0000-0000-0000-000000000001',
    '90000000-0000-0000-0000-000000000001',
    '50000000-0000-0000-0000-000000000001',
    NULL,
    '10000000-0000-0000-0000-000000000008',
    NULL,
    NULL,
    NULL,
    '2026-09-25 10:05:00+08',
    NULL,
    'driver_app',
    '{"reason":"engine_warning","vehicle_code":"VEH-001","requires_replanning":true}'::jsonb
),
(
    'a0000000-0000-0000-0000-000000000002',
    'INC-20260925-MER-001',
    'MERCHANT_DELAY',
    'ASSESSING',
    '80000000-0000-0000-0000-000000000001',
    NULL,
    NULL,
    '20000000-0000-0000-0000-000000000002',
    NULL,
    '2026-09-25 09:10:00+08',
    '2026-09-25 09:25:00+08',
    900,
    '2026-09-25 09:12:00+08',
    NULL,
    'merchant_portal',
    '{"reason":"preparation_delay","threshold_seconds":600,"requires_assessment":true}'::jsonb
);

-- ============================================================
-- 11. DELIVERY PLAN ORDERS
-- ============================================================

-- Current plan
INSERT INTO delivery_plan_orders (
    id, delivery_plan_id, order_id,
    assignment_status, vehicle_route_id,
    unassigned_reason_code, unassigned_reason_detail
) VALUES
('b0000000-0000-0000-0000-000000000001','80000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','ASSIGNED','90000000-0000-0000-0000-000000000001',NULL,NULL),
('b0000000-0000-0000-0000-000000000002','80000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','ASSIGNED','90000000-0000-0000-0000-000000000001',NULL,NULL),
('b0000000-0000-0000-0000-000000000003','80000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000003','ASSIGNED','90000000-0000-0000-0000-000000000002',NULL,NULL),
('b0000000-0000-0000-0000-000000000004','80000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000004','ASSIGNED','90000000-0000-0000-0000-000000000002',NULL,NULL),
('b0000000-0000-0000-0000-000000000005','80000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000005','ASSIGNED','90000000-0000-0000-0000-000000000002',NULL,NULL),
(
    'b0000000-0000-0000-0000-000000000006',
    '80000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000006',
    'UNASSIGNED',
    NULL,
    'CAPACITY_INFEASIBLE',
    'No remaining vehicle capacity in the initial plan.'
);

-- Candidate plan
INSERT INTO delivery_plan_orders (
    id, delivery_plan_id, order_id,
    assignment_status, vehicle_route_id,
    unassigned_reason_code, unassigned_reason_detail
) VALUES
('b0000000-0000-0000-0000-000000000011','80000000-0000-0000-0000-000000000002','40000000-0000-0000-0000-000000000001','ASSIGNED','90000000-0000-0000-0000-000000000011',NULL,NULL),
('b0000000-0000-0000-0000-000000000012','80000000-0000-0000-0000-000000000002','40000000-0000-0000-0000-000000000002','ASSIGNED','90000000-0000-0000-0000-000000000012',NULL,NULL),
('b0000000-0000-0000-0000-000000000013','80000000-0000-0000-0000-000000000002','40000000-0000-0000-0000-000000000003','ASSIGNED','90000000-0000-0000-0000-000000000013',NULL,NULL),
('b0000000-0000-0000-0000-000000000014','80000000-0000-0000-0000-000000000002','40000000-0000-0000-0000-000000000004','ASSIGNED','90000000-0000-0000-0000-000000000013',NULL,NULL),
('b0000000-0000-0000-0000-000000000015','80000000-0000-0000-0000-000000000002','40000000-0000-0000-0000-000000000005','ASSIGNED','90000000-0000-0000-0000-000000000013',NULL,NULL),
(
    'b0000000-0000-0000-0000-000000000016',
    '80000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000006',
    'UNASSIGNED',
    NULL,
    'NO_FEASIBLE_ROUTE',
    'Order remains unassigned after recovery optimization.'
);

-- ============================================================
-- 12. ROUTE STOPS
-- ============================================================

-- Current plan / route 1
INSERT INTO route_stops (
    id, vehicle_route_id, order_id, location_id,
    stop_type, sequence_no, precedence_stop_id, source_incident_id,
    planned_arrival_at, planned_departure_at,
    actual_arrival_at, actual_departure_at,
    service_seconds, time_window_start_at, time_window_end_at,
    demand_load_units_snapshot, status
) VALUES
(
    'c0000000-0000-0000-0000-000000000001',
    '90000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000002',
    'PICKUP', 1, NULL, NULL,
    '2026-09-25 09:00:00+08', '2026-09-25 09:05:00+08',
    '2026-09-25 09:01:00+08', '2026-09-25 09:06:00+08',
    300, NULL, NULL, 2, 'COMPLETED'
),
(
    'c0000000-0000-0000-0000-000000000002',
    '90000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    'PICKUP', 2, NULL, NULL,
    '2026-09-25 09:06:00+08', '2026-09-25 09:11:00+08',
    '2026-09-25 09:07:00+08', '2026-09-25 09:12:00+08',
    300, NULL, NULL, 1, 'COMPLETED'
),
(
    'c0000000-0000-0000-0000-000000000003',
    '90000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000004',
    'DELIVERY', 3,
    'c0000000-0000-0000-0000-000000000001', NULL,
    '2026-09-25 09:40:00+08', '2026-09-25 09:45:00+08',
    '2026-09-25 09:39:00+08', '2026-09-25 09:44:00+08',
    300,
    '2026-09-25 09:30:00+08', '2026-09-25 10:30:00+08',
    2, 'COMPLETED'
),
(
    'c0000000-0000-0000-0000-000000000004',
    '90000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000005',
    'DELIVERY', 4,
    'c0000000-0000-0000-0000-000000000002', NULL,
    '2026-09-25 10:20:00+08', '2026-09-25 10:25:00+08',
    NULL, NULL,
    300,
    '2026-09-25 10:00:00+08', '2026-09-25 11:30:00+08',
    1, 'PLANNED'
);

-- Current plan / route 2
INSERT INTO route_stops (
    id, vehicle_route_id, order_id, location_id,
    stop_type, sequence_no, precedence_stop_id, source_incident_id,
    planned_arrival_at, planned_departure_at,
    service_seconds, time_window_start_at, time_window_end_at,
    demand_load_units_snapshot, status
) VALUES
(
    'c0000000-0000-0000-0000-000000000005',
    '90000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000003',
    'PICKUP', 1, NULL, NULL,
    '2026-09-25 09:15:00+08', '2026-09-25 09:20:00+08',
    300, NULL, NULL, 2, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000006',
    '90000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000004',
    '10000000-0000-0000-0000-000000000003',
    'PICKUP', 2, NULL, NULL,
    '2026-09-25 09:20:00+08', '2026-09-25 09:25:00+08',
    300, NULL, NULL, 1, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000007',
    '90000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000006',
    'DELIVERY', 3,
    'c0000000-0000-0000-0000-000000000005', NULL,
    '2026-09-25 10:25:00+08', '2026-09-25 10:30:00+08',
    300,
    '2026-09-25 10:15:00+08', '2026-09-25 11:30:00+08',
    2, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000008',
    '90000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000005',
    '10000000-0000-0000-0000-000000000002',
    'PICKUP', 4, NULL, NULL,
    '2026-09-25 10:50:00+08', '2026-09-25 10:55:00+08',
    300, NULL, NULL, 2, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000009',
    '90000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000004',
    '10000000-0000-0000-0000-000000000007',
    'DELIVERY', 5,
    'c0000000-0000-0000-0000-000000000006', NULL,
    '2026-09-25 11:10:00+08', '2026-09-25 11:15:00+08',
    300,
    '2026-09-25 11:00:00+08', '2026-09-25 12:00:00+08',
    1, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000010',
    '90000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000005',
    '10000000-0000-0000-0000-000000000006',
    'DELIVERY', 6,
    'c0000000-0000-0000-0000-000000000008', NULL,
    '2026-09-25 11:40:00+08', '2026-09-25 11:45:00+08',
    300,
    '2026-09-25 11:00:00+08', '2026-09-25 12:30:00+08',
    2, 'PLANNED'
);

-- Candidate plan / frozen completed route
INSERT INTO route_stops (
    id, vehicle_route_id, order_id, location_id,
    stop_type, sequence_no, precedence_stop_id, source_incident_id,
    planned_arrival_at, planned_departure_at,
    actual_arrival_at, actual_departure_at,
    service_seconds, time_window_start_at, time_window_end_at,
    demand_load_units_snapshot, status
) VALUES
(
    'c0000000-0000-0000-0000-000000000011',
    '90000000-0000-0000-0000-000000000011',
    '40000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000002',
    'PICKUP', 1, NULL, NULL,
    '2026-09-25 09:00:00+08', '2026-09-25 09:05:00+08',
    '2026-09-25 09:01:00+08', '2026-09-25 09:06:00+08',
    300, NULL, NULL, 2, 'COMPLETED'
),
(
    'c0000000-0000-0000-0000-000000000012',
    '90000000-0000-0000-0000-000000000011',
    '40000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000004',
    'DELIVERY', 2,
    'c0000000-0000-0000-0000-000000000011', NULL,
    '2026-09-25 09:40:00+08', '2026-09-25 09:45:00+08',
    '2026-09-25 09:39:00+08', '2026-09-25 09:44:00+08',
    300,
    '2026-09-25 09:30:00+08', '2026-09-25 10:30:00+08',
    2, 'COMPLETED'
);

-- Candidate plan / replacement vehicle route with handover
INSERT INTO route_stops (
    id, vehicle_route_id, order_id, location_id,
    stop_type, sequence_no, precedence_stop_id, source_incident_id,
    planned_arrival_at, planned_departure_at,
    service_seconds, time_window_start_at, time_window_end_at,
    demand_load_units_snapshot, status
) VALUES
(
    'c0000000-0000-0000-0000-000000000013',
    '90000000-0000-0000-0000-000000000012',
    '40000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000008',
    'HANDOVER', 1, NULL,
    'a0000000-0000-0000-0000-000000000001',
    '2026-09-25 10:10:00+08', '2026-09-25 10:15:00+08',
    300, NULL, NULL, 1, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000014',
    '90000000-0000-0000-0000-000000000012',
    '40000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000005',
    'DELIVERY', 2,
    'c0000000-0000-0000-0000-000000000013', NULL,
    '2026-09-25 10:50:00+08', '2026-09-25 10:55:00+08',
    300,
    '2026-09-25 10:00:00+08', '2026-09-25 11:30:00+08',
    1, 'PLANNED'
);

-- Candidate plan / adjusted route 3
INSERT INTO route_stops (
    id, vehicle_route_id, order_id, location_id,
    stop_type, sequence_no, precedence_stop_id, source_incident_id,
    planned_arrival_at, planned_departure_at,
    service_seconds, time_window_start_at, time_window_end_at,
    demand_load_units_snapshot, status
) VALUES
(
    'c0000000-0000-0000-0000-000000000015',
    '90000000-0000-0000-0000-000000000013',
    '40000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000003',
    'PICKUP', 1, NULL, NULL,
    '2026-09-25 09:25:00+08', '2026-09-25 09:30:00+08',
    300, NULL, NULL, 2, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000016',
    '90000000-0000-0000-0000-000000000013',
    '40000000-0000-0000-0000-000000000004',
    '10000000-0000-0000-0000-000000000003',
    'PICKUP', 2, NULL, NULL,
    '2026-09-25 09:30:00+08', '2026-09-25 09:35:00+08',
    300, NULL, NULL, 1, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000017',
    '90000000-0000-0000-0000-000000000013',
    '40000000-0000-0000-0000-000000000003',
    '10000000-0000-0000-0000-000000000006',
    'DELIVERY', 3,
    'c0000000-0000-0000-0000-000000000015', NULL,
    '2026-09-25 10:20:00+08', '2026-09-25 10:25:00+08',
    300,
    '2026-09-25 10:15:00+08', '2026-09-25 11:30:00+08',
    2, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000018',
    '90000000-0000-0000-0000-000000000013',
    '40000000-0000-0000-0000-000000000005',
    '10000000-0000-0000-0000-000000000002',
    'PICKUP', 4, NULL, NULL,
    '2026-09-25 10:40:00+08', '2026-09-25 10:45:00+08',
    300, NULL, NULL, 2, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000019',
    '90000000-0000-0000-0000-000000000013',
    '40000000-0000-0000-0000-000000000004',
    '10000000-0000-0000-0000-000000000007',
    'DELIVERY', 5,
    'c0000000-0000-0000-0000-000000000016', NULL,
    '2026-09-25 11:05:00+08', '2026-09-25 11:10:00+08',
    300,
    '2026-09-25 11:00:00+08', '2026-09-25 12:00:00+08',
    1, 'PLANNED'
),
(
    'c0000000-0000-0000-0000-000000000020',
    '90000000-0000-0000-0000-000000000013',
    '40000000-0000-0000-0000-000000000005',
    '10000000-0000-0000-0000-000000000006',
    'DELIVERY', 6,
    'c0000000-0000-0000-0000-000000000018', NULL,
    '2026-09-25 11:30:00+08', '2026-09-25 11:35:00+08',
    300,
    '2026-09-25 11:00:00+08', '2026-09-25 12:30:00+08',
    2, 'PLANNED'
);

-- ============================================================
-- 13. INCIDENT AFFECTED ORDERS
-- ============================================================
INSERT INTO incident_affected_orders (
    id, incident_id, order_id, original_vehicle_route_id,
    execution_status_snapshot, risk_status_snapshot,
    was_picked_up, was_completed,
    requires_replanning, handover_required,
    impact_type, impact_reason, assessed_at
) VALUES
(
    'd0000000-0000-0000-0000-000000000001',
    'a0000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000001',
    '90000000-0000-0000-0000-000000000001',
    'COMPLETED',
    'NORMAL',
    true,
    true,
    false,
    false,
    'COMPLETED_FROZEN',
    'Order was completed before the vehicle became unavailable and must remain frozen.',
    '2026-09-25 10:06:00+08'
),
(
    'd0000000-0000-0000-0000-000000000002',
    'a0000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000002',
    '90000000-0000-0000-0000-000000000001',
    'PICKED_UP',
    'AT_RISK',
    true,
    false,
    true,
    true,
    'HANDOVER_REQUIRED',
    'Order was already picked up when VEH-001 failed; cargo must be handed over to a replacement vehicle.',
    '2026-09-25 10:06:00+08'
),
(
    'd0000000-0000-0000-0000-000000000003',
    'a0000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000003',
    '90000000-0000-0000-0000-000000000002',
    'PLANNED',
    'AT_RISK',
    false,
    false,
    true,
    false,
    'WAITING_TIME_UPDATE',
    'Merchant ready time moved from 09:10 to 09:25; pickup schedule needs reassessment.',
    '2026-09-25 09:13:00+08'
),
(
    'd0000000-0000-0000-0000-000000000004',
    'a0000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000004',
    '90000000-0000-0000-0000-000000000002',
    'PLANNED',
    'AT_RISK',
    false,
    false,
    true,
    false,
    'DOWNSTREAM_ROUTE_IMPACT',
    'Merchant delay may shift downstream arrival times on the same route.',
    '2026-09-25 09:13:00+08'
);

-- ============================================================
-- 14. RECOVERY PLANS
-- ============================================================
INSERT INTO recovery_plans (
    id, recovery_code, incident_id, attempt_no,
    previous_recovery_plan_id,
    base_delivery_plan_id, candidate_delivery_plan_id,
    status, replanning_scope, scope_description,
    agent_explanation, solver_status, validation_status,
    solver_validation_summary,
    dispatcher_decision, decision_reason,
    reviewed_by, reviewed_at
) VALUES
(
    'e0000000-0000-0000-0000-000000000001',
    'REC-20260925-001',
    'a0000000-0000-0000-0000-000000000001',
    1,
    NULL,
    '80000000-0000-0000-0000-000000000001',
    '80000000-0000-0000-0000-000000000002',
    'PENDING_REVIEW',
    'CROSS_ROUTE',
    'Freeze completed work, hand over the picked-up order, and use an available replacement vehicle.',
    'VEH-001 became unavailable after ORD-002 was picked up. The candidate preserves ORD-001 as completed, creates a handover for ORD-002, and assigns VEH-003 as the replacement vehicle.',
    'FEASIBLE',
    'VALID',
    '{"capacity_ok":true,"time_windows_ok":true,"handover_required":true,"replacement_vehicle":"VEH-003"}'::jsonb,
    NULL,
    NULL,
    NULL,
    NULL
),
(
    'e0000000-0000-0000-0000-000000000002',
    'REC-20260925-002',
    'a0000000-0000-0000-0000-000000000002',
    1,
    NULL,
    '80000000-0000-0000-0000-000000000001',
    NULL,
    'DRAFT',
    'AFFECTED_ROUTE',
    'Re-evaluate the route containing orders from the delayed merchant.',
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL
);

-- 15. P1 RISK ALERTS
-- At 10:05, route 2 is 3,000 seconds behind its first pickup. ORD-003's
-- projected delivery at 11:15 reaches its 11:30 window's 900-second threshold.
INSERT INTO risk_alerts (
    id, delivery_plan_id, order_id, business_date, risk_type, status,
    evidence, detected_at, last_evaluated_at
) VALUES (
    'f0000000-0000-0000-0000-000000000001',
    '80000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000003',
    '2026-09-25', 'DELIVERY_WINDOW', 'ACTIVE',
    '{"reason_category":"APPROACHING_WINDOW","estimated_arrival_at":"2026-09-25T11:15:00+08:00","delivery_window_end_at":"2026-09-25T11:30:00+08:00","delay_seconds":3000,"threshold_seconds":900,"vehicle_route_id":"90000000-0000-0000-0000-000000000002"}'::jsonb,
    '2026-09-25 10:05:00+08', '2026-09-25 10:05:00+08'
);

-- Use the same cursor allocation protocol as live alert writers.
SELECT pg_advisory_xact_lock(55120, 1);
INSERT INTO risk_alert_changes (
    change_id, alert_id, change_type, recorded_at, evidence_snapshot
) VALUES (
    nextval('public.risk_alert_change_cursor_seq'),
    'f0000000-0000-0000-0000-000000000001',
    'CREATED', '2026-09-25 10:05:00+08',
    '{"reason_category":"APPROACHING_WINDOW","estimated_arrival_at":"2026-09-25T11:15:00+08:00","delivery_window_end_at":"2026-09-25T11:30:00+08:00","delay_seconds":3000,"threshold_seconds":900,"vehicle_route_id":"90000000-0000-0000-0000-000000000002"}'::jsonb
);

COMMIT;

-- ============================================================
-- Verification queries
-- ============================================================

SELECT 'locations' AS table_name, COUNT(*) AS row_count FROM locations
UNION ALL
SELECT 'merchants', COUNT(*) FROM merchants
UNION ALL
SELECT 'customers', COUNT(*) FROM customers
UNION ALL
SELECT 'orders', COUNT(*) FROM orders
UNION ALL
SELECT 'vehicles', COUNT(*) FROM vehicles
UNION ALL
SELECT 'drivers', COUNT(*) FROM drivers
UNION ALL
SELECT 'vehicle_driver_assignments', COUNT(*) FROM vehicle_driver_assignments
UNION ALL
SELECT 'delivery_plans', COUNT(*) FROM delivery_plans
UNION ALL
SELECT 'vehicle_routes', COUNT(*) FROM vehicle_routes
UNION ALL
SELECT 'incidents', COUNT(*) FROM incidents
UNION ALL
SELECT 'delivery_plan_orders', COUNT(*) FROM delivery_plan_orders
UNION ALL
SELECT 'route_stops', COUNT(*) FROM route_stops
UNION ALL
SELECT 'incident_affected_orders', COUNT(*) FROM incident_affected_orders
UNION ALL
SELECT 'recovery_plans', COUNT(*) FROM recovery_plans
UNION ALL
SELECT 'risk_alerts', COUNT(*) FROM risk_alerts
UNION ALL
SELECT 'risk_alert_changes', COUNT(*) FROM risk_alert_changes
ORDER BY table_name;
