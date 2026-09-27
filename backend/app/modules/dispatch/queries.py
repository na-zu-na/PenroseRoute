"""Read-only projections. Sessions are closed before any Agent/model invocation."""
from datetime import date, datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import VehicleRoute
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository


def iso(value):
    return value.isoformat() if value else None


def count_phrase(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def human_time(value: str | datetime | None) -> str | None:
    if not value:
        return None
    try:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(ZoneInfo("Asia/Singapore"))
    return f"{local.day} {local.strftime('%B')} at {local.strftime('%I:%M %p').lstrip('0')}"


ALERT_REASONS = {
    "PREDICTED_MISS": "the estimated arrival was at or after the delivery window closed",
    "APPROACHING_WINDOW": "the estimated arrival was close to the end of the delivery window",
}



class DispatchQueries:
    def __init__(self, session_factory, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.clock = session_factory, clock

    def resources(self, business_date: date):
        at = self.clock()
        with self.sessions() as session:
            pairs = FleetRepository(session).get_active_vehicle_driver_pairs(at)
            current = PlanRepository(session).get_current_plan(business_date)
            busy = set(session.scalars(select(VehicleRoute.vehicle_id).where(
                VehicleRoute.delivery_plan_id == current.id,
                VehicleRoute.status.in_(("PLANNED", "ACTIVE")),
            ))) if current else set()
            items = []
            for pair in pairs:
                v, d = pair.vehicle, pair.driver
                available = v.status == "AVAILABLE" and d.status == "AVAILABLE" and v.id not in busy
                items.append({"vehicle_id": str(v.id), "vehicle_code": v.vehicle_code,
                    "driver_id": str(d.id), "driver_code": d.driver_code,
                    "assignment_id": str(pair.id), "capacity_load_units": v.capacity_load_units,
                    "vehicle_status": str(v.status), "driver_status": str(d.status),
                    "location_id": str(v.current_location_id), "location_recorded_at": iso(v.current_location_recorded_at),
                    "idle": available, "on_current_plan": v.id in busy})
            idle_count = sum(item["idle"] for item in items)
            availability_text = (
                f"I found {count_phrase(len(items), 'active vehicle-driver pair')}; "
                + (f"{count_phrase(idle_count, 'pair')} currently {'appears' if idle_count == 1 else 'appear'} idle." if idle_count else "No pairs appear idle.")
            ) if items else "No active vehicle-driver pairs are available. No pairs appear idle."
            return {"business_date": iso(business_date), "as_of": iso(at), "observed_at": iso(at),
                "missing_reasons": [], "facts": [{"id": "resources", "text": f"{availability_text} This is based on current availability, not future shifts or remaining cargo capacity."}],
                "availability_basis": "Active vehicle-driver pairs and current status; not future shifts or remaining load",
                "total": len(items), "idle_count": idle_count,
                "items": items[:100], "truncated": len(items) > 100}

    def operations(self, business_date: date):
        from dataclasses import asdict
        from fastapi.encoders import jsonable_encoder
        from app.core.errors import NotFound
        from app.modules.operations.alert_queries import AlertQueryService
        from app.modules.operations.queries import OperationsQueryService
        with self.sessions() as session:
            alert_summary = AlertQueryService(session).active_summary(business_date)
            try:
                data = jsonable_encoder(asdict(OperationsQueryService(session).dashboard(business_date)))
            except NotFound as error:
                return self._envelope({
                    "current_plan": None,
                    "active_alert_count": alert_summary.active_count,
                    "alert_reason_counts": alert_summary.reason_counts,
                    "alerts_as_of": iso(alert_summary.as_of),
                }, business_date=business_date, missing=[error.code], facts=[
                        {"id": "current_plan", "text": f"There is no active delivery plan for {business_date.strftime('%d %B %Y').lstrip('0')}, so I cannot summarize its routes or orders."},
                        {"id": "alerts", "text": f"I can still see {count_phrase(alert_summary.active_count, 'active risk alert')}. Alert totals may differ from the number of at-risk orders."},
                    ])
        plan = data["current_plan"]
        data["unfinished_order_count"] = data["orders"]["total"] - data["orders"]["completed"]
        data.update(
            active_alert_count=alert_summary.active_count,
            alert_reason_counts=alert_summary.reason_counts,
            alerts_as_of=iso(alert_summary.as_of),
        )
        unfinished = data["unfinished_order_count"]
        at_risk = data["orders"]["at_risk"]
        incidents = data["open_incidents"]
        reviews = data["pending_recovery_reviews"]
        operations_text = (
            f"{count_phrase(unfinished, 'order')} {'remains' if unfinished == 1 else 'remain'} unfinished. "
            + (f"{count_phrase(at_risk, 'order')} may miss {'its delivery window' if at_risk == 1 else 'their delivery windows'}." if at_risk else "No orders are currently at risk.")
        )
        review_text = (
            (f"{count_phrase(incidents, 'incident')} still {'needs' if incidents == 1 else 'need'} attention" if incidents else "No unresolved incidents need attention")
            + ", and "
            + (f"{count_phrase(reviews, 'recovery option')} {'awaits' if reviews == 1 else 'await'} dispatcher review" if reviews else "no recovery options need review")
            + (". Proposed changes are not active yet." if reviews else ".")
        )
        return self._envelope(data, business_date=business_date, facts=[
            {"id": "current_plan", "text": f"For {business_date.strftime('%d %B %Y').lstrip('0')}, delivery plan version {plan['version_no']} is in use.", "plan_id": plan["delivery_plan_id"]},
            {"id": "operations", "text": operations_text, "plan_id": plan["delivery_plan_id"]},
            {"id": "reviews", "text": review_text, "plan_id": plan["delivery_plan_id"]},
            {"id": "alerts", "text": f"There are {count_phrase(alert_summary.active_count, 'active risk alert')}. Alert totals and at-risk order totals can differ."},
        ])

    def proposal(self, recovery_id: UUID):
        from fastapi.encoders import jsonable_encoder
        from app.modules.recovery.queries import RecoveryQueryService
        with self.sessions() as session:
            data = jsonable_encoder(RecoveryQueryService(session).get_attempt(recovery_id))
        if data["candidate_delivery_plan_id"] is None:
            reason = {"INFEASIBLE": "no feasible route was found", "ERROR": "the route calculation failed"}.get(data["solver_status"], "the result was not valid for review")
            message = f"This recovery attempt did not produce an option for review because {reason}. The current plan has not changed."
        elif data["status"] == "PENDING_REVIEW":
            message = "A recovery option is ready for dispatcher review. It has not replaced the current plan."
        else:
            decision = {"APPROVE": "approved", "REJECT": "rejected", "MODIFY": "sent back for another attempt"}.get(data["dispatcher_decision"])
            message = f"The dispatcher {decision} this recovery option." if decision else "This recovery option is not awaiting review."
        scope = {"AFFECTED_ROUTE": "the affected route", "CROSS_ROUTE": "multiple routes", "ALL_REMAINING": "all remaining work"}.get(data["replanning_scope"])
        if scope:
            message += f" The attempt considered {scope}."
        return self._envelope(data, facts=[{
            "id": "recovery", "recovery_plan_id": str(recovery_id),
            "plan_id": data["candidate_delivery_plan_id"],
            "text": message,
        }])

    def compare(self, recovery_id: UUID):
        from dataclasses import asdict
        from fastapi.encoders import jsonable_encoder
        from app.modules.planning.comparison import PlanComparisonService
        with self.sessions() as session:
            comparison = PlanComparisonService(session).compare_recovery(recovery_id)
            data = jsonable_encoder(asdict(comparison))
        reassigned = comparison.reassigned_order_count
        frozen = len(comparison.frozen_completed_order_ids)
        handovers = len({stop.order_id for stop in comparison.stop_changes if stop.stop_type == "HANDOVER" and stop.change_type != "REMOVED"})
        eta_unknown = sum(order.eta_unavailable_reason is not None for order in comparison.orders)
        facts = [
            {"id": "assignments", "text": f"Compared with the original plan, this option reassigns {count_phrase(reassigned, 'order')}."},
        ]
        if frozen:
            facts.append({"id": "completed", "text": f"{count_phrase(frozen, 'completed order')} {'remains' if frozen == 1 else 'remain'} protected from changes."})
        if handovers:
            facts.append({"id": "handover", "text": f"{count_phrase(handovers, 'order')} {'requires' if handovers == 1 else 'require'} a cargo handover before delivery can continue."})
        if eta_unknown:
            facts.append({"id": "eta", "text": f"The delivery-time change cannot be compared reliably for {count_phrase(eta_unknown, 'order')}."})
        metrics = comparison.remaining_metrics
        if metrics.delta_distance_meters is None or metrics.delta_duration_seconds is None:
            facts.append({"id": "travel", "text": "The remaining distance and travel time cannot be compared reliably from the available snapshots."})
        else:
            distance = abs(metrics.delta_distance_meters) / 1000
            minutes = abs(metrics.delta_duration_seconds) / 60
            distance_direction = "longer" if metrics.delta_distance_meters > 0 else "shorter" if metrics.delta_distance_meters < 0 else "unchanged"
            duration_direction = "longer" if metrics.delta_duration_seconds > 0 else "shorter" if metrics.delta_duration_seconds < 0 else "unchanged"
            facts.append({"id": "travel", "text": f"The remaining route is {distance:.1f} km {distance_direction} and about {minutes:.0f} minutes {duration_direction}."})
        facts.append({"id": "review", "text": "This option can be reviewed, but only dispatcher approval can make it active." if comparison.reviewable else "This is a historical comparison, not an option that can still be approved."})
        return self._envelope(data, business_date=comparison.business_date, facts=[{
            "id": fact["id"], "text": fact["text"], "recovery_plan_id": str(recovery_id),
            "plan_id": str(comparison.candidate_plan_id), "comparison_at": data["comparison_at"],
        } for fact in facts])

    def explain_alert(self, business_date, order_id=None, alert_id=None):
        from dataclasses import asdict
        from fastapi.encoders import jsonable_encoder
        from app.integrations.agent.contracts import RecoveryError
        from app.modules.operations.alert_queries import AlertQueryService

        with self.sessions() as session:
            service = AlertQueryService(session)
            if alert_id:
                alert = service.get_alert(alert_id)
                alerts = [alert] if alert and alert.business_date == business_date else []
            else:
                alerts = service.list_alerts_for_order(business_date, order_id)
            if order_id:
                alerts = [alert for alert in alerts if alert.order_id == order_id]
            if not alerts:
                raise RecoveryError(
                    "ALERT_NOT_FOUND",
                    f"No active or historical alert matches business date {business_date} and the selected ID.",
                    404,
                )

            items, facts = [], []
            for alert in alerts:
                changes = service.list_changes_for_alert(alert.id)
                item = jsonable_encoder(asdict(alert))
                item["changes"] = jsonable_encoder([asdict(change) for change in changes])
                items.append(item)
                initial = next((change.evidence_snapshot for change in changes if change.change_type == "CREATED"), alert.evidence)
                reason = ALERT_REASONS.get(initial.get("reason_category"), "a delivery-window risk was detected")
                facts.append({
                    "id": f"alert:{alert.id}:status",
                    "order_id": str(alert.order_id), "alert_id": str(alert.id),
                    "text": f"This order received a delivery-window alert because {reason}. The alert is {'still active' if alert.status == 'ACTIVE' else 'now resolved'}.",
                })
                arrival = human_time(initial.get("estimated_arrival_at"))
                deadline = human_time(initial.get("delivery_window_end_at"))
                if arrival and deadline:
                    facts.append({
                        "id": f"alert:{alert.id}:timing",
                        "order_id": str(alert.order_id), "alert_id": str(alert.id),
                        "text": f"At the time, arrival was estimated for {arrival}, while the delivery window closed on {deadline} (Singapore time).",
                    })
                if len(changes) > 1:
                    facts.append({"id": f"alert:{alert.id}:history", "order_id": str(alert.order_id),
                        "alert_id": str(alert.id), "text": f"The alert has {len(changes)} recorded changes, including its latest {'resolution' if alert.status == 'RESOLVED' else 'assessment'}."})

        return self._envelope({
            "order_id": str(order_id) if order_id else None,
            "alert_id": str(alert_id) if alert_id else None,
            "alerts": items,
        }, business_date=business_date, facts=facts)

    def _envelope(self, data, *, business_date=None, missing=(), facts=()):
        return {**data, "business_date": str(business_date) if business_date else data.get("business_date"),
                "as_of": self.clock().isoformat(), "truncated": False,
                "missing_reasons": list(missing), "facts": list(facts)}
