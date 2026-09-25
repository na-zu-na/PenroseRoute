from typing import Any


class BusinessError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class NotFound(BusinessError):
    pass


class Conflict(BusinessError):
    pass


class IntegrationError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class AuthenticationError(Exception):
    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
