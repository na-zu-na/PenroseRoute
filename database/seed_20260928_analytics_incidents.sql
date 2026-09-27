-- Additive, idempotent Analytics demo incidents for 2026-09-28.
-- Target: penrose_route_demo_rebuilt after seed_20260928_dispatch_demo.sql
-- and its normal planning/recovery workflow. This script adds only resolved
-- historical merchant-delay facts and affected-order snapshots. It does not
-- update Orders, Merchants, the CURRENT Plan, the pending Candidate or Decisions.

BEGIN;

DO $guard$
BEGIN
    IF current_database() <> 'penrose_route_demo_rebuilt' THEN
        RAISE EXCEPTION 'Refusing analytics demo seed in %', current_database();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE plan_code = 'PLAN-20260928-V1' AND business_date = DATE '2026-09-28'
    ) OR NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE id = '5e12cbc3-25ca-4249-ac37-83c1a1b7225c'
          AND plan_code = 'PLAN-20260928-V2' AND status = 'CURRENT'
    ) OR NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE id = 'ba630d36-87c0-4ca9-ab16-02957862d0ff'
          AND plan_code = 'PLAN-20260928-V3' AND status = 'CANDIDATE'
    ) OR NOT EXISTS (
        SELECT 1 FROM recovery_plans
        WHERE id = '2824fe42-3644-40f5-969f-0811e90809fd'
          AND incident_id = '70772257-5a52-456c-8efe-fe5b4d9cccf3'
          AND status = 'PENDING_REVIEW'
    ) THEN
        RAISE EXCEPTION 'Expected September 28 plans/review are missing or changed';
    END IF;
    IF (SELECT count(*) FROM orders
        WHERE business_date = DATE '2026-09-28'
          AND execution_status = 'PLANNED' AND risk_status = 'NORMAL') <> 2 THEN
        RAISE EXCEPTION 'Expected two unchanged September 28 demo orders';
    END IF;
END
$guard$;

WITH scenarios(id, incident_code, plan_code, original_ready_at, updated_ready_at, detected_at) AS (
    VALUES
    ('a9280000-0000-4000-8000-000000000001'::uuid,
     'INC-20260928-ANALYTICS-001', 'PLAN-20260928-V1',
     '2026-09-28 18:00:00+08'::timestamptz, '2026-09-28 18:05:00+08'::timestamptz,
     '2026-09-28 00:30:00+08'::timestamptz),
    ('a9280000-0000-4000-8000-000000000002'::uuid,
     'INC-20260928-ANALYTICS-002', 'PLAN-20260928-V1',
     '2026-09-28 18:00:00+08'::timestamptz, '2026-09-28 18:10:00+08'::timestamptz,
     '2026-09-28 01:00:00+08'::timestamptz),
    ('a9280000-0000-4000-8000-000000000003'::uuid,
     'INC-20260928-ANALYTICS-003', 'PLAN-20260928-V2',
     '2026-09-28 18:15:00+08'::timestamptz, '2026-09-28 18:20:00+08'::timestamptz,
     '2026-09-28 02:00:00+08'::timestamptz)
)
INSERT INTO incidents (
    id, incident_code, incident_type, status, delivery_plan_id, merchant_id,
    original_ready_at, updated_ready_at, delay_seconds, detected_at,
    resolved_at, detected_by, details, created_at, updated_at
)
SELECT s.id, s.incident_code, 'MERCHANT_DELAY', 'RESOLVED', p.id,
       'f0280000-0000-4000-8000-000000000001'::uuid,
       s.original_ready_at, s.updated_ready_at,
       extract(epoch FROM s.updated_ready_at - s.original_ready_at)::integer,
       s.detected_at, s.detected_at, 'analytics_demo_seed',
       '{"demo_only":true,"scenario":"short_merchant_delay_no_replanning"}'::jsonb,
       s.detected_at, s.detected_at
FROM scenarios s
JOIN delivery_plans p ON p.plan_code = s.plan_code
WHERE p.business_date = DATE '2026-09-28'
ON CONFLICT (incident_code) DO NOTHING;

INSERT INTO incident_affected_orders (
    incident_id, order_id, original_vehicle_route_id,
    execution_status_snapshot, risk_status_snapshot,
    was_picked_up, was_completed, requires_replanning, handover_required,
    impact_type, impact_reason, assessed_at
)
SELECT i.id, o.id, dpo.vehicle_route_id,
       o.execution_status, o.risk_status,
       false, false, false, false,
       'WAITING_TIME_UPDATE',
       'Merchant ready time changed before pickup; delay was at most 600 seconds.',
       i.detected_at
FROM incidents i
JOIN delivery_plan_orders dpo ON dpo.delivery_plan_id = i.delivery_plan_id
JOIN orders o ON o.id = dpo.order_id AND o.merchant_id = i.merchant_id
WHERE i.incident_code IN (
    'INC-20260928-ANALYTICS-001',
    'INC-20260928-ANALYTICS-002',
    'INC-20260928-ANALYTICS-003'
)
AND dpo.assignment_status = 'ASSIGNED'
ON CONFLICT (incident_id, order_id) DO NOTHING;

COMMIT;

-- Then run validate_20260928_analytics_incidents.sql.
