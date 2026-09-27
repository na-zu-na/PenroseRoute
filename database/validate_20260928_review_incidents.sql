-- Read-only acceptance check for three actionable 2026-09-28 incidents.
DO $$
DECLARE
    base_id uuid;
BEGIN
    SELECT id INTO base_id FROM delivery_plans
    WHERE plan_code = 'PLAN-20260928-V4' AND status = 'CURRENT';
    IF base_id IS NULL THEN
        RAISE EXCEPTION 'V4 must remain Current';
    END IF;
    IF (
        SELECT count(*) FROM incidents
        WHERE delivery_plan_id = base_id
          AND incident_type = 'VEHICLE_UNAVAILABLE' AND status = 'REVIEW'
    ) <> 3 THEN
        RAISE EXCEPTION 'Expected three reviewable vehicle incidents';
    END IF;
    IF (
        SELECT count(DISTINCT vehicle_id) FROM incidents
        WHERE delivery_plan_id = base_id
          AND incident_type = 'VEHICLE_UNAVAILABLE' AND status = 'REVIEW'
    ) <> 3 THEN
        RAISE EXCEPTION 'Review incidents must have distinct source vehicles';
    END IF;
    IF (
        SELECT count(*) FROM incidents i
        WHERE i.delivery_plan_id = base_id AND i.status = 'REVIEW'
          AND EXISTS (
              SELECT 1 FROM incident_affected_orders a
              WHERE a.incident_id = i.id AND a.requires_replanning
          )
          AND EXISTS (
              SELECT 1 FROM recovery_plans rp
              JOIN delivery_plans c ON c.id = rp.candidate_delivery_plan_id
              WHERE rp.incident_id = i.id AND rp.base_delivery_plan_id = base_id
                AND rp.status = 'PENDING_REVIEW' AND rp.solver_status = 'FEASIBLE'
                AND rp.validation_status = 'VALID' AND rp.replanning_scope = 'ALL_REMAINING'
                AND c.status = 'CANDIDATE' AND c.validation_status = 'VALID'
                AND c.assigned_order_count = 10 AND c.unassigned_order_count = 0
                AND c.parent_plan_id = base_id
          )
    ) <> 3 THEN
        RAISE EXCEPTION 'Each REVIEW incident needs a valid pending full-scope Candidate';
    END IF;
    IF EXISTS (
        SELECT 1 FROM recovery_plans rp
        JOIN incidents i ON i.id = rp.incident_id
        JOIN vehicle_routes r ON r.delivery_plan_id = rp.candidate_delivery_plan_id
        JOIN vehicles v ON v.id = r.vehicle_id
        WHERE i.delivery_plan_id = base_id AND i.status = 'REVIEW'
          AND rp.status = 'PENDING_REVIEW' AND v.status = 'UNAVAILABLE'
    ) THEN
        RAISE EXCEPTION 'A pending Candidate still uses an unavailable vehicle';
    END IF;
END $$;

SELECT i.incident_code, i.status, v.vehicle_code AS broken_vehicle,
       rp.recovery_code, rp.replanning_scope, c.plan_code AS candidate_plan,
       c.assigned_order_count
FROM incidents i
JOIN vehicles v ON v.id = i.vehicle_id
JOIN recovery_plans rp ON rp.incident_id = i.id AND rp.status = 'PENDING_REVIEW'
JOIN delivery_plans c ON c.id = rp.candidate_delivery_plan_id
WHERE i.delivery_plan_id = (
    SELECT id FROM delivery_plans WHERE plan_code = 'PLAN-20260928-V4'
)
ORDER BY v.vehicle_code;
