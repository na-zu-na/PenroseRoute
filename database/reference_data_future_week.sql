-- PenroseRoute deployment seed: planning facts for the next seven Singapore
-- business dates (tomorrow through day +7 at import time).
-- Run once, after create_datatable.sql, on a NEW EMPTY database only.
-- Plans, routes, stops, incidents and alerts are intentionally not fabricated:
-- create them through the normal application workflows after import.

SET search_path TO public;
BEGIN;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM locations)
       OR EXISTS (SELECT 1 FROM orders)
       OR EXISTS (SELECT 1 FROM delivery_plans) THEN
        RAISE EXCEPTION 'Future-week reference data requires an empty database; no existing data was changed';
    END IF;
END
$guard$;

INSERT INTO locations (id, location_code, display_name, address_text, latitude, longitude) VALUES
('10000000-0000-0000-0000-000000000001', 'LOC-HUB-001', 'West Hub', 'Jurong West, Singapore', 1.339700, 103.706600),
('10000000-0000-0000-0000-000000000002', 'LOC-MER-001', 'Jurong East Pickup', 'Jurong East, Singapore', 1.332900, 103.743600),
('10000000-0000-0000-0000-000000000003', 'LOC-MER-002', 'Clementi Pickup', 'Clementi, Singapore', 1.315100, 103.765100),
('10000000-0000-0000-0000-000000000004', 'LOC-MER-003', 'West Coast Pickup', 'West Coast Road, Singapore', 1.306000, 103.755000),
('10000000-0000-0000-0000-000000000005', 'LOC-CUS-001', 'Bukit Batok Delivery', 'Bukit Batok, Singapore', 1.349600, 103.749000),
('10000000-0000-0000-0000-000000000006', 'LOC-CUS-002', 'Choa Chu Kang Delivery', 'Choa Chu Kang, Singapore', 1.385400, 103.744300),
('10000000-0000-0000-0000-000000000007', 'LOC-CUS-003', 'Queenstown Delivery', 'Queenstown, Singapore', 1.294200, 103.786100),
('10000000-0000-0000-0000-000000000008', 'LOC-CUS-004', 'Bukit Panjang Delivery', 'Bukit Panjang, Singapore', 1.377400, 103.771900),
('10000000-0000-0000-0000-000000000009', 'LOC-CUS-005', 'West Coast Delivery', 'West Coast, Singapore', 1.303900, 103.759600),
('10000000-0000-0000-0000-000000000010', 'LOC-CUS-006', 'Toa Payoh Delivery', 'Toa Payoh, Singapore', 1.334200, 103.851000),
('10000000-0000-0000-0000-000000000011', 'LOC-CUS-007', 'Bishan Delivery', 'Bishan, Singapore', 1.350800, 103.848000),
('10000000-0000-0000-0000-000000000012', 'LOC-CUS-008', 'Holland Village Delivery', 'Holland Village, Singapore', 1.311500, 103.796300);

INSERT INTO merchants (id, merchant_code, name, pickup_location_id, preparation_status, operational_ready_at, default_pickup_service_seconds) VALUES
('20000000-0000-0000-0000-000000000001', 'MER-001', 'Jurong Fresh Kitchen', '10000000-0000-0000-0000-000000000002', 'READY', NULL, 180),
('20000000-0000-0000-0000-000000000002', 'MER-002', 'Clementi Daily Goods', '10000000-0000-0000-0000-000000000003', 'READY', NULL, 180),
('20000000-0000-0000-0000-000000000003', 'MER-003', 'West Coast Supply', '10000000-0000-0000-0000-000000000004', 'READY', NULL, 180);

-- Ready time is order-specific for each business day; a merchant-level
-- operational_ready_at cannot represent seven independent daily shifts.
INSERT INTO customers (id, customer_code, name, default_delivery_location_id) VALUES
('30000000-0000-0000-0000-000000000001', 'CUS-001', 'Customer A', '10000000-0000-0000-0000-000000000005'),
('30000000-0000-0000-0000-000000000002', 'CUS-002', 'Customer B', '10000000-0000-0000-0000-000000000006'),
('30000000-0000-0000-0000-000000000003', 'CUS-003', 'Customer C', '10000000-0000-0000-0000-000000000007'),
('30000000-0000-0000-0000-000000000004', 'CUS-004', 'Customer D', '10000000-0000-0000-0000-000000000008'),
('30000000-0000-0000-0000-000000000005', 'CUS-005', 'Customer E', '10000000-0000-0000-0000-000000000009'),
('30000000-0000-0000-0000-000000000006', 'CUS-006', 'Customer F', '10000000-0000-0000-0000-000000000010'),
('30000000-0000-0000-0000-000000000007', 'CUS-007', 'Customer G', '10000000-0000-0000-0000-000000000011'),
('30000000-0000-0000-0000-000000000008', 'CUS-008', 'Customer H', '10000000-0000-0000-0000-000000000012');

INSERT INTO vehicles (id, vehicle_code, name, capacity_load_units, status, current_location_id, current_location_recorded_at) VALUES
('50000000-0000-0000-0000-000000000001', 'VEH-001', 'Van Alpha', 8, 'AVAILABLE', '10000000-0000-0000-0000-000000000001', now()),
('50000000-0000-0000-0000-000000000002', 'VEH-002', 'Van Bravo', 8, 'AVAILABLE', '10000000-0000-0000-0000-000000000001', now()),
('50000000-0000-0000-0000-000000000003', 'VEH-003', 'Van Charlie', 8, 'AVAILABLE', '10000000-0000-0000-0000-000000000001', now()),
('50000000-0000-0000-0000-000000000004', 'VEH-004', 'Van Delta', 8, 'AVAILABLE', '10000000-0000-0000-0000-000000000001', now()),
('50000000-0000-0000-0000-000000000005', 'VEH-005', 'Van Echo', 8, 'AVAILABLE', '10000000-0000-0000-0000-000000000001', now());

INSERT INTO drivers (id, driver_code, name, status) VALUES
('60000000-0000-0000-0000-000000000001', 'DRV-001', 'Driver Alex', 'AVAILABLE'),
('60000000-0000-0000-0000-000000000002', 'DRV-002', 'Driver Ben', 'AVAILABLE'),
('60000000-0000-0000-0000-000000000003', 'DRV-003', 'Driver Chris', 'AVAILABLE'),
('60000000-0000-0000-0000-000000000004', 'DRV-004', 'Driver Dana', 'AVAILABLE'),
('60000000-0000-0000-0000-000000000005', 'DRV-005', 'Driver Erin', 'AVAILABLE');

WITH dates AS (
    SELECT (now() AT TIME ZONE 'Asia/Singapore')::date + day_no AS business_date
    FROM generate_series(1, 7) AS days(day_no)
), pairs(vehicle_code, driver_code) AS (
    VALUES ('VEH-001', 'DRV-001'), ('VEH-002', 'DRV-002'),
           ('VEH-003', 'DRV-003'), ('VEH-004', 'DRV-004'),
           ('VEH-005', 'DRV-005')
)
INSERT INTO vehicle_driver_assignments (
    vehicle_id, driver_id, assigned_from_at, assigned_until_at, status
)
SELECT v.id, d.id,
       (dates.business_date + time '08:00') AT TIME ZONE 'Asia/Singapore',
       (dates.business_date + time '20:00') AT TIME ZONE 'Asia/Singapore',
       'PLANNED'
FROM dates CROSS JOIN pairs
JOIN vehicles AS v ON v.vehicle_code = pairs.vehicle_code
JOIN drivers AS d ON d.driver_code = pairs.driver_code;

WITH dates AS (
    SELECT (now() AT TIME ZONE 'Asia/Singapore')::date + day_no AS business_date
    FROM generate_series(1, 7) AS days(day_no)
), templates(order_no, merchant_code, customer_code, ready_time, window_start, window_end, load_units) AS (
    VALUES
    ( 1, 'MER-001', 'CUS-001', time '09:00', time '10:00', time '14:00', 1),
    ( 2, 'MER-001', 'CUS-002', time '09:00', time '10:00', time '15:00', 2),
    ( 3, 'MER-001', 'CUS-003', time '09:20', time '10:00', time '16:00', 1),
    ( 4, 'MER-001', 'CUS-004', time '09:20', time '11:00', time '17:00', 2),
    ( 5, 'MER-002', 'CUS-005', time '09:30', time '10:00', time '15:00', 1),
    ( 6, 'MER-002', 'CUS-006', time '09:30', time '11:00', time '16:00', 2),
    ( 7, 'MER-002', 'CUS-007', time '10:00', time '11:00', time '17:00', 1),
    ( 8, 'MER-002', 'CUS-008', time '10:00', time '12:00', time '18:00', 2),
    ( 9, 'MER-003', 'CUS-001', time '10:00', time '11:00', time '17:00', 1),
    (10, 'MER-003', 'CUS-003', time '10:15', time '11:00', time '18:00', 2),
    (11, 'MER-003', 'CUS-005', time '10:30', time '12:00', time '18:00', 1),
    (12, 'MER-003', 'CUS-007', time '10:30', time '12:00', time '18:00', 2)
)
INSERT INTO orders (
    order_code, business_date, merchant_id, customer_id,
    pickup_location_id, delivery_location_id,
    pickup_ready_at, pickup_service_seconds,
    delivery_window_start_at, delivery_window_end_at,
    delivery_service_seconds, demand_load_units,
    execution_status, risk_status
)
SELECT 'ORD-' || to_char(dates.business_date, 'YYYYMMDD') || '-' || lpad(templates.order_no::text, 3, '0'),
       dates.business_date, m.id, c.id,
       m.pickup_location_id, c.default_delivery_location_id,
       (dates.business_date + templates.ready_time) AT TIME ZONE 'Asia/Singapore', 180,
       (dates.business_date + templates.window_start) AT TIME ZONE 'Asia/Singapore',
       (dates.business_date + templates.window_end) AT TIME ZONE 'Asia/Singapore',
       240, templates.load_units, 'PLANNED', 'NORMAL'
FROM dates CROSS JOIN templates
JOIN merchants AS m ON m.merchant_code = templates.merchant_code
JOIN customers AS c ON c.customer_code = templates.customer_code;

COMMIT;

SELECT business_date, count(*) AS order_count
FROM orders GROUP BY business_date ORDER BY business_date;
