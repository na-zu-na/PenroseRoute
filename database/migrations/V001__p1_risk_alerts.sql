-- P1 U06 alert persistence. Apply only through database/apply_migrations.py.
-- This script runs inside the runner's transaction; do not add BEGIN/COMMIT.

CREATE TABLE public.risk_alerts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_plan_id uuid NOT NULL REFERENCES public.delivery_plans(id) ON DELETE RESTRICT,
    order_id uuid NOT NULL REFERENCES public.orders(id) ON DELETE RESTRICT,
    business_date date NOT NULL,
    risk_type varchar(32) NOT NULL,
    status varchar(16) NOT NULL,
    evidence jsonb NOT NULL,
    detected_at timestamptz NOT NULL,
    last_evaluated_at timestamptz NOT NULL,
    resolved_at timestamptz,
    CONSTRAINT ck_risk_alerts_type CHECK (risk_type = 'DELIVERY_WINDOW'),
    CONSTRAINT ck_risk_alerts_status CHECK (status IN ('ACTIVE', 'RESOLVED')),
    CONSTRAINT ck_risk_alerts_times CHECK (
        last_evaluated_at >= detected_at
        AND (resolved_at IS NULL OR resolved_at >= detected_at)
        AND ((status = 'ACTIVE' AND resolved_at IS NULL)
             OR (status = 'RESOLVED' AND resolved_at IS NOT NULL))
    )
);

CREATE UNIQUE INDEX uq_risk_alerts_active_plan_order_type
    ON public.risk_alerts (delivery_plan_id, order_id, risk_type)
    WHERE status = 'ACTIVE';

CREATE INDEX ix_risk_alerts_business_date_status
    ON public.risk_alerts (business_date, status, detected_at DESC);

-- No column default: a writer must take advisory lock (55120, 1) before nextval.
CREATE SEQUENCE public.risk_alert_change_cursor_seq AS bigint CACHE 1;

CREATE TABLE public.risk_alert_changes (
    change_id bigint PRIMARY KEY,
    alert_id uuid NOT NULL REFERENCES public.risk_alerts(id) ON DELETE RESTRICT,
    change_type varchar(16) NOT NULL,
    recorded_at timestamptz NOT NULL,
    evidence_snapshot jsonb NOT NULL,
    CONSTRAINT ck_risk_alert_changes_type CHECK (change_type IN ('CREATED', 'UPDATED', 'RESOLVED'))
);

CREATE INDEX ix_risk_alert_changes_alert ON public.risk_alert_changes (alert_id, change_id);
