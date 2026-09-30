"""OGC API Process: Pluvial Flood Risk Calculation — city-wide data fan-out variant.

Implements the same IPCC risk framework as
:class:`~pluvial_flood_risk.processes.flood_risk_process.PluvialFloodRiskProcess`
but uses a **data fan-out** execution model instead of an operation fan-out:

    split_inputs        — city-wide vulnerability (GLOBAL), partition by statistical unit
    execute_single xN   — exposure + hazard + risk on one statistical unit
    merge_results       — GLOBAL head/tail classification, build output

This trades the 4-way operation parallelism of the scatter-gather variant for
**N-way data parallelism across statistical units**, which scales better at city
size.  The two variants are complementary:

- ``pluvial_flood_risk`` — better for a focused sub-area (small N, all 4 branches run)
- ``pluvial_flood_risk_citywide`` — better for the entire city (large N, each worker
  processes one unit sequentially but many units run concurrently)

Inputs and outputs are identical to ``PluvialFloodRiskProcess``; the process
description YAML differs only in ``id``, ``title``, and ``description``.
"""

from __future__ import annotations

import json
import logging
import time

import geopandas as gpd
import pandas as pd
from fastprocesses.core.base_process import BaseParallelProcess
from fastprocesses.core.models import ProcessDescription
from fastprocesses.core.types import JobProgressCallback
from fastprocesses.processes.process_registry import register_process
from pydantic import BaseModel

from pluvial_flood_risk.settings import settings

from ..algorithms.exposure import run_exposure_pipeline
from ..algorithms.hazard_ma import run_hazard_ma_pipeline
from ..algorithms.hazard_wb import run_hazard_wb_pipeline
from ..algorithms.risk import run_risk_pipeline
from ..algorithms.vulnerability import run_vulnerability_pipeline
from ..models.field_names import (
    BUILDING_FOOTPRINT_AREA_M2,
    BUILDING_TYPE,
    FLOORS_ABOVE_GROUND,
    HAZARD_MOBILITY_ACCESSIBILITY,
    HAZARD_WELL_BEING,
    LIVING_AREA_M2_STATISTICAL_UNIT,
    PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY,
    PLUVIAL_FLOOD_RISK_WELL_BEING,
    RESIDENTS_IN_STATISTICAL_UNIT,
    STATISTICAL_UNIT_ID,
)
from ..models.inputs import HazardParams, ProcessInputs, RiskWeights
from ..spatial_utils import (
    convert_geojson_to_gdf,
    filter_gdf_to_boundary,
    join_statistical_unit_attributes,
    project_crs_to_metric,
)
from ._pfr_base import (
    _HAZARD_BUFFER_HALO_M,
    _OUTPUT_COLUMNS,
    BasePfRProcess,
    PfRiskResult,
    apply_optional_smoothing,
    finalize_risk_outputs,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Partial-result model  (one per statistical unit, Celery-serialised)
# ---------------------------------------------------------------------------
class _ChunkResult(BaseModel):
    """Partial risk results for one statistical unit, returned by execute_single.

    ``features`` are the building GeoJSON features (WGS-84) with all computed
    columns attached as properties, but WITHOUT risk-class labels (those require
    city-wide classification in merge_results).

    ``risk_wb`` and ``risk_ma`` mirror the per-building PFR values so that
    merge_results can run head/tail classification without re-parsing the features.
    """

    features: list[dict]
    risk_wb: list[float | None]
    risk_ma: list[float | None]


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------


def _batch_unit_items(items: list[dict], chunk_size: int) -> list[dict]:
    """Group per-unit work items into batches of *chunk_size* units each.

    Buildings are simply concatenated (they are disjoint by construction).
    Streets and flood features shared between adjacent unit halos are
    deduplicated by GeoJSON ``id``; features without an ``id`` are always
    included.

    Args:
        items: Per-unit work items produced by the partitioning loop in
            ``split_inputs``. Each item must contain ``"buildings"``,
            ``"streets"``, and ``"flood_layers"`` GeoJSON FeatureCollections
            and a ``"unit_id"`` string.
        chunk_size: Maximum number of statistical units per batch.

    Returns:
        Batched work items.  Returns *items* unchanged when
        ``chunk_size <= 1`` or ``len(items) <= chunk_size``.
    """
    if chunk_size <= 1 or len(items) <= chunk_size:
        return items

    shared_params = {
        k: v for k, v in items[0].items()
        if k not in ("unit_id", "buildings", "streets", "flood_layers")
    }

    batched: list[dict] = []
    for i in range(0, len(items), chunk_size):
        batch = items[i : i + chunk_size]

        merged_buildings = [
            f for itm in batch for f in itm["buildings"]["features"]
        ]

        streets_seen: set[object] = set()
        merged_streets: list[dict] = []
        for itm in batch:
            for f in itm["streets"]["features"]:
                fid = f.get("id")
                if fid is None or fid not in streets_seen:
                    merged_streets.append(f)
                    if fid is not None:
                        streets_seen.add(fid)

        flood_seen: set[object] = set()
        merged_flood: list[dict] = []
        for itm in batch:
            for f in itm["flood_layers"]["features"]:
                fid = f.get("id")
                if fid is None or fid not in flood_seen:
                    merged_flood.append(f)
                    if fid is not None:
                        flood_seen.add(fid)

        batched.append({
            **shared_params,
            "unit_id": ",".join(itm["unit_id"] for itm in batch),
            "buildings": {"type": "FeatureCollection", "features": merged_buildings},
            "streets": {"type": "FeatureCollection", "features": merged_streets},
            "flood_layers": {"type": "FeatureCollection", "features": merged_flood},
        })

    return batched


@register_process("pluvial-flood-risk-citywide")
class PluvialFloodRiskCitywide(BasePfRProcess, BaseParallelProcess):
    """Pluvial flood risk at building level — city-wide data fan-out variant.

    Partitions the city by statistical unit and runs each unit on a dedicated
    Celery worker.  Vulnerability is computed globally in ``split_inputs`` so
    TOPSIS normalisation is always city-wide.  Head/tail classification in
    ``merge_results`` is also city-wide (or boundary-subset when
    ``calculation_boundary`` is set).
    """

    process_description = ProcessDescription.from_yaml(
        str(settings.process_description_pfr_citywide_file)
    )

    # ------------------------------------------------------------------
    # Coordinator — load full dataset, global vulnerability, partition
    # ------------------------------------------------------------------

    def split_inputs(self, exec_body: dict) -> list[dict]:
        """Compute global vulnerability and partition buildings by statistical unit.

        Returns one work-item dict per statistical unit.  Each item contains the
        pre-projected GeoJSON features for the unit's buildings (with vulnerability
        scores already attached) plus the halo-clipped streets and flood layers.

        Args:
            exec_body: Resolved execution body (remote inputs already downloaded).

        Returns:
            List of work-item dicts, one per unit with buildings.
        """
        inp = ProcessInputs.from_exec_body(exec_body)
        _t0 = time.perf_counter()

        # 1. Build city-wide buildings GDF (metric CRS)
        blds_gdf = convert_geojson_to_gdf(inp.buildings["features"])
        # Capture the source CRS before projection so we can restore it on output.
        # When target_crs is set, calculations and outputs use that CRS.
        source_crs = blds_gdf.crs.to_string() if blds_gdf.crs else "EPSG:4326"
        output_crs = inp.target_crs or source_crs
        blds_metric = project_crs_to_metric(blds_gdf, inp.target_crs)
        blds_metric[BUILDING_FOOTPRINT_AREA_M2] = blds_metric.geometry.area

        # 2. Join statistical unit attributes when residents / unit IDs are absent
        stat_gdf = convert_geojson_to_gdf(
            inp.statistical_units["features"]
        ) if inp.statistical_units else None

        needs_join = any(
            col not in blds_metric.columns
            for col in (RESIDENTS_IN_STATISTICAL_UNIT, STATISTICAL_UNIT_ID)
        )
        if needs_join:
            if stat_gdf is None:
                raise ValueError(
                    "statistical_units must be provided when buildings lack "
                    "'residents_in_statistical_unit' or 'statistical_unit_id'."
                )
            blds_metric = join_statistical_unit_attributes(blds_metric, stat_gdf)

        # 3. Compute living_area_m2_statistical_unit city-wide (used by exposure)
        if LIVING_AREA_M2_STATISTICAL_UNIT not in blds_metric.columns:
            living_area = (
                (blds_metric[FLOORS_ABOVE_GROUND] - (blds_metric[BUILDING_TYPE] - 1))
                * blds_metric[BUILDING_FOOTPRINT_AREA_M2]
            )
            blds_metric[LIVING_AREA_M2_STATISTICAL_UNIT] = (
                living_area.groupby(blds_metric[STATISTICAL_UNIT_ID]).transform("sum")
            )

        # 4. Global vulnerability pipeline (TOPSIS over all city buildings)
        if "id" in blds_metric.columns:
            blds_metric = blds_metric.set_index("id")
        # Kept distinct from blds_metric to avoid overwriting the input dataset.
        blds_scored = run_vulnerability_pipeline(
            blds_metric, inp.vulnerability_parameters
        )
        logger.info(
            "split_inputs: global vulnerability done in %.1fs, %d buildings",
            time.perf_counter() - _t0, len(blds_scored),
        )

        # 5. Optional boundary clip: only score buildings inside the boundary
        if inp.calculation_boundary is not None:
            before = len(blds_scored)
            blds_scored = filter_gdf_to_boundary(blds_scored, inp.calculation_boundary)
            logger.info(
                "split_inputs: calculation_boundary applied — %d/%d buildings",
                len(blds_scored), before,
            )

        # 6. Build streets and flood GDFs (metric CRS)
        streets_metric = project_crs_to_metric(
            convert_geojson_to_gdf(inp.streets["features"]),
            inp.target_crs,
        )
        flood_metric = project_crs_to_metric(
            convert_geojson_to_gdf(inp.flood_layers["features"]),
            inp.target_crs,
            blds_scored,
        )

        # 7. Partition by statistical unit and build work items
        shared_params = {
            "target_crs": inp.target_crs,
            "output_crs": output_crs,
            "hazard_parameters": inp.hazard_parameters.model_dump(),
            "risk_weights": inp.risk_weights.model_dump(),
            "output_settings": inp.output_settings.model_dump(),
            "n_classification_iterations": inp.n_classification_iterations,
        }

        items: list[dict] = []
        for unit_id, unit_blds in blds_scored.groupby(STATISTICAL_UNIT_ID):
            halo = unit_blds.buffer(_HAZARD_BUFFER_HALO_M).union_all()
            halo_streets = streets_metric[streets_metric.intersects(halo)]
            halo_flood = flood_metric[flood_metric.intersects(halo)]

            # Serialise with the building-ID index kept as the GeoJSON feature id
            # field so execute_single can reconstruct the ID unambiguously via
            # convert_geojson_to_gdf's canonical-ID logic (f.get("id")).
            items.append({
                **shared_params,
                "unit_id": str(unit_id),
                "buildings": json.loads(unit_blds.to_json()),
                "streets": json.loads(halo_streets.to_json()),
                "flood_layers": json.loads(halo_flood.to_json()),
            })

        n_units = len(items)
        items = _batch_unit_items(items, settings.citywide_chunk_size)

        logger.info(
            "split_inputs: %d statistical units → %d work items (%.1fs total)",
            n_units, len(items), time.perf_counter() - _t0,
        )
        return items

    # ------------------------------------------------------------------
    # Worker — exposure + hazard + risk on one statistical unit
    # ------------------------------------------------------------------

    def execute_single(
        self,
        item: dict,
        job_progress_callback: JobProgressCallback | None = None,
    ) -> _ChunkResult:
        """Run the local pipelines (exposure, hazard, risk) on one statistical unit.

        Receives a work-item produced by ``split_inputs`` containing pre-computed
        vulnerability scores and halo-clipped streets/flood features.

        Args:
            item: Work-item dict from split_inputs.

        Returns:
            _ChunkResult with WGS-84 building features (risk scores in properties).
        """
        target_crs = item["target_crs"]
        hazard_params = HazardParams(**item["hazard_parameters"])
        risk_weights = RiskWeights(**item["risk_weights"])
        unit_id = item.get("unit_id", "?")

        # Reconstruct GDFs from serialised GeoJSON (coordinates in metric CRS).
        # Use convert_geojson_to_gdf so that empty feature lists (e.g. a unit
        # with no streets or flood features in its halo) are handled safely —
        # gpd.GeoDataFrame.from_features([], crs=...) raises ValueError in
        # geopandas 1.1 because the returned plain DataFrame has no geometry column.
        blds_gdf = convert_geojson_to_gdf(
            item["buildings"]["features"], target_crs
        )
        streets_gdf = convert_geojson_to_gdf(
            item["streets"]["features"], target_crs
        )
        flood_gdf = convert_geojson_to_gdf(
            item["flood_layers"]["features"], target_crs
        )

        if "id" in blds_gdf.columns:
            blds_gdf = blds_gdf.set_index("id")

        logger.info(
            "execute_single[%s]: %d buildings, %d streets, %d flood features",
            unit_id, len(blds_gdf), len(streets_gdf), len(flood_gdf),
        )
        _t0 = time.perf_counter()

        # Exposure (living_area_m2_statistical_unit already pre-computed)
        _crs = blds_gdf.crs
        blds_gdf = gpd.GeoDataFrame(
            run_exposure_pipeline(blds_gdf), geometry="geometry", crs=_crs
        )

        if flood_gdf.empty:
            # No flood features intersect this unit's halo — hazard is 0.
            # (Column schema is lost on empty serialised GDFs so hazard pipelines
            # would raise; setting 0 is semantically correct: no flood = no hazard.)
            logger.warning(
                "execute_single[%s]: no flood features in halo — hazard set to 0",
                unit_id,
            )
            blds_gdf[HAZARD_WELL_BEING] = 0.0
            blds_gdf[HAZARD_MOBILITY_ACCESSIBILITY] = 0.0
        else:
            # Hazard WB
            hazard_wb_result = run_hazard_wb_pipeline(blds_gdf, flood_gdf, hazard_params)
            blds_gdf[HAZARD_WELL_BEING] = hazard_wb_result[HAZARD_WELL_BEING]

            # Hazard MA
            ma_level = hazard_params.ma_flood_level_cm
            ma_flood = (
                flood_gdf[flood_gdf["level_cm"] == ma_level]
                if "level_cm" in flood_gdf.columns
                else flood_gdf
            )
            hazard_ma_result = run_hazard_ma_pipeline(
                blds_gdf, streets_gdf, ma_flood, hazard_params
            )
            blds_gdf[HAZARD_MOBILITY_ACCESSIBILITY] = hazard_ma_result[
                HAZARD_MOBILITY_ACCESSIBILITY
            ]

        # Risk
        risk_result = run_risk_pipeline(blds_gdf, risk_weights)
        for col in (PLUVIAL_FLOOD_RISK_WELL_BEING, PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY):
            blds_gdf[col] = risk_result[col]

        logger.info(
            "execute_single[%s]: pipelines done in %.1fs",
            unit_id, time.perf_counter() - _t0,
        )

        # Reproject to the requested output CRS (target_crs when specified,
        # otherwise the original input CRS — typically EPSG:4326 for GeoJSON)
        output_crs = item.get("output_crs", "EPSG:4326")
        blds_out = blds_gdf.to_crs(output_crs)

        risk_wb = [
            float(v) if pd.notna(v) else None
            for v in blds_gdf[PLUVIAL_FLOOD_RISK_WELL_BEING]
        ]
        risk_ma = [
            float(v) if pd.notna(v) else None
            for v in blds_gdf[PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY]
        ]

        # Serialise to GeoJSON features (risk class labels added later in merge)
        features = json.loads(blds_out.to_json())["features"]
        return _ChunkResult(features=features, risk_wb=risk_wb, risk_ma=risk_ma)

    # ------------------------------------------------------------------
    # Coordinator — global classification and final output
    # ------------------------------------------------------------------

    def merge_results(
        self,
        results: list[dict],
        exec_body: dict,
        job_progress_callback: JobProgressCallback | None = None,
    ) -> PfRiskResult:
        """Merge all partition results, run global classification, build output.

        Args:
            results: List of serialised _ChunkResult dicts from execute_single.
            exec_body: Original execution body (used for output settings, requested
                outputs, and decimal precision).

        Returns:
            PfRiskResult with GeoJSON FeatureCollection and classification breaks.
        """
        inp = ProcessInputs.from_exec_body(exec_body)
        requested = exec_body.get("outputs")

        # Collect features and risk values from all chunks
        all_features: list[dict] = []
        all_risk_wb: list[float | None] = []
        all_risk_ma: list[float | None] = []
        for chunk in results:
            all_features.extend(chunk.get("features", []))
            all_risk_wb.extend(chunk.get("risk_wb", []))
            all_risk_ma.extend(chunk.get("risk_ma", []))

        logger.info("merge_results: %d buildings across all units", len(all_features))

        # Build risk DataFrame indexed by building ID so that classification
        # labels can be looked up by ID in build_enriched_features (avoids
        # positional-index fragility when chunks arrive out of order).
        building_ids = [str(f.get("id")) for f in all_features]
        risk_df = pd.DataFrame(
            {
                PLUVIAL_FLOOD_RISK_WELL_BEING: all_risk_wb,
                PLUVIAL_FLOOD_RISK_MOBILITY_ACCESSIBILITY: all_risk_ma,
            },
            index=building_ids,
            dtype="float64",
        )
        risk_df = apply_optional_smoothing(risk_df, all_features, inp)
        return finalize_risk_outputs(
            risk_df, all_features, inp, requested,
            output_columns=list(_OUTPUT_COLUMNS),
            candidate_cols=list(_OUTPUT_COLUMNS),
        )

