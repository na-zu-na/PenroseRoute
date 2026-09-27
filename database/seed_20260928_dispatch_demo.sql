-- One-off, additive dispatch approval demo for 2026-09-28.
-- Run only against penrose_route_demo_rebuilt; never deletes existing rows.
-- The plan, incident, and recovery candidate are created through application workflows.
BEGIN;

DO $$
BEGIN
    IF current_database() <> 'penrose_route_demo_rebuilt' THEN
        RAISE EXCEPTION 'Refusing to seed dispatch demo in %', current_database();
    END IF;
    IF EXISTS (SELECT 1 FROM delivery_plans WHERE business_date = DATE '2026-09-28')
       OR EXISTS (SELECT 1 FROM orders WHERE business_date = DATE '2026-09-28') THEN
        RAISE EXCEPTION '2026-09-28 demo data already exists';
    END IF;
END $$;

INSERT INTO merchants (
    id, merchant_code, name, pickup_location_id,
    preparation_status, operational_ready_at, default_pickup_service_seconds
) VALUES (
    'f0280000-0000-4000-8000-000000000001', 'MER-SEP28-DISPATCH',
    'September 28 Dispatch Demo Merchant',
    '10000000-0000-0000-0000-000000000002',
    'READY', '2026-09-28 18:00:00+08', 300
);

INSERT INTO vehicles (
    id, vehicle_code, name, capacity_load_units, status,
    current_location_id, current_location_recorded_at
) VALUES (
    'f0280000-0000-4000-8000-000000000002', 'VEH-SEP28-DISPATCH',
    'September 28 Dispatch Demo Van', 4, 'AVAILABLE',
    '10000000-0000-0000-0000-000000000001', '2026-09-28 16:00:00+08'
);

INSERT INTO drivers (id, driver_code, name, status) VALUES (
    'f0280000-0000-4000-8000-000000000003', 'DRV-SEP28-DISPATCH',
    'September 28 Dispatch Demo Driver', 'AVAILABLE'
);

INSERT INTO vehicle_driver_assignments (
    id, vehicle_id, driver_id, assigned_from_at, assigned_until_at, status
) VALUES (
    'f0280000-0000-4000-8000-000000000004',
    'f0280000-0000-4000-8000-000000000002',
    'f0280000-0000-4000-8000-000000000003',
    '2026-09-28 16:00:00+08', '2026-09-29 00:00:00+08', 'PLANNED'
);

INSERT INTO orders (
    id, order_code, business_date, merchant_id, customer_id,
    pickup_location_id, delivery_location_id, pickup_ready_at,
    pickup_service_seconds, delivery_window_start_at, delivery_window_end_at,
    delivery_service_seconds, demand_load_units, execution_status, risk_status
) VALUES
(
    'f0280000-0000-4000-8000-000000000005', 'ORD-20260928-DISPATCH-001',
    DATE '2026-09-28', 'f0280000-0000-4000-8000-000000000001',
    '30000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000004',
    '2026-09-28 18:00:00+08', 300,
    '2026-09-28 19:00:00+08', '2026-09-28 22:00:00+08',
    300, 1, 'PLANNED', 'NORMAL'
),
(
    'f0280000-0000-4000-8000-000000000006', 'ORD-20260928-DISPATCH-002',
    DATE '2026-09-28', 'f0280000-0000-4000-8000-000000000001',
    '30000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000002',
    '10000000-0000-0000-0000-000000000005',
    '2026-09-28 18:00:00+08', 300,
    '2026-09-28 19:30:00+08', '2026-09-28 22:30:00+08',
    300, 1, 'PLANNED', 'NORMAL'
);

COMMIT;
