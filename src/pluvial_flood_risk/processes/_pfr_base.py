"""Shared input-resolution and validation mixin for PFR process variants.

Both :class:`~pluvial_flood_risk.processes.flood_risk_process.PluvialFloodRiskProcess`
(scatter-gather) and
:class:`~pluvial_flood_risk.processes.citywide_process.PluvialFloodRiskCitywide`
(data fan-out) resolve inputs and validate them in exactly the same way.
This module extracts that shared logic so neither process class duplicates it.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pyproj import CRS as ProjCRS
from referencing import Registry
from shapely.geometry import shape as shapely_shape

from ..io import fetch_references_cached
from ..models.inputs import (
    BuildingProperties,
    BuildingsFeatureCollection,
    FloodLayerProperties,
    FloodLayersFeatureCollection,
    StatisticalUnitProperties,
    StatisticalUnitsFeatureCollection,
    StreetProperties,
    StreetsFeatureCollection,
)
from ..settings import settings
from ..spatial_utils import (
    expand_boundary_by_meters,
    filter_features_to_intersecting_units,
)
from ..validation import drop_invalid_features, load_schema_file

logger = logging.getLogger(__name__)

_HAZARD_BUFFER_HALO_M = 30.0


def _build_pfr_schema_registry() -> Registry:
    """Return a pre-loaded JSON schema Registry shared by all PFR process variants."""
    return Registry().with_resources([
        (
            "https://geojson.org/schema/FeatureCollection.json",
            load_schema_file("schemas/geojson-featurecollection.json"),
        ),
        (
            "https://geojson.org/schema/Polygon.json",
            load_schema_file("schemas/geojson-Polygon.json"),
        ),
        (
            "https://geojson.org/schema/MultiPolygon.json",
            load_schema_file("schemas/geojson-MultiPolygon.json"),
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping"
            "/-/raw/main/schemas/buildings-schema.json",
            load_schema_file("schemas/buildings-schema.json"),
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping"
            "/-/raw/main/schemas/streets-schema.json",
            load_schema_file("schemas/streets-schema.json"),
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping"
            "/-/raw/main/schemas/statistical-units-schema.json",
            load_schema_file("schemas/statistical-units-schema.json"),
        ),
        (
            "https://gitlab.opencode.de/lgvhh/udp/urban-pluvial-flood-risk-mapping"
            "/-/raw/main/schemas/flood-layers-schema.json",
            load_schema_file("schemas/flood-layers-schema.json"),
        ),
    ])


class BasePfRProcess:
    """Mixin with shared input-resolution and validation logic for PFR processes.

    Inherit alongside a fastprocesses base class::

        class PluvialFloodRiskProcess(BasePfRProcess, BaseScatterProcess): ...
        class PluvialFloodRiskCitywide(BasePfRProcess, BaseParallelProcess): ...

    Python MRO ensures these methods are found before the framework base class,
    so fastprocesses will invoke the correct ``resolve_remote_inputs`` and
    ``late_validate`` hooks.
    """

    schema_registry = _build_pfr_schema_registry()

    _REFERENCE_INPUTS = ("buildings", "streets", "flood_layers")

    # ------------------------------------------------------------------
    # Remote input resolution
    # ------------------------------------------------------------------

    def resolve_remote_inputs(
        self,
        exec_body: dict,
        job_progress_callback=None,
    ) -> dict:
        """Download URL-valued inputs and replace them with resolved FeatureCollections.

        Detects ``{href, type}`` reference objects in ``buildings``, ``streets``,
        and ``flood_layers``.  Inline FeatureCollection dicts pass through unchanged.
        When a ``calculation_boundary`` is present, injects ``bbox`` / ``crs`` /
        ``bbox-crs`` OGC API parameters into the streets and flood_layers URLs before
        fetching so only the relevant region is downloaded in the target CRS.
        After fetching, derives ``buildings_local`` — the city buildings pre-filtered
        to the statistical units that intersect the boundary.
        """
        inputs = dict(exec_body["inputs"])
        references = [
            key for key in self._REFERENCE_INPUTS
            if isinstance(inputs.get(key), dict) and "href" in inputs[key]
        ]
        if not references:
            logger.debug("resolve_remote_inputs: all inputs are inline, skipping")
            return exec_body

        logger.info("resolve_remote_inputs: downloading remote inputs: %s", references)
        cb = inputs.get("calculation_boundary")
        if cb is not None:
            self._inject_bbox_params(inputs, references, cb)
            self._inject_crs_params(inputs, references, inputs.get("target_crs"))

        ref_map = {key: inputs[key] for key in references}
        resolved = fetch_references_cached(
            ref_map,
            allowed_origins=self._load_allowed_origins(),
            page_limit=settings.ogc_page_limit,
            max_concurrent_requests=settings.max_parallel_requests,
        )
        for key, data in resolved.items():
            logger.info("Resolved '%s': %d features", key, len(data.get("features", [])))
            inputs[key] = data

        if cb is not None:
            self._prefilter_buildings_by_boundary_units(inputs, cb)

        if job_progress_callback:
            job_progress_callback(0, "Downloaded remote inputs: %s" % ", ".join(references))
        return {**exec_body, "inputs": inputs}

    @staticmethod
    def _inject_bbox_params(inputs: dict, references: list[str], cb: dict) -> None:
        """Narrow streets/flood_layers fetch URLs to the boundary+halo bounding box.

        Buildings are intentionally excluded — the vulnerability step needs
        city-wide TOPSIS statistics.
        """
        halo = expand_boundary_by_meters(cb, _HAZARD_BUFFER_HALO_M)
        minx, miny, maxx, maxy = shapely_shape(halo).bounds
        bbox = f"{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}"
        for key in ("streets", "flood_layers"):
            if key not in references:
                continue
            ref = dict(inputs[key])
            parsed = urlparse(ref["href"])
            params = parse_qs(parsed.query, keep_blank_values=True)
            params["bbox"] = [bbox]
            ref["href"] = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
            inputs[key] = ref
            logger.info(
                "resolve_remote_inputs: bbox (±%.0fm halo) applied to '%s': %s",
                _HAZARD_BUFFER_HALO_M, key, bbox,
            )

    @staticmethod
    def _inject_crs_params(inputs: dict, references: list[str], target_crs: object) -> None:
        """Add ``crs=`` and ``bbox-crs=`` OGC API parameters to streets/flood_layers URLs.

        Requests data in the target projected CRS so features arrive pre-projected.
        ``bbox-crs`` declares that the existing bbox parameter is in WGS-84 (CRS84),
        not in the response CRS.  Skipped for flatgeobuf references (embed own CRS)
        and when ``target_crs`` is absent or unresolvable.
        """
        if not isinstance(target_crs, str) or not target_crs:
            return
        try:
            auth, code = ProjCRS.from_user_input(target_crs).to_authority()
            crs_uri = f"http://www.opengis.net/def/crs/{auth}/0/{code}"
        except Exception:
            logger.warning(
                "resolve_remote_inputs: could not derive OGC URI for "
                "target_crs=%r — crs parameter injection skipped",
                target_crs,
            )
            return
        wgs84_uri = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
        for key in ("streets", "flood_layers"):
            if key not in references:
                continue
            ref = dict(inputs[key])
            if ref.get("type") != "application/geo+json":
                continue
            parsed = urlparse(ref["href"])
            params = parse_qs(parsed.query, keep_blank_values=True)
            params["crs"] = [crs_uri]
            params["bbox-crs"] = [wgs84_uri]
            ref["href"] = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
            inputs[key] = ref
            logger.debug(
                "resolve_remote_inputs: crs=%r bbox-crs=%r injected into '%s'",
                crs_uri, wgs84_uri, key,
            )

    @staticmethod
    def _prefilter_buildings_by_boundary_units(inputs: dict, cb: dict) -> None:
        """Store a boundary-area subset of city buildings in ``inputs['buildings_local']``.

        Keeps every building that falls inside a statistical unit which intersects
        the calculation boundary.  Retaining whole units (rather than clipping to
        the boundary polygon itself) ensures that unit-level aggregations such as
        the living-area sum and TOPSIS normalisation remain correct for units that
        straddle the boundary edge.  Falls back to the 30 m halo region when no
        statistical units are provided.
        """
        buildings = inputs.get("buildings")
        units = inputs.get("statistical_units")
        if (
            not (isinstance(buildings, dict) and buildings.get("features"))
            or not (isinstance(units, dict) and units.get("features"))
        ):
            return
        local_features = filter_features_to_intersecting_units(
            buildings["features"],
            units["features"],
            cb,
            expand_boundary_by_meters(cb, _HAZARD_BUFFER_HALO_M),
        )
        inputs["buildings_local"] = {"type": "FeatureCollection", "features": local_features}
        logger.info(
            "resolve_remote_inputs: buildings_local = %d / %d features",
            len(local_features), len(buildings["features"]),
        )

    @staticmethod
    def _load_allowed_origins() -> list[str]:
        """Read FP_ALLOWED_INPUT_ORIGINS from the environment.

        Raises
        ------
        ValueError
            If the variable is missing or empty.
        """
        raw = os.environ.get("FP_ALLOWED_INPUT_ORIGINS", "").strip()
        if not raw:
            raise ValueError(
                "FP_ALLOWED_INPUT_ORIGINS is not set. "
                "Configure an allowlist of trusted origin prefixes "
                "(JSON array or comma-separated) before accepting URL inputs."
            )
        try:
            origins = json.loads(raw)
            if not isinstance(origins, list):
                raise ValueError("FP_ALLOWED_INPUT_ORIGINS must be a JSON array.")
            return origins
        except json.JSONDecodeError:
            return [o.strip() for o in raw.split(",") if o.strip()]

    # ------------------------------------------------------------------
    # Validation hook
    # ------------------------------------------------------------------

    def late_validate(self, inputs: dict) -> bool:
        """Validate resolved FeatureCollections against typed geojson-pydantic models.

        Called by fastprocesses after ``resolve_remote_inputs`` substitutes any
        URL-valued inputs with downloaded data.  At this point every spatial input
        is a plain GeoJSON dict and can be parsed against its typed alias.

        Raises ``ValueError`` (forwarded to the job as a user-facing error) if any
        FeatureCollection is structurally invalid or a feature is missing required
        properties (e.g. ``depth_cm`` on flood layers, ``residents`` on statistical
        units).  Extra properties are silently ignored (``extra='ignore'``).
        """
        logger.info("late_validate: validating resolved FeatureCollections")
        for name in ("buildings", "streets", "flood_layers", "statistical_units"):
            fc = inputs.get(name)
            if fc is None:
                continue
            geom_types = Counter(
                (f.get("geometry") or {}).get("type", "null")
                for f in fc.get("features", [])
            )
            if geom_types.keys() - {"Polygon", "MultiPolygon"}:
                logger.warning(
                    "late_validate: '%s' contains unexpected geometry types: %s",
                    name, dict(geom_types),
                )
            else:
                logger.debug(
                    "late_validate: '%s' geometry types: %s", name, dict(geom_types)
                )
        _LAYER_VALIDATORS = [
            ("buildings",         BuildingProperties,       BuildingsFeatureCollection),
            ("streets",           StreetProperties,         StreetsFeatureCollection),
            ("flood_layers",      FloodLayerProperties,     FloodLayersFeatureCollection),
            ("statistical_units", StatisticalUnitProperties, StatisticalUnitsFeatureCollection),
        ]
        for layer_name, props_model, fc_model in _LAYER_VALIDATORS:
            fc = inputs.get(layer_name)
            if fc is None:
                continue
            if settings.drop_invalid_input_features:
                inputs[layer_name] = drop_invalid_features(fc, props_model, layer_name)
            else:
                fc_model.model_validate(fc)
                logger.debug(
                    "late_validate: '%s' OK (%d features)",
                    layer_name, len(fc.get("features", [])),
                )
        logger.info("late_validate: all inputs valid")
        return True
