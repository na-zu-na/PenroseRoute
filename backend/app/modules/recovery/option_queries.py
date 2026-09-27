"""Read persisted ranked options without rerunning risk checks or optimization."""
from app.core.errors import NotFound
from app.db.models.planning import DeliveryPlanStatus
from app.db.models.recovery import RecoveryPlanStatus
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.recovery_repository import RecoveryRepository
from app.modules.recovery.queries import RecoveryQueryService


def list_options(session, incident_id):
    incident = IncidentRepository(session).get_incident_for_query(incident_id)
    if incident is None:
        raise NotFound(code='INCIDENT_NOT_FOUND', message='Incident was not found')
    attempts = RecoveryRepository(session).get_recovery_attempts(incident_id)
    batches = [a for a in attempts if (a.solver_validation_summary or {}).get('options')]
    latest = attempts[-1] if attempts else None
    if latest is None or not (latest.solver_validation_summary or {}).get('options'):
        summary = latest.solver_validation_summary or {} if latest else {}
        if latest is None:
            outcome = 'NOT_STARTED'
        elif latest.solver_status is None:
            outcome = 'GENERATING'
        elif str(latest.solver_status) == 'ERROR':
            outcome = 'FAILED'
        elif str(latest.solver_status) == 'INFEASIBLE':
            outcome = 'NO_FEASIBLE_RECOVERY'
        else:
            outcome = 'LEGACY_RECOVERY'
        return dict(incident_id=incident_id, outcome=outcome,
                    batch_id=summary.get('options_batch_id'),
                    candidates=[], recommended_recovery_plan_id=None, ranking_policy=None,
                    manual_intervention_required=outcome in ('FAILED', 'NO_FEASIBLE_RECOVERY'))
    batch_id = batches[-1].solver_validation_summary['options']['batch_id']
    candidates = []
    for attempt in batches:
        option = attempt.solver_validation_summary['options']
        if option['batch_id'] != batch_id or option.get('filtered_by_limit'):
            continue
        data = RecoveryQueryService._detail(attempt)
        data.update(priority=option['priority'], metrics=option['metrics'],
                    strategy=option['strategy'], ranking_reason=option['ranking_reason'],
                    review_deadline_at=option.get('review_deadline_at'),
                    reviewable=(attempt.status == RecoveryPlanStatus.PENDING_REVIEW
                        and attempt.base_delivery_plan.status == DeliveryPlanStatus.CURRENT
                        and attempt.candidate_delivery_plan.status == DeliveryPlanStatus.CANDIDATE))
        candidates.append(data)
    candidates.sort(key=lambda x: x['priority'])
    active = [x for x in candidates if x['reviewable']]
    return dict(incident_id=incident_id, batch_id=batch_id,
                outcome='PENDING_REVIEW' if active else 'DECIDED', candidates=candidates,
                recommended_recovery_plan_id=active[0]['recovery_plan_id'] if active else None,
                ranking_policy=batches[-1].solver_validation_summary['options']['ranking_policy'],
                manual_intervention_required=not active and incident.status != 'RESOLVED')
