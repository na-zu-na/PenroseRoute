"""Single-owner periodic alert reconciliation for Current delivery plans."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Event
from typing import Callable

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.alerts import AlertStatus, RiskAlert
from app.db.models.planning import DeliveryPlan, DeliveryPlanStatus, RouteStop, StopStatus, StopType, VehicleRoute
from app.modules.operations.alerts import AlertService


logger = logging.getLogger(__name__)
OWNER_LOCK = (55120, 2)


@dataclass(frozen=True)
class AlertScanResult:
    scanned_dates: tuple[date, ...]
    created_count: int
    updated_count: int
    resolved_count: int


def scan_once(session_factory: Callable[[], Session], now: datetime) -> AlertScanResult:
    """Evaluate dates with unfinished Current deliveries or active alerts."""
    with session_factory() as session, session.begin():
        unfinished = set(session.scalars(
            select(DeliveryPlan.business_date).join(VehicleRoute).join(RouteStop).where(
                DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
                RouteStop.stop_type == StopType.DELIVERY,
                RouteStop.status != StopStatus.COMPLETED,
            ).distinct()
        ))
        active = set(session.scalars(
            select(RiskAlert.business_date).where(RiskAlert.status == AlertStatus.ACTIVE).distinct()
        ))
    dates = tuple(sorted(unfinished | active))
    created = updated = resolved = 0
    for business_date in dates:
        with session_factory() as session:
            result = AlertService(session).evaluate_business_date(business_date, now)
        created += result.created_count
        updated += result.updated_count
        resolved += result.resolved_count
    return AlertScanResult(dates, created, updated, resolved)


def _owner_state(connection: Connection) -> tuple[int, bool]:
    if connection.invalidated:
        raise RuntimeError("Alert worker ownership connection was invalidated")
    row = connection.exec_driver_sql(
        "SELECT pg_backend_pid(), EXISTS ("
        "SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() "
        "AND classid=55120 AND objid=2 AND objsubid=2 AND granted)"
    ).one()
    connection.commit()
    return int(row[0]), bool(row[1])


def run_worker(
    session_factory: sessionmaker,
    interval_seconds: int,
    stop_event: Event,
    clock: Callable[[], datetime],
    sleep: Callable[[int], object],
) -> None:
    """Retry ownership; scan only on the pinned PostgreSQL connection."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    engine = session_factory.kw["bind"]
    while not stop_event.is_set():
        try:
            with engine.connect() as owner:
                owner_pid, acquired = owner.exec_driver_sql(
                    "SELECT pg_backend_pid(), pg_try_advisory_lock(%s, %s)", OWNER_LOCK
                ).one()
                if not acquired:
                    owner.commit()
                    sleep(interval_seconds)
                    continue
                try:
                    owner.commit()
                    while not stop_event.is_set():
                        pid, held = _owner_state(owner)
                        if pid != owner_pid or not held:
                            break

                        def owned_session() -> Session:
                            current_pid, still_held = _owner_state(owner)
                            if current_pid != owner_pid or not still_held:
                                raise RuntimeError("Alert worker lost ownership during scan")
                            return session_factory(bind=owner)

                        # Every ORM read/write transaction uses the owner connection.
                        scan_once(owned_session, clock())
                        if stop_event.is_set():
                            break
                        sleep(interval_seconds)
                finally:
                    if not owner.invalidated:
                        try:
                            if owner.in_transaction():
                                owner.rollback()
                            pid, unlocked = owner.exec_driver_sql(
                                "SELECT pg_backend_pid(), pg_advisory_unlock(%s, %s)", OWNER_LOCK
                            ).one()
                            owner.commit()
                            if pid != owner_pid or not unlocked:
                                logger.warning("Alert worker ownership was already lost")
                        except Exception:
                            logger.exception("Could not release alert worker ownership lock")
        except Exception:
            if not stop_event.is_set():
                logger.exception("Alert worker scan or ownership failed; reacquiring before next scan")
        if not stop_event.is_set():
            sleep(interval_seconds)


def main() -> None:
    from app.core.config import get_settings
    from app.db.session import SessionLocal

    logging.basicConfig(level=logging.INFO)
    stop = Event()
    try:
        run_worker(SessionLocal, get_settings().alert_scan_interval_seconds,
                   stop, lambda: datetime.now(timezone.utc), stop.wait)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
