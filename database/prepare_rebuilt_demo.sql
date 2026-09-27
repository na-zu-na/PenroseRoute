-- Apply only to the disposable penrose_route_demo_rebuilt database after
-- create_datatable.sql and reference_data.sql. Never run against the live DB.
BEGIN;

DO $$
BEGIN
    IF current_database() <> 'penrose_route_demo_rebuilt' THEN
        RAISE EXCEPTION 'Refusing to prepare demo data in %', current_database();
    END IF;
END $$;

-- The legacy seed contains hand-authored plans and incident outcomes. Keep
-- its base resources, then let the real workflows generate these results.
TRUNCATE TABLE
    risk_alert_changes, risk_alerts, recovery_plans,
    incident_affected_orders, incidents,
    delivery_plan_orders, route_stops, vehicle_routes, delivery_plans;

UPDATE merchants
SET preparation_status = 'READY',
    operational_ready_at = CASE merchant_code
        WHEN 'MER-001' THEN '2026-09-27 09:00:00+08'::timestamptz
        ELSE '2026-09-27 09:10:00+08'::timestamptz
    END;

-- Completed execution facts cannot be regressed by UPDATE. In this fresh
-- disposable database, replace only the six legacy demo orders with new
-- pre-execution facts instead of disabling that protection trigger.
CREATE TEMP TABLE demo_core_orders ON COMMIT DROP AS
SELECT * FROM orders WHERE id IN (
    '40000000-0000-0000-0000-000000000001',
    '40000000-0000-0000-0000-000000000002',
    '40000000-0000-0000-0000-000000000003',
    '40000000-0000-0000-0000-000000000004',
    '40000000-0000-0000-0000-000000000005',
    '40000000-0000-0000-0000-000000000006'
);

DELETE FROM orders WHERE id IN (SELECT id FROM demo_core_orders);
INSERT INTO orders (
    id, order_code, business_date, merchant_id, customer_id,
    pickup_location_id, delivery_location_id, pickup_ready_at,
    pickup_service_seconds, delivery_window_start_at, delivery_window_end_at,
    delivery_service_seconds, demand_load_units, execution_status, risk_status,
    created_at, updated_at
)
SELECT
    id, replace(order_code, '20260925', '20260927'), DATE '2026-09-27',
    merchant_id, customer_id, pickup_location_id, delivery_location_id,
    pickup_ready_at + INTERVAL '2 days', pickup_service_seconds,
    delivery_window_start_at + INTERVAL '2 days',
    delivery_window_end_at + INTERVAL '2 days', delivery_service_seconds,
    demand_load_units, 'PLANNED', 'NORMAL', created_at, updated_at
FROM demo_core_orders;

UPDATE vehicles
SET status = 'AVAILABLE',
    current_location_id = '10000000-0000-0000-0000-000000000001',
    current_location_recorded_at = '2026-09-27 08:00:00+08'::timestamptz
WHERE id IN (
    '50000000-0000-0000-0000-000000000001',
    '50000000-0000-0000-0000-000000000002',
    '50000000-0000-0000-0000-000000000003'
);

UPDATE drivers SET status = 'AVAILABLE'
WHERE id IN (
    '60000000-0000-0000-0000-000000000001',
    '60000000-0000-0000-0000-000000000002',
    '60000000-0000-0000-0000-000000000003'
);

UPDATE vehicle_driver_assignments
SET assigned_from_at = assigned_from_at + INTERVAL '2 days',
    assigned_until_at = assigned_until_at + INTERVAL '2 days',
    status = 'PLANNED',
    activated_at = NULL
WHERE id IN (
    '70000000-0000-0000-0000-000000000001',
    '70000000-0000-0000-0000-000000000002',
    '70000000-0000-0000-0000-000000000003'
);

COMMIT;
