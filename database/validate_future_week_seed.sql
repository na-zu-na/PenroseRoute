-- Read-only acceptance check for reference_data_future_week.sql.
-- Run on the freshly seeded database on the same Singapore calendar day.
DO $check$
DECLARE
    first_day date := (now() AT TIME ZONE 'Asia/Singapore')::date + 1;
BEGIN
    IF (SELECT count(*) FROM public.locations) <> 12
       OR (SELECT count(*) FROM public.merchants) <> 3
       OR (SELECT count(*) FROM public.customers) <> 8
       OR (SELECT count(*) FROM public.vehicles) <> 5
       OR (SELECT count(*) FROM public.drivers) <> 5 THEN
        RAISE EXCEPTION 'Future-week reference resource counts are incorrect';
    END IF;

    IF EXISTS (
        SELECT 1 FROM generate_series(0, 6) AS days(offset_no)
        WHERE (SELECT count(*) FROM public.orders
               WHERE business_date = first_day + days.offset_no) <> 12
           OR (SELECT count(*) FROM public.vehicle_driver_assignments
               WHERE (assigned_from_at AT TIME ZONE 'Asia/Singapore')::date = first_day + days.offset_no) <> 5
    ) OR (SELECT count(*) FROM public.orders) <> 84
      OR (SELECT count(*) FROM public.vehicle_driver_assignments) <> 35 THEN
        RAISE EXCEPTION 'Future-week orders or daily vehicle-driver pairs are missing';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.orders AS o
        JOIN public.merchants AS m ON m.id = o.merchant_id
        JOIN public.customers AS c ON c.id = o.customer_id
        WHERE o.pickup_location_id <> m.pickup_location_id
           OR o.delivery_location_id <> c.default_delivery_location_id
           OR o.execution_status <> 'PLANNED'
           OR o.risk_status <> 'NORMAL'
           OR (o.pickup_ready_at AT TIME ZONE 'Asia/Singapore')::date <> o.business_date
           OR (o.delivery_window_end_at AT TIME ZONE 'Asia/Singapore')::date <> o.business_date
    ) THEN
        RAISE EXCEPTION 'An order has invalid merchant, customer, status, or business-day facts';
    END IF;

    IF (SELECT count(*) FROM public.delivery_plans) <> 0
       OR (SELECT count(*) FROM public.incidents) <> 0
       OR (SELECT count(*) FROM public.recovery_plans) <> 0 THEN
        RAISE EXCEPTION 'Plans and incidents must be created by the application workflows';
    END IF;
END
$check$;
