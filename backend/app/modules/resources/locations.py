from decimal import Decimal

from app.core.errors import Conflict
from app.db.models import Location
from app.db.repositories.resource_repository import ResourceRepository


def resolve_location(
    repository: ResourceRepository,
    *,
    location_code: str,
    display_name: str,
    address_text: str | None,
    latitude: Decimal,
    longitude: Decimal,
) -> Location:
    existing = repository.get_location_by_code(location_code)
    if existing is not None:
        if existing.latitude != latitude or existing.longitude != longitude:
            raise Conflict(
                code="LOCATION_CODE_CONFLICT",
                message="Location code already refers to different coordinates",
            )
        return existing

    location = Location(
        location_code=location_code,
        display_name=display_name,
        address_text=address_text,
        latitude=latitude,
        longitude=longitude,
    )
    repository.add_location(location)
    repository.flush()
    return location
