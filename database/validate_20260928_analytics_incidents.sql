-- Read-only verification for seed_20260928_analytics_incidents.sql.
DO $check$
DECLARE
    seeded_count integer;
    affected_count integer;
BEGIN
    SELECT count(*) INTO seeded_count FROM incidents
    WHERE incident_code IN (
        'INC-20260928-ANALYTICS-001',
        'INC-20260928-ANALYTICS-002',
        'INC-20260928-ANALYTICS-003'
    ) AND status = 'RESOLVED' AND incident_type = 'MERCHANT_DELAY';
    IF seeded_count <> 3 THEN
        RAISE EXCEPTION 'Expected 3 resolved analytics demo incidents, found %', seeded_count;
    END IF;

    SELECT count(*) INTO affected_count FROM incident_affected_orders a
    JOIN incidents i ON i.id = a.incident_id
    WHERE i.incident_code IN (
        'INC-20260928-ANALYTICS-001',
        'INC-20260928-ANALYTICS-002',
        'INC-20260928-ANALYTICS-003'
    ) AND a.execution_status_snapshot = 'PLANNED'
      AND a.risk_status_snapshot = 'NORMAL'
      AND NOT a.requires_replanning
      AND NOT a.was_picked_up
      AND NOT a.was_completed;
    IF affected_count <> 6 THEN
        RAISE EXCEPTION 'Expected 6 unchanged affected-order snapshots, found %', affected_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE id = '5e12cbc3-25ca-4249-ac37-83c1a1b7225c' AND status = 'CURRENT'
    ) OR NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE id = 'ba630d36-87c0-4ca9-ab16-02957862d0ff' AND status = 'CANDIDATE'
    ) OR NOT EXISTS (
        SELECT 1 FROM recovery_plans
        WHERE id = '2824fe42-3644-40f5-969f-0811e90809fd'
          AND incident_id = '70772257-5a52-456c-8efe-fe5b4d9cccf3'
          AND status = 'PENDING_REVIEW'
    ) THEN
        RAISE EXCEPTION 'Existing current plan or pending review changed';
    END IF;
END
$check$;

SELECT i.incident_code, i.status, i.delay_seconds, p.plan_code,
       count(a.id) AS affected_order_snapshots
FROM incidents i
JOIN delivery_plans p ON p.id = i.delivery_plan_id
LEFT JOIN incident_affected_orders a ON a.incident_id = i.id
WHERE i.incident_code LIKE 'INC-20260928-ANALYTICS-%'
GROUP BY i.id, p.plan_code
ORDER BY i.incident_code;
