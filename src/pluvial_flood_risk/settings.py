"""Runtime settings for the pluvial flood risk pipeline.

All settings are read from environment variables with the ``PFR_`` prefix.
Instantiate once at import time via the module-level ``settings`` singleton.

Example
-------
Set via environment before starting the worker::

    export PFR_DROP_INCOMPLETE_STATISTICAL_UNITS=true
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class PfrSettings(BaseSettings):
    """Environment-driven settings for the pluvial flood risk pipeline."""

    model_config = SettingsConfigDict(env_prefix="PFR_", case_sensitive=False)

    drop_incomplete_statistical_units: bool = False
    """When *True*, statistical unit features that fail Pydantic validation
    (e.g. missing required fields) are silently dropped and logged as warnings
    rather than raising a ``ValueError`` that would fail the job.

    Default ``False`` — validation errors are fatal (safe default for
    production data where missing residents would corrupt risk scores).

    Set ``PFR_DROP_INCOMPLETE_STATISTICAL_UNITS=true`` to enable lenient mode.
    """


settings = PfrSettings()
