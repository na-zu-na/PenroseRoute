from datetime import date
from uuid import UUID

from sqlalchemy import select
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
