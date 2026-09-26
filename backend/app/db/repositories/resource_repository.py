from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Customer, Location, Merchant, Order
from app.db.models.planning import DeliveryPlan, DeliveryPlanOrder, DeliveryPlanStatus


class ResourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_orders_for_business_date(self, business_date: date) -> list[Order]:
        statement = (
            select(Order)
            .where(Order.business_date == business_date)
            .options(
                joinedload(Order.merchant).joinedload(Merchant.pickup_location),
                joinedload(Order.customer).joinedload(
                    Customer.default_delivery_location
                ),
                joinedload(Order.pickup_location),
                joinedload(Order.delivery_location),
            )
            .order_by(Order.order_code)
        )
        return list(self.session.scalars(statement))

    def list_orders(
        self, *, page: int, page_size: int, business_date: date | None = None,
        execution_status: str | None = None, risk_status: str | None = None,
        merchant_id: UUID | None = None,
    ) -> list[Order]:
        statement = select(Order).options(
            joinedload(Order.merchant), joinedload(Order.customer),
            joinedload(Order.pickup_location), joinedload(Order.delivery_location),
        )
        statement = self._filter_orders(statement, business_date, execution_status, risk_status, merchant_id)
        return list(self.session.scalars(statement.order_by(Order.order_code).offset((page - 1) * page_size).limit(page_size)))

    def count_orders(
        self, *, business_date: date | None = None,
        execution_status: str | None = None, risk_status: str | None = None,
        merchant_id: UUID | None = None,
    ) -> int:
        statement = self._filter_orders(
            select(func.count()).select_from(Order), business_date,
            execution_status, risk_status, merchant_id,
        )
        return int(self.session.scalar(statement) or 0)

    @staticmethod
    def _filter_orders(statement, business_date, execution_status, risk_status, merchant_id):
        if business_date is not None:
            statement = statement.where(Order.business_date == business_date)
        if execution_status is not None:
            statement = statement.where(Order.execution_status == execution_status)
        if risk_status is not None:
            statement = statement.where(Order.risk_status == risk_status)
        if merchant_id is not None:
            statement = statement.where(Order.merchant_id == merchant_id)
        return statement

    def get_order_by_id(self, order_id: UUID) -> Order | None:
        return self.session.scalar(
            select(Order).where(Order.id == order_id).options(
                joinedload(Order.merchant), joinedload(Order.customer),
                joinedload(Order.pickup_location), joinedload(Order.delivery_location),
            )
        )

    def lock_orders_by_ids(self, order_ids: list[UUID]) -> list[Order]:
        if not order_ids:
            return []
        return list(self.session.scalars(
            select(Order).where(Order.id.in_(order_ids)).order_by(Order.id)
            .with_for_update().execution_options(populate_existing=True)
        ))

    def get_current_order_membership(self, order_id: UUID) -> tuple[DeliveryPlanOrder, DeliveryPlan] | None:
        return self.session.execute(
            select(DeliveryPlanOrder, DeliveryPlan)
            .join(DeliveryPlan, DeliveryPlanOrder.delivery_plan_id == DeliveryPlan.id)
            .options(joinedload(DeliveryPlanOrder.vehicle_route))
            .where(
                DeliveryPlanOrder.order_id == order_id,
                DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
            )
        ).one_or_none()

    def get_location_by_code(self, location_code: str) -> Location | None:
        return self.session.scalar(
            select(Location).where(Location.location_code == location_code)
        )

    def list_merchants(
        self,
        *,
        page: int,
        page_size: int,
        preparation_status: str | None = None,
    ) -> list[Merchant]:
        statement = select(Merchant).options(joinedload(Merchant.pickup_location))
        if preparation_status is not None:
            statement = statement.where(
                Merchant.preparation_status == preparation_status
            )
        statement = statement.order_by(Merchant.merchant_code).offset(
            (page - 1) * page_size
        ).limit(page_size)
        return list(self.session.scalars(statement))

    def count_merchants(self, *, preparation_status: str | None = None) -> int:
        statement = select(func.count()).select_from(Merchant)
        if preparation_status is not None:
            statement = statement.where(
                Merchant.preparation_status == preparation_status
            )
        return self.session.scalar(statement) or 0

    def list_customers(self, *, page: int, page_size: int) -> list[Customer]:
        statement = (
            select(Customer)
            .options(joinedload(Customer.default_delivery_location))
            .order_by(Customer.customer_code)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement))

    def count_customers(self) -> int:
        return self.session.scalar(select(func.count()).select_from(Customer)) or 0

    def get_merchant_by_id(self, merchant_id: UUID) -> Merchant | None:
        statement = (
            select(Merchant)
            .where(Merchant.id == merchant_id)
            .options(joinedload(Merchant.pickup_location))
        )
        return self.session.scalar(statement)

    def lock_merchant_by_id(self, merchant_id: UUID) -> Merchant | None:
        statement = (
            select(Merchant)
            .where(Merchant.id == merchant_id)
            .options(joinedload(Merchant.pickup_location))
            .with_for_update(of=Merchant)
        )
        return self.session.scalar(statement)

    def get_customer_by_id(self, customer_id: UUID) -> Customer | None:
        statement = (
            select(Customer)
            .where(Customer.id == customer_id)
            .options(joinedload(Customer.default_delivery_location))
        )
        return self.session.scalar(statement)

    def lock_order_by_id(self, order_id: UUID) -> Order | None:
        statement = select(Order).where(Order.id == order_id).with_for_update()
        return self.session.scalar(statement)

    def lock_orders_by_ids(self, order_ids: list[UUID]) -> list[Order]:
        if not order_ids:
            return []
        statement = (
            select(Order)
            .where(Order.id.in_(order_ids))
            .order_by(Order.id)
            .with_for_update()
        )
        return list(self.session.scalars(statement))

    def add_location(self, location: Location) -> None:
        self.session.add(location)

    def add_merchant(self, merchant: Merchant) -> None:
        self.session.add(merchant)

    def add_customer(self, customer: Customer) -> None:
        self.session.add(customer)

    def add_order(self, order: Order) -> None:
        self.session.add(order)

    def flush(self) -> None:
        self.session.flush()
