from __future__ import annotations

from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


DataT = TypeVar("DataT")
ItemT = TypeVar("ItemT")


class ApiResponse(BaseModel, Generic[DataT]):
    success: bool
    code: str
    message: str
    data: DataT | None
    request_id: str


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class PaginatedData(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def from_items(
        cls,
        *,
        items: list[ItemT],
        page: int,
        page_size: int,
        total: int,
    ) -> PaginatedData[ItemT]:
        return cls(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
        )
