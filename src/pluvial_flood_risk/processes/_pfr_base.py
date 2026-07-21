"""Shared input-resolution and validation mixin for PFR process variants.

Both :class:`~pluvial_flood_risk.processes.flood_risk_process.PluvialFloodRiskProcess`
(scatter-gather) and
:class:`~pluvial_flood_risk.processes.citywide_process.PluvialFloodRiskCitywide`
(data fan-out) resolve inputs and validate them in exactly the same way.
This module extracts that shared logic so neither process class duplicates it.
"""

from __future__ import annotations

import io
import json
import logging
from collections import Counter
from typing import ClassVar
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import geopandas as gpd
from fastprocesses import BaseProcessResult
from pyproj import CRS as ProjCRS
from referencing import Registry
from shapely.geometry import shape as shapely_shape

from ..io import fetch_references_cached
from ..models.field_names import (
    BUILDING_LIVING_AREA_M2,
    BUILDING_TYPE,
    CHILDREN_UNDER_10,
    COPING_CAPACITY_INDEX,
    ELDERLY_SINGLES_OVER_65,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    FLOORS_ABOVE_GROUND,
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    LIVING_AREA_M2_STATISTICAL_UNIT,
    NO_HIGHSCHOOL_DIPLOMA,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    RESIDENTS_GROUND_FLOOR,
    RESIDENTS_IN_STATISTICAL_UNIT,
    RESIDENTS_PER_BUILDING,
    RISK_CLASS_MOBILITY_ACCESSIBILITY,
    RISK_CLASS_WELL_BEING,
    SENSITIVITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    SOCIAL_VULNERABILITY_INDEX,
    SOCIAL_WELFARE_RECIPIENTS,
    STATISTICAL_UNIT_ID,
)
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
        allowed_origins = settings.allowed_input_origins
        if not allowed_origins:
            raise ValueError(
                "FP_ALLOWED_INPUT_ORIGINS is not set. "
                "Configure an allowlist of trusted origin prefixes "
                "(JSON array or comma-separated) before accepting URL inputs."
            )
        resolved = fetch_references_cached(
            ref_map,
            allowed_origins=allowed_origins,
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

_INPUT_BUILDING_OUTPUT_COLUMNS: list[str] = [
    FLOORS_ABOVE_GROUND,
    BUILDING_TYPE,
    CHILDREN_UNDER_10,
    ELDERLY_SINGLES_OVER_65,
    SOCIAL_WELFARE_RECIPIENTS,
    NO_HIGHSCHOOL_DIPLOMA,
    STATISTICAL_UNIT_ID,
    RESIDENTS_IN_STATISTICAL_UNIT,
    LIVING_AREA_M2_STATISTICAL_UNIT,
]

# All computed output columns produced by the full pipeline.
# Matches the enriched-feature properties written by both process variants.
_OUTPUT_COLUMNS: list[str] = [
    SENSITIVITY_INDEX,
    COPING_CAPACITY_INDEX,
    SOCIAL_VULNERABILITY_INDEX,
    SOCIAL_VULNERABILITY_FLOOD_POTENTIAL,
    BUILDING_LIVING_AREA_M2,
    RESIDENTS_PER_BUILDING,
    RESIDENTS_GROUND_FLOOR,
    EXPOSURE_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    HAZARD_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    RISK_CLASS_WELL_BEING,
    RISK_CLASS_MOBILITY_ACCESSIBILITY,
]


# ---------------------------------------------------------------------------
# Shared output model
# ---------------------------------------------------------------------------


class PfRiskResult(BaseProcessResult):
    """Combined output of the pluvial flood risk pipeline.

    Fields mirror the process outputs declared in the process description YAML.
    Shared by both :class:`PluvialFloodRiskProcess` (scatter-gather) and
    :class:`PluvialFloodRiskCitywide` (data fan-out).
    """

    output_serializers: ClassVar = {
        "risk_buildings": {
            "application/geo+json": "risk_buildings_to_json",
            "application/flatgeobuf": "risk_buildings_to_fgb",
        },
        "voronoi_diagram": {
            "application/geo+json": "voronoi_diagram_to_json",
            "application/flatgeobuf": "voronoi_diagram_to_fgb",
        },
        "classification_breaks_wb": {
            "application/json": "classification_breaks_wb_to_json",
        },
        "classification_breaks_ma": {
            "application/json": "classification_breaks_ma_to_json",
        },
    }

    @staticmethod
    def _fc_to_fgb(fc: dict) -> bytes:
        """Serialise a GeoJSON FeatureCollection dict to FlatGeobuf bytes."""
        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326")
        buf = io.BytesIO()
        gdf.to_file(buf, driver="FlatGeobuf")
        return buf.getvalue()

    def risk_buildings_to_json(self) -> bytes:
        result = self.model_dump(
            mode="json", include={"risk_buildings"}
        )["risk_buildings"]
        return json.dumps(result).encode()

    def risk_buildings_to_fgb(self) -> bytes:
        fc = self.model_dump(mode="json", include={"risk_buildings"})["risk_buildings"]
        return self._fc_to_fgb(fc)

    def voronoi_diagram_to_json(self) -> bytes:
        result = self.model_dump(
            mode="json", include={"voronoi_diagram"}
        )["voronoi_diagram"]
        return json.dumps(result).encode()

    def voronoi_diagram_to_fgb(self) -> bytes:
        fc = self.model_dump(
            mode="json", include={"voronoi_diagram"}
        )["voronoi_diagram"]
        return self._fc_to_fgb(fc)

    def classification_breaks_wb_to_json(self) -> bytes:
        result = self.model_dump(
            mode="json", include={"classification_breaks_wb"}
        )["classification_breaks_wb"]
        return json.dumps(result).encode()

    def classification_breaks_ma_to_json(self) -> bytes:
        result = self.model_dump(
            mode="json", include={"classification_breaks_ma"}
        )["classification_breaks_ma"]
        return json.dumps(result).encode()

    risk_buildings: dict | None = None
    voronoi_diagram: dict | None = None
    classification_breaks_wb: list[float] | None = None
    classification_breaks_ma: list[float] | None = None
    validation_report: dict | None = None
