-- Read-only acceptance checks for the expanded 2026-09-28 demo.
DO $$
DECLARE
    current_plan_id uuid;
BEGIN
    IF (SELECT count(*) FROM orders WHERE business_date = DATE '2026-09-28') <> 10 THEN
        RAISE EXCEPTION 'Expected exactly 10 September 28 orders';
    END IF;

    SELECT id INTO current_plan_id
    FROM delivery_plans
    WHERE business_date = DATE '2026-09-28' AND status = 'CURRENT';
    IF current_plan_id IS NULL THEN
        RAISE EXCEPTION 'Expected one current September 28 plan';
    END IF;
    IF (SELECT count(*) FROM delivery_plan_orders WHERE delivery_plan_id = current_plan_id AND assignment_status = 'ASSIGNED') <> 10 THEN
        RAISE EXCEPTION 'All 10 orders must belong to current routes';
    END IF;
    IF (SELECT count(*) FROM vehicle_routes WHERE delivery_plan_id = current_plan_id) < 2 THEN
        RAISE EXCEPTION 'Expected multiple routed vehicles';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM orders o
        JOIN delivery_plan_orders po ON po.order_id = o.id
        WHERE po.delivery_plan_id = current_plan_id AND o.risk_status = 'AT_RISK'
    ) THEN
        RAISE EXCEPTION 'Expected deterministic AT_RISK order on current plan';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE plan_code = 'PLAN-20260928-V2' AND status = 'SUPERSEDED'
    ) THEN
        RAISE EXCEPTION 'Old current plan snapshot must be superseded';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM delivery_plans
        WHERE plan_code = 'PLAN-20260928-V3' AND status = 'CANCELLED'
    ) THEN
        RAISE EXCEPTION 'Previous pending candidate must be cancelled';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM recovery_plans rp
        JOIN delivery_plans p ON p.id = rp.candidate_delivery_plan_id
        WHERE p.plan_code = 'PLAN-20260928-V3'
          AND rp.status = 'DECIDED' AND rp.dispatcher_decision = 'REJECT'
    ) THEN
        RAISE EXCEPTION 'Previous pending recovery must be decided';
    END IF;
    IF EXISTS (
        SELECT 1 FROM recovery_plans rp
        JOIN delivery_plans p ON p.id = rp.candidate_delivery_plan_id
        JOIN incidents i ON i.id = rp.incident_id
        WHERE p.plan_code = 'PLAN-20260928-V3' AND i.status <> 'RESOLVED'
    ) THEN
        RAISE EXCEPTION 'An incident on the superseded plan must not remain actionable';
    END IF;
END $$;

SELECT p.plan_code, p.status, p.assigned_order_count, p.vehicle_count,
       count(*) FILTER (WHERE o.risk_status = 'AT_RISK') AS at_risk_orders
FROM delivery_plans p
JOIN delivery_plan_orders po ON po.delivery_plan_id = p.id
JOIN orders o ON o.id = po.order_id
WHERE p.business_date = DATE '2026-09-28' AND p.status = 'CURRENT'
GROUP BY p.id;
