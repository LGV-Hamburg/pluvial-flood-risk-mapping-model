"""FastAPI / OGC API Processes entry point.

Start the API server:
    uvicorn app:app --host 0.0.0.0 --port 8000

Start a Celery worker (separate terminal):
    celery -A fastprocesses.worker.celery_app worker --loglevel=info
"""

import logging

import uvicorn
from fastprocesses.api.server import OGCProcessesAPI
from fastprocesses.common import settings
from fastprocesses.processes.process_registry import get_process_registry

import pluvial_flood_risk.processes.citywide_process  # noqa: F401

# Import the process module so that @register_process fires and
# PluvialFloodRiskProcess appears in the OGC process registry.
import pluvial_flood_risk.processes.flood_risk_process  # noqa: F401
from pluvial_flood_risk.settings import settings as pfr_settings

# Fail loudly at startup if the expected process was not registered.
# This catches accidental removal of the import above before any request is served.
_REQUIRED_PROCESSES = {"pluvial-flood-risk-regional", "pluvial-flood-risk-citywide"}
_registered = set(get_process_registry().get_process_ids())
_missing = _REQUIRED_PROCESSES - _registered

# Module-level logger for the app
logger = logging.getLogger("pluvial-flood-risk")


class _FixedUrlReferencePublisher:
    """Stub output-reference publisher that always returns a fixed href.

    Discards the result payload and returns ``PFR_REFERENCE_HREF`` verbatim.
    Intended as a development stand-in until a proper file-serving backend
    (object storage, static-files route) is available.
    """

    def __init__(self, href: str) -> None:
        self._href = href

    def publish(self, payload: bytes, *, output_id: str, media_type: str) -> str:  # noqa: ARG002
        return self._href

if _missing:
    raise RuntimeError(
        f"Expected OGC processes not registered at startup: {_missing}. "
        "Ensure all process modules are imported before OGCProcessesAPI is created."
    )

api = OGCProcessesAPI(
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
)

if pfr_settings.reference_href:
    api.process_manager.output_reference_publisher = _FixedUrlReferencePublisher(
        pfr_settings.reference_href
    )
    logger.info(
        "Output-reference publisher configured (stub): href=%s",
        pfr_settings.reference_href,
    )

app = api.get_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        log_level=settings.FP_LOG_LEVEL,
    )
