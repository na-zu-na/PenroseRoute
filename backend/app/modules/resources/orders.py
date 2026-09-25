"""Order application operations; execution facts advance only forwards."""
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessError, Conflict, NotFound
from app.db.models import Order
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.repositories.resource_repository import ResourceRepository


EXECUTION_PATH = tuple(OrderExecutionStatus)


class OrderService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ResourceRepository(session)

    def list_orders(
        self, *, page: int, page_size: int, business_date: date | None,
        execution_status: str | None, risk_status: str | None, merchant_id: UUID | None,
    ) -> tuple[list[Order], int]:
        filters = dict(
            business_date=business_date, execution_status=execution_status,
            risk_status=risk_status, merchant_id=merchant_id,
        )
        return (
            self.repository.list_orders(page=page, page_size=page_size, **filters),
            self.repository.count_orders(**filters),
        )

    def get_order(self, order_id: UUID) -> Order:
        order = self.repository.get_order_by_id(order_id)
        if order is None:
            raise NotFound(code="ORDER_NOT_FOUND", message="Order was not found")
        return order

    def current_plan(self, order_id: UUID):
        return self.repository.get_current_order_membership(order_id)

    def create_order(self, values: dict) -> Order:
        merchant, customer = self._parties_and_locations(values)
        try:
            order = Order(
                order_code=values["order_code"], business_date=values["business_date"],
                merchant=merchant, customer=customer,
                pickup_location=merchant.pickup_location,
                delivery_location=customer.default_delivery_location,
                pickup_ready_at=values["pickup_ready_at"],
                pickup_service_seconds=values["pickup_service_seconds"],
                delivery_window_start_at=values["delivery_window_start_at"],
                delivery_window_end_at=values["delivery_window_end_at"],
                delivery_service_seconds=values["delivery_service_seconds"],
                demand_load_units=values["demand_load_units"],
                execution_status=OrderExecutionStatus.PLANNED,
                risk_status=OrderRiskStatus.NORMAL,
            )
            self.repository.add_order(order)
            self.repository.flush()
            self.session.commit()
            return order
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(code="RESOURCE_CONFLICT", message="Order code already exists") from error

    def replace_order(self, order_id: UUID, values: dict) -> Order:
        try:
            order = self.repository.lock_order_by_id(order_id)
            if order is None:
                raise NotFound(code="ORDER_NOT_FOUND", message="Order was not found")
            if order.execution_status is not OrderExecutionStatus.PLANNED or self.current_plan(order_id):
                raise Conflict(code="ORDER_NOT_EDITABLE", message="Planned or executing order cannot be edited")
            merchant, customer = self._parties_and_locations(values)
            order.business_date = values["business_date"]
            order.merchant, order.customer = merchant, customer
            order.pickup_location = merchant.pickup_location
            order.delivery_location = customer.default_delivery_location
            for name in (
                "pickup_ready_at", "pickup_service_seconds", "delivery_window_start_at",
                "delivery_window_end_at", "delivery_service_seconds", "demand_load_units",
            ):
                setattr(order, name, values[name])
            order.updated_at = datetime.now(timezone.utc)
            self.repository.flush()
            self.session.commit()
            return order
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(code="RESOURCE_CONFLICT", message="Order update conflicts with existing data") from error

    def advance_execution(self, order_id: UUID, target: str) -> Order:
        target = OrderExecutionStatus(target)
        order = self.repository.lock_order_by_id(order_id)
        if order is None:
            raise NotFound(code="ORDER_NOT_FOUND", message="Order was not found")
        if order.execution_status == target:
            return self.get_order(order_id)
        current_index = EXECUTION_PATH.index(order.execution_status)
        if current_index + 1 >= len(EXECUTION_PATH) or EXECUTION_PATH[current_index + 1] != target:
            raise Conflict(
                code="INVALID_ORDER_STATE_TRANSITION",
                message="Order execution status must advance one step and cannot go backwards",
            )
        order.execution_status = target
        order.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return self.get_order(order_id)

    def _parties_and_locations(self, values: dict):
        merchant = self.repository.get_merchant_by_id(values["merchant_id"])
        if merchant is None:
            raise NotFound(code="MERCHANT_NOT_FOUND", message="Merchant was not found")
        customer = self.repository.get_customer_by_id(values["customer_id"])
        if customer is None:
            raise NotFound(code="CUSTOMER_NOT_FOUND", message="Customer was not found")
        for provided, actual in (
            (values["pickup_location"], merchant.pickup_location),
            (values["delivery_location"], customer.default_delivery_location),
        ):
            if (
                provided["location_code"] != actual.location_code
                or provided["latitude"] != actual.latitude
                or provided["longitude"] != actual.longitude
            ):
                raise BusinessError(
                    code="VALIDATION_ERROR",
                    message="Order location must match the current Merchant or Customer location",
                )
        return merchant, customer
