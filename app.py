"""FastAPI / OGC API Processes entry point.

Start the API server:
    uvicorn app:app --host 0.0.0.0 --port 8000

Start a Celery worker (separate terminal):
    celery -A fastprocesses.worker.celery_app worker --loglevel=info
"""

from __future__ import annotations

import os

import uvicorn
from fastprocesses.api.server import OGCProcessesAPI
from fastprocesses.common import settings
from fastprocesses.processes.process_registry import get_process_registry
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Import the process module so that @register_process fires and
# PluvialFloodRiskProcess appears in the OGC process registry.
import pluvial_flood_risk.processes.flood_risk_process  # noqa: F401

# Fail loudly at startup if the expected process was not registered.
# This catches accidental removal of the import above before any request is served.
_REQUIRED_PROCESSES = {"pluvial_flood_risk"}
_registered = set(get_process_registry().get_process_ids())
_missing = _REQUIRED_PROCESSES - _registered

# Max request body size accepted by this app (default: 200 MiB).
_MAX_REQUEST_BODY_BYTES = int(os.getenv("PFR_MAX_REQUEST_BODY_BYTES", "209715200"))

if _missing:
    raise RuntimeError(
        f"Expected OGC processes not registered at startup: {_missing}. "
        "Ensure all process modules are imported before OGCProcessesAPI is created."
    )


class RequestBodyTooLargeError(Exception):
    """Raised when an incoming request exceeds configured body size limit."""


# Starlette middleware to enforce request body size.
class IncreaseBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int) -> None:
        super().__init__(app)
        self.limit = limit

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self.limit:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            "Request body too large. "
                            f"Max allowed is {self.limit} bytes."
                        )
                    },
                )

            request._receive = self.receive_with_limit(
                request._receive,
                limit=self.limit,
            )

        try:
            return await call_next(request)
        except RequestBodyTooLargeError:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        f"Request body too large. Max allowed is {self.limit} bytes."
                    )
                },
            )

    @staticmethod
    def receive_with_limit(original_receive, limit):
        bytes_seen = 0

        async def receive():
            nonlocal bytes_seen
            message = await original_receive()
            if message.get("type") == "http.request":
                bytes_seen += len(message.get("body") or b"")
                if bytes_seen > limit:
                    raise RequestBodyTooLargeError()
            return message

        return receive

app = OGCProcessesAPI(
    contact={
        "name": "Agency for Geoinformation and Surveying Hamburg",
        "url": (
            "https://www.hamburg.de/politik-und-verwaltung/behoerden/"
            "behoerde-fuer-stadtentwicklung-und-wohnen/aemter-und-landesbetrieb/"
            "landesbetrieb-geoinformation-und-vermessung"
        ),
        "email": "info@gv.hamburg.de",
    },
    license={
        "name": "GNU General Public License v3.0",
        "url": "https://github.com/LGV-Hamburg/noiseprocesses/blob/main/LICENSE",
    },
    terms_of_service="",
).get_app()

app.add_middleware(IncreaseBodySizeMiddleware, limit=_MAX_REQUEST_BODY_BYTES)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        log_level=settings.FP_LOG_LEVEL,
    )
