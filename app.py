"""FastAPI / OGC API Processes entry point.

Start the API server:
    uvicorn app:app --host 0.0.0.0 --port 8000

Start a Celery worker (separate terminal):
    celery -A fastprocesses.worker.celery_app worker --loglevel=info
"""


import logging
import os
import time

import uvicorn
from fastprocesses.api.server import OGCProcessesAPI
from fastprocesses.processes.process_registry import get_process_registry
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
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

# Module-level logger for the app
logger = logging.getLogger("pluvial_flood_risk")


if _missing:
    raise RuntimeError(
        f"Expected OGC processes not registered at startup: {_missing}. "
        "Ensure all process modules are imported before OGCProcessesAPI is created."
    )


class RequestBodyTooLargeError(Exception):
    """Raised when an incoming request exceeds configured body size limit."""



class IngressLoggingMiddleware(BaseHTTPMiddleware):
    """Log basic ingress request/response information for troubleshooting."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint
    ) -> Response:
        started = time.perf_counter()
        content_length = request.headers.get("content-length", "-")
        logger.info(
            "Ingress start method=%s path=%s content_length=%s",
            request.method,
            request.url.path,
            content_length,
        )
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "Ingress error method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "Ingress end method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


# Starlette middleware to enforce request body size.



class IncreaseBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int) -> None:
        super().__init__(app)
        self.limit = limit

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self.limit:
                logger.warning(
                    "Ingress reject method=%s path=%s reason=content_length_over_limit "
                    "content_length=%s limit=%s",
                    request.method,
                    request.url.path,
                    content_length,
                    self.limit,
                )
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
            logger.warning(
                "Ingress reject method=%s path=%s reason=stream_over_limit limit=%s",
                request.method,
                request.url.path,
                self.limit,
            )
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

# Order matters: ingress logging should wrap request-size enforcement.
app.add_middleware(IngressLoggingMiddleware)
app.add_middleware(IncreaseBodySizeMiddleware, limit=_MAX_REQUEST_BODY_BYTES)

# Configure logging at startup if not already configured by Uvicorn/Gunicorn
@app.on_event("startup")
async def configure_logging():
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        log_level="info",
    )
