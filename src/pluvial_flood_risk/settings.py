"""Runtime settings for the pluvial flood risk pipeline.

All settings are read from environment variables with the ``PFR_`` prefix.
Instantiate once at import time via the module-level ``settings`` singleton.

Example
-------
Set via environment before starting the worker::

    export PFR_DROP_INVALID_INPUT_FEATURES=true
"""

import json
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PfrSettings(BaseSettings):
    """Environment-driven settings for the pluvial flood risk pipeline."""

    model_config = SettingsConfigDict(env_prefix="PFR_", case_sensitive=False)

    process_description_pfr_file: Path | None = None
    """Path to a JSON file containing the process description for the
    PluvialFloodRiskProcess. If set, the file path is given
    """

    process_description_pfr_citywide_file: Path | None = None

    citywide_chunk_size: int = 1
    """Number of statistical units batched into a single Celery worker task for
    the citywide process.

    Increase to reduce worker count when individual units are small (e.g. set
    to ``5`` to process 5 units per worker).  The default of ``1`` preserves
    the original one-worker-per-unit behaviour.

    Set ``PFR_CITYWIDE_CHUNK_SIZE=<n>`` to override.
    """

    max_parallel_requests: int = 10
    """Maximum number of concurrent HTTP requests allowed when downloading
    remote inputs (semaphore passed to ``fetch_all_references``).

    Lower values reduce load on upstream OGC API servers at the cost of
    slower downloads for large datasets.  Default ``10``.

    Set ``PFR_MAX_PARALLEL_REQUESTS=<n>`` to override.
    """

    ogc_page_limit: int = 10_000
    """Number of features requested per OGC API Features page.

    Larger values reduce the number of parallel page requests and therefore
    server load and round-trip overhead.  Most OGC API servers cap this at
    10 000.  Default ``10000``.

    Set ``PFR_OGC_PAGE_LIMIT=<n>`` to override.
    """

    drop_invalid_input_features: bool = False
    """When *True*, features that fail validation (missing required properties
    or missing ``"geometry"`` key) are silently dropped and logged as warnings
    rather than raising a ``ValueError`` that would fail the job.  Applies to
    all input layers: buildings, streets, flood_layers, and statistical_units.

    Default ``False`` — validation errors are fatal (safe default for
    production data where corrupt features would skew risk scores).

    Set ``PFR_DROP_INVALID_INPUT_FEATURES=true`` to enable lenient mode.
    """

    dev_cache_dir: str | None = None
    """Writable directory for the joblib fetch cache used in development.

    When set, downloaded OGC API responses are cached to disk and reused on
    subsequent runs.  The cache key includes all request parameters, so
    changing any URL busts it automatically.

    **Never set in production** — stale cached data will be served instead of
    live data.

    Set ``PFR_DEV_CACHE_DIR=data/intermediate`` to enable.
    """

    reference_href: str | None = None
    """Fixed href returned for all ``transmissionMode: reference`` outputs.

    When set, the API advertises support for output-by-reference and returns
    this URL as the ``href`` in the qualified-value response.  The actual
    result bytes are **discarded** — this is a stub for development and testing
    until a proper file-serving backend (object storage, static-files route)
    is wired up.

    Set ``PFR_REFERENCE_HREF=https​://example.com/results`` to enable.
    """

    pfr_allowed_input_origins: str = Field(
        default="",
        alias="PFR_ALLOWED_INPUT_ORIGINS",
    )

    @computed_field
    @property
    def allowed_input_origins(self) -> list[str]:
        """Allowlist of trusted URL origins for user-supplied ``{href, type}``
        reference inputs in ``PluvialFloodRiskProcess``.

        Accepted formats: JSON array (``'["https://example.com"]'``) or
        comma-separated string (``'https://a.com,https://b.com'``).

        Must be non-empty before accepting URL-valued inputs; absence causes
        ``resolve_remote_inputs`` to raise a ``ValueError``.

        Set ``PFR_ALLOWED_INPUT_ORIGINS='["https://trusted.example.com/"]'``.
        """
        v = self.pfr_allowed_input_origins.strip()
        if not v:
            return []
        try:
            parsed = json.loads(v)
            if not isinstance(parsed, list):
                raise ValueError(
                    "PFR_ALLOWED_INPUT_ORIGINS "
                    "must be a JSON array or comma-separated string."
                )
            return [str(o) for o in parsed]
        except json.JSONDecodeError:
            return [o.strip() for o in v.split(",") if o.strip()]


settings = PfrSettings()
