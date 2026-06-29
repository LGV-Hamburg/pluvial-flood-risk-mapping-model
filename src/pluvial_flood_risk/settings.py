"""Runtime settings for the pluvial flood risk pipeline.

All settings are read from environment variables with the ``PFR_`` prefix.
Instantiate once at import time via the module-level ``settings`` singleton.

Example
-------
Set via environment before starting the worker::

    export PFR_DROP_INVALID_INPUT_FEATURES=true
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class PfrSettings(BaseSettings):
    """Environment-driven settings for the pluvial flood risk pipeline."""

    model_config = SettingsConfigDict(env_prefix="PFR_", case_sensitive=False)

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


settings = PfrSettings()
