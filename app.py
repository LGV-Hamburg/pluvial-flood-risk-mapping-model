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
from redis.exceptions import ResponseError as _RedisOOM

from pluvial_flood_risk.settings import settings as pfr_settings

# Module-level logger for the app
logger = logging.getLogger("pluvial-flood-risk")

# ---------------------------------------------------------------------------
# Process registration
#
# Each process module is imported so its @register_process decorator fires.
# If the broker Redis/Dragonfly is out of memory it raises ResponseError.
# We catch that per-process and log an error rather than crashing the entire
# application — the process will be absent from the registry but the API can
# still serve other requests (health checks, other processes).
# Any OTHER exception (import error, YAML parse failure, etc.) is re-raised
# immediately so programming mistakes stay loud.
# ---------------------------------------------------------------------------
_REQUIRED_PROCESSES = {
    "pluvial-flood-risk-regional": "pluvial_flood_risk.processes.flood_risk_process",
    "pluvial-flood-risk-citywide": "pluvial_flood_risk.processes.citywide_process",
}
_redis_oom_failures: set[str] = set()

for _process_id, _module_path in _REQUIRED_PROCESSES.items():
    try:
        __import__(_module_path)
    except _RedisOOM as exc:
        logger.error(
            "Process '%s' could not be registered — Redis broker out of memory: %s. "
            "Free broker memory and restart the service to restore this process.",
            _process_id, exc,
        )
        _redis_oom_failures.add(_process_id)

# Any process that is NEITHER registered NOR failed due to Redis OOM is a
# programming error (missing import, typo, etc.) — fail loudly.
_registered = set(get_process_registry().get_process_ids())
_unaccounted_missing = set(_REQUIRED_PROCESSES) - _registered - _redis_oom_failures
if _unaccounted_missing:
    raise RuntimeError(
        f"Expected OGC processes not registered at startup: {_unaccounted_missing}. "
        "Ensure all process modules are imported before OGCProcessesAPI is created."
    )


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
