import pytest
from pydantic import ValidationError

from app.schemas.common import PaginatedData, PaginationParams


def test_pagination_defaults_and_page_count() -> None:
    params = PaginationParams()
    result = PaginatedData.from_items(
        items=["first", "second"],
        page=params.page,
        page_size=params.page_size,
        total=21,
    )

    assert params.page == 1
    assert params.page_size == 20
    assert result.total_pages == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [("page", 0), ("page_size", 0), ("page_size", 101)],
)
def test_pagination_rejects_values_outside_contract(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        PaginationParams(**{field: value})
