from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Customer, Location, Merchant, Order


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

    def get_customer_by_id(self, customer_id: UUID) -> Customer | None:
        statement = (
            select(Customer)
            .where(Customer.id == customer_id)
            .options(joinedload(Customer.default_delivery_location))
        )
        return self.session.scalar(statement)

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
