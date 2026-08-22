"""Unified error envelope for API consumers.

All errors return a consistent shape:

    {
      "error": {
        "code": "invalid_api_key",
        "message": "Invalid API key",
        "status": 401,
        "details": null,
        "request_id": "abc-123",
        "timestamp": "2026-08-22T10:00:00Z"
      }
    }

This makes it easy for external systems to handle errors programmatically.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Map HTTP status -> stable machine-readable code.
_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}


def _request_id() -> str:
    return str(uuid.uuid4())[:8]


def _envelope(
    status_code: int,
    message: str,
    details: Any = None,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    code = _STATUS_CODES.get(status_code, "error")
    body = {
        "error": {
            "code": code,
            "message": message,
            "status": status_code,
            "details": details,
            "request_id": request_id or _request_id(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    """Register FastAPI exception handlers that emit the unified envelope."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Preserve useful headers (e.g. Retry-After from the rate limiter).
        headers = dict(exc.headers or {})
        return _envelope(
            status_code=exc.status_code,
            message=str(exc.detail) if isinstance(exc.detail, str) else "Request failed",
            details=exc.detail if not isinstance(exc.detail, str) else None,
            request_id=request.headers.get("X-Request-ID"),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for err in exc.errors():
            errors.append(
                {
                    "loc": ".".join(str(p) for p in err.get("loc", [])),
                    "msg": err.get("msg", ""),
                }
            )
        return _envelope(
            status_code=422,
            message="Request validation failed",
            details=errors,
            request_id=request.headers.get("X-Request-ID"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _envelope(
            status_code=500,
            message="Internal server error",
            request_id=request.headers.get("X-Request-ID"),
        )
