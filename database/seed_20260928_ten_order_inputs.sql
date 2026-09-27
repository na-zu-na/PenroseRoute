-- Stage 8 additional orders and two executable vehicle-driver pairs.
-- Intended for backend/app/jobs/refresh_20260928_demo.py only: that job
-- rolls this transaction back for a dry solve, then reapplies it atomically
-- with the new plan. Never run this file alone on a deployed database.
DO $$
BEGIN
    IF current_database() <> 'penrose_route_demo_rebuilt' THEN
        RAISE EXCEPTION 'Refusing to modify an unexpected database';
    END IF;
    IF (SELECT count(*) FROM orders WHERE business_date = DATE '2026-09-28') <> 2
       OR NOT EXISTS (SELECT 1 FROM delivery_plans WHERE plan_code = 'PLAN-20260928-V2' AND status = 'CURRENT')
       OR NOT EXISTS (SELECT 1 FROM delivery_plans WHERE plan_code = 'PLAN-20260928-V3' AND status = 'CANDIDATE') THEN
        RAISE EXCEPTION 'Unexpected September 28 demo baseline';
    END IF;
END $$;

INSERT INTO vehicle_driver_assignments (
    id, vehicle_id, driver_id, assigned_from_at, assigned_until_at, status
) VALUES
(
    'f0280000-0000-4000-8000-000000000015',
    '50000000-0000-0000-0000-000000000002',
    '60000000-0000-0000-0000-000000000002',
    '2026-09-28 16:00:00+08', '2026-09-29 00:00:00+08', 'PLANNED'
),
(
    'f0280000-0000-4000-8000-000000000016',
    '50000000-0000-0000-0000-000000000003',
    '60000000-0000-0000-0000-000000000003',
    '2026-09-28 16:00:00+08', '2026-09-29 00:00:00+08', 'PLANNED'
);

INSERT INTO orders (
    id, order_code, business_date, merchant_id, customer_id,
    pickup_location_id, delivery_location_id, pickup_ready_at,
    pickup_service_seconds, delivery_window_start_at, delivery_window_end_at,
    delivery_service_seconds, demand_load_units, execution_status, risk_status
)
SELECT
    ('f0280000-0000-4000-8000-' || lpad(template.order_no::text, 12, '0'))::uuid,
    'ORD-20260928-DISPATCH-' || lpad((template.order_no - 4)::text, 3, '0'),
    DATE '2026-09-28', 'f0280000-0000-4000-8000-000000000001'::uuid,
    customer.id, '10000000-0000-0000-0000-000000000002'::uuid,
    customer.default_delivery_location_id, '2026-09-28 18:00:00+08'::timestamptz,
    300, template.window_start, template.window_end, 300, 1, 'PLANNED', 'NORMAL'
FROM (VALUES
    ( 7, 'CUS-003', '2026-09-28 19:00:00+08'::timestamptz, '2026-09-28 19:30:00+08'::timestamptz),
    ( 8, 'CUS-003', '2026-09-28 19:05:00+08'::timestamptz, '2026-09-28 19:35:00+08'::timestamptz),
    ( 9, 'CUS-004', '2026-09-28 19:00:00+08'::timestamptz, '2026-09-28 19:30:00+08'::timestamptz),
    (10, 'CUS-004', '2026-09-28 19:05:00+08'::timestamptz, '2026-09-28 19:35:00+08'::timestamptz),
    (11, 'CUS-001', '2026-09-28 19:00:00+08'::timestamptz, '2026-09-28 19:12:00+08'::timestamptz),
    (12, 'CUS-001', '2026-09-28 19:05:00+08'::timestamptz, '2026-09-28 19:17:00+08'::timestamptz),
    (13, 'CUS-002', '2026-09-28 19:35:00+08'::timestamptz, '2026-09-28 20:05:00+08'::timestamptz),
    (14, 'CUS-002', '2026-09-28 19:40:00+08'::timestamptz, '2026-09-28 20:10:00+08'::timestamptz)
) AS template(order_no, customer_code, window_start, window_end)
JOIN customers customer ON customer.customer_code = template.customer_code;
