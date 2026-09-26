"""Best-effort alert refresh after a source business transaction commits."""

import logging
from datetime import date, datetime

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.modules.operations.alerts import AlertService


logger = logging.getLogger(__name__)


def refresh_alerts_for_business_date(session: Session, business_date: date, now: datetime) -> None:
    """Use a clean transaction; a failed refresh must not undo its source command."""
    bind = session.get_bind()
    if isinstance(bind, Connection) and bind.in_transaction():
        # A savepoint release is not a commit of the externally owned transaction.
        logger.debug("Alert refresh deferred until outer transaction commits for %s", business_date)
        return
    engine = bind.engine if isinstance(bind, Connection) else bind
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as refresh_session:
            AlertService(refresh_session).evaluate_business_date(business_date, now)
    except Exception:
        logger.exception("Alert refresh failed after committed business event for %s", business_date)
