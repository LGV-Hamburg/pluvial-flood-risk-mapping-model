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

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PfrSettings(BaseSettings):
    """Environment-driven settings for the pluvial flood risk pipeline."""

    model_config = SettingsConfigDict(env_prefix="PFR_", case_sensitive=False)

    process_description_pfr_file: Path | None = None
    """Path to a JSON file containing the process description for the
    PluvialFloodRiskProcess. If set, the file path is given
    """

    process_description_pfr_citywide_file: Path | None = None

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

    allowed_input_origins: list[str] = Field(
        default=[]
    )
    """Allowlist of trusted URL origins for user-supplied ``{href, type}``
    reference inputs in ``PluvialFloodRiskProcess``.

    Accepted formats: JSON array (``'["https://example.com"]'``) or
    comma-separated string (``'https://a.com,https://b.com'``).

    Must be set before accepting any URL-valued inputs; absence causes
    ``resolve_remote_inputs`` to raise a ``ValueError``.

    Set ``FP_ALLOWED_INPUT_ORIGINS='["https://trusted.example.com/"]'``.
    """

    @field_validator("allowed_input_origins", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, v: object) -> list[str]:
        if not v:
            return []
        if isinstance(v, list):
            return [str(o) for o in v]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            try:
                parsed = json.loads(v)
                if not isinstance(parsed, list):
                    raise ValueError(
                        "FP_ALLOWED_INPUT_ORIGINS must be a JSON array."
                    )
                return [str(o) for o in parsed]
            except json.JSONDecodeError:
                return [o.strip() for o in v.split(",") if o.strip()]
        raise ValueError(f"Cannot parse FP_ALLOWED_INPUT_ORIGINS: {v!r}")


settings = PfrSettings()
