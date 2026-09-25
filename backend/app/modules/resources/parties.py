from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import Customer, Merchant
from app.db.models.resources import MerchantPreparationStatus
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.resources.locations import resolve_location


class PartyService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ResourceRepository(session)

    def list_merchants(
        self,
        *,
        page: int,
        page_size: int,
        preparation_status: str | None,
    ) -> tuple[list[Merchant], int]:
        return (
            self.repository.list_merchants(
                page=page,
                page_size=page_size,
                preparation_status=preparation_status,
            ),
            self.repository.count_merchants(
                preparation_status=preparation_status
            ),
        )

    def get_merchant(self, merchant_id: UUID) -> Merchant:
        merchant = self.repository.get_merchant_by_id(merchant_id)
        if merchant is None:
            raise NotFound(
                code="MERCHANT_NOT_FOUND",
                message="Merchant was not found",
            )
        return merchant

    def create_merchant(
        self,
        *,
        merchant_code: str,
        name: str,
        location_code: str,
        address_text: str | None,
        latitude: Decimal,
        longitude: Decimal,
        operational_ready_at: datetime | None,
        default_pickup_service_seconds: int,
    ) -> Merchant:
        try:
            location = resolve_location(
                self.repository,
                location_code=location_code,
                display_name=name,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            merchant = Merchant(
                merchant_code=merchant_code,
                name=name,
                pickup_location=location,
                preparation_status=MerchantPreparationStatus.PREPARING,
                operational_ready_at=operational_ready_at,
                default_pickup_service_seconds=default_pickup_service_seconds,
            )
            self.repository.add_merchant(merchant)
            self.repository.flush()
            self.session.commit()
            return merchant
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Merchant code or location code already exists",
            ) from error

    def replace_merchant(
        self,
        merchant_id: UUID,
        *,
        name: str,
        location_code: str,
        address_text: str | None,
        latitude: Decimal,
        longitude: Decimal,
        default_pickup_service_seconds: int,
    ) -> Merchant:
        merchant = self.get_merchant(merchant_id)
        try:
            merchant.name = name
            merchant.pickup_location = resolve_location(
                self.repository,
                location_code=location_code,
                display_name=name,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            merchant.default_pickup_service_seconds = (
                default_pickup_service_seconds
            )
            merchant.updated_at = datetime.now(timezone.utc)
            self.repository.flush()
            self.session.commit()
            return merchant
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Merchant location conflicts with an existing location",
            ) from error

    def update_merchant_status(
        self,
        merchant_id: UUID,
        *,
        status: str,
    ) -> Merchant:
        merchant = self.get_merchant(merchant_id)
        target = MerchantPreparationStatus(status)
        if merchant.preparation_status == target:
            return merchant
        allowed = {
            (
                MerchantPreparationStatus.PREPARING,
                MerchantPreparationStatus.READY,
            ),
            (
                MerchantPreparationStatus.DELAYED,
                MerchantPreparationStatus.READY,
            ),
        }
        if (merchant.preparation_status, target) not in allowed:
            raise Conflict(
                code="INVALID_MERCHANT_STATUS_TRANSITION",
                message="Merchant preparation status transition is not allowed",
            )
        merchant.preparation_status = target
        merchant.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return merchant

    def list_customers(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Customer], int]:
        return (
            self.repository.list_customers(page=page, page_size=page_size),
            self.repository.count_customers(),
        )

    def get_customer(self, customer_id: UUID) -> Customer:
        customer = self.repository.get_customer_by_id(customer_id)
        if customer is None:
            raise NotFound(
                code="CUSTOMER_NOT_FOUND",
                message="Customer was not found",
            )
        return customer

    def create_customer(
        self,
        *,
        customer_code: str,
        name: str,
        location_code: str,
        address_text: str | None,
        latitude: Decimal,
        longitude: Decimal,
    ) -> Customer:
        try:
            location = resolve_location(
                self.repository,
                location_code=location_code,
                display_name=name,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            customer = Customer(
                customer_code=customer_code,
                name=name,
                default_delivery_location=location,
            )
            self.repository.add_customer(customer)
            self.repository.flush()
            self.session.commit()
            return customer
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Customer code or location code already exists",
            ) from error

    def replace_customer(
        self,
        customer_id: UUID,
        *,
        name: str,
        location_code: str,
        address_text: str | None,
        latitude: Decimal,
        longitude: Decimal,
    ) -> Customer:
        customer = self.get_customer(customer_id)
        try:
            customer.name = name
            customer.default_delivery_location = resolve_location(
                self.repository,
                location_code=location_code,
                display_name=name,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            customer.updated_at = datetime.now(timezone.utc)
            self.repository.flush()
            self.session.commit()
            return customer
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Customer location conflicts with an existing location",
            ) from error
