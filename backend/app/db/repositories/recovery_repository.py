from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import RecoveryPlan


class RecoveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_recovery_attempts(self, incident_id: UUID) -> list[RecoveryPlan]:
        statement = (
            select(RecoveryPlan)
            .where(RecoveryPlan.incident_id == incident_id)
            .options(
                joinedload(RecoveryPlan.base_delivery_plan),
                joinedload(RecoveryPlan.candidate_delivery_plan),
            )
            .order_by(RecoveryPlan.attempt_no)
        )
        return list(self.session.scalars(statement))

    def lock_recovery_plan_for_decision(
        self, recovery_plan_id: UUID
    ) -> RecoveryPlan | None:
        statement = (
            select(RecoveryPlan)
            .where(RecoveryPlan.id == recovery_plan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement)

    def add_recovery_plan(self, recovery_plan: RecoveryPlan) -> None:
        self.session.add(recovery_plan)

    def flush(self) -> None:
        self.session.flush()
