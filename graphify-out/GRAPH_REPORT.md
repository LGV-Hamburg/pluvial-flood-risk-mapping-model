# Graph Report - flood-risk-mapping-model  (2026-10-25)

## Corpus Check
- 83 files · ~79,254 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1289 nodes · 1938 edges · 122 communities (85 shown, 37 thin omitted)
- Extraction: 81% EXTRACTED · 19% INFERRED · 0% AMBIGUOUS · INFERRED: 372 edges (avg confidence: 0.68)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6a490c41`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 104|Community 104]]
- [[_COMMUNITY_Community 105|Community 105]]
- [[_COMMUNITY_Community 106|Community 106]]
- [[_COMMUNITY_Community 107|Community 107]]
- [[_COMMUNITY_Community 108|Community 108]]
- [[_COMMUNITY_Community 109|Community 109]]
- [[_COMMUNITY_Community 110|Community 110]]
- [[_COMMUNITY_Community 111|Community 111]]
- [[_COMMUNITY_Community 113|Community 113]]
- [[_COMMUNITY_Community 114|Community 114]]
- [[_COMMUNITY_Community 115|Community 115]]
- [[_COMMUNITY_Community 116|Community 116]]
- [[_COMMUNITY_Community 117|Community 117]]
- [[_COMMUNITY_Community 118|Community 118]]
- [[_COMMUNITY_Community 119|Community 119]]
- [[_COMMUNITY_Community 120|Community 120]]
- [[_COMMUNITY_Community 121|Community 121]]

## God Nodes (most connected - your core abstractions)
1. `HazardParams` - 65 edges
2. `RiskWeights` - 45 edges
3. `ClassificationResult` - 36 edges
4. `VulnerabilityParams` - 29 edges
5. `PipelineParams` - 24 edges
6. `PfRiskResult` - 21 edges
7. `flooded_fraction_col()` - 18 edges
8. `PluvialFloodRiskProcess` - 17 edges
9. `run_vulnerability_pipeline()` - 17 edges
10. `run_classification_pipeline()` - 17 edges

## Surprising Connections (you probably didn't know these)
- `test_projected_buildings_survive_exact_wgs84_boundary_filter()` --calls--> `filter_features_to_boundary()`  [INFERRED]
  tests/test_spatial_utils.py → src/pluvial_flood_risk/spatial_utils.py
- `test_wgs84_features_survive_when_target_crs_is_projected()` --calls--> `filter_features_to_boundary()`  [INFERRED]
  tests/test_spatial_utils.py → src/pluvial_flood_risk/spatial_utils.py
- `default_params()` --calls--> `VulnerabilityParams`  [INFERRED]
  tests/test_pluvial_flood_risk.py → src/pluvial_flood_risk/models/inputs.py
- `params_default()` --calls--> `HazardParams`  [INFERRED]
  tests/test_pluvial_flood_risk.py → src/pluvial_flood_risk/models/inputs.py
- `test_projected_buildings_intersect_wgs84_boundary_units()` --calls--> `filter_features_to_intersecting_units()`  [INFERRED]
  tests/test_spatial_utils.py → src/pluvial_flood_risk/spatial_utils.py

## Communities (122 total, 37 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (44): Run the full mobility & accessibility hazard pipeline (Steps 3.1–3.2).      Call, run_hazard_ma_pipeline(), compute_hazard_wb(), compute_wb_flood_fractions(), flooded_area_col(), flooded_fraction_col(), Compute the flooded fraction within the 2 m building buffer per depth class., Compute the well-being hazard index HWB (Step 4.2, Eq. 12).      .. math:: (+36 more)

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (28): compute_building_living_area(), compute_exposure_mobility_accessibility(), compute_residents_ground_floor(), compute_residents_per_building(), Ground-floor resident count / well-being exposure index (Step 2, Eq. 9).      .., Mobility & accessibility exposure index (Step 2, Eq. 10).      EMA = R (total re, Run the complete exposure pipeline (Step 2).      Parameters     ----------, Net usable floor area of the building (Step 2, Eq. 8a).      .. math:: (+20 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (29): PFRAnalyzer, Pluvial Flood Risk Analyzer., Run all steps. Returns (buildings_gdf, statistical_units_gdf)., - paths.buildings            - paths.statistical_units            - paths.street, calculate_exposure_residents(), calculate_exposure_wellbeing(), Exposure calculation for pluvial flood risk., Distribute statistical-unit population to individual buildings     proportionall (+21 more)

### Community 3 - "Community 3"
Cohesion: 0.05
Nodes (39): 1. Objective, 2. Why a new process (framework constraint), 3. Algorithm: global vs partitionable (grounded in code), 4. fastprocesses 0.22.0 capabilities used (verified in installed package), 5. Phased implementation, 6. New / changed files, 7. Locked decisions, Additions (+31 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (21): compute_hazard_ma(), Compute the mobility & accessibility hazard index HMA (Step 3.2, Eq. 13).      ., Verify Eq. 13 by feeding expected fractions from ArcPy into compute_hazard_ma()., Building 1: near-fully flooded → HMA ≈ 1., Building 3: small fractions → HMA ≈ 6.66e-13 (lognorm tail)., Building 4: intermediate fraction → HMA ≈ 0.332., Building 5: tiny 30m fraction → HMA ≈ 7.81e-8., Building 6: high 30m fraction → HMA ≈ 0.9995. (+13 more)

### Community 5 - "Community 5"
Cohesion: 0.1
Nodes (19): compute_risk(), Compute a single pluvial flood risk index (Eq. 14/15).      PFR = SV_PF^a × E^b, Compute PFR_WB and PFR_MA for all buildings (Step 5).      Expects *df* to alrea, run_risk_pipeline(), Exponent weights for the IPCC risk product formula (Step 5, Eq. 14 and 15)., RiskWeights, Build a minimal one-row or multi-row risk DataFrame., Unit tests for compute_risk (the core Eq. 14/15 formula). (+11 more)

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (16): ClassificationResult, classify_risk(), head_tail_breaks(), Apply Head/Tail Breaks classification to both PFR_WB and PFR_MA.      Expects *d, Output of :func:`run_classification_pipeline`.      Attributes     ----------, Compute Head/Tail Break thresholds via iterative mean.      NaN values are ignor, Assign an ordinal risk class label to each building value.      The class bounda, run_classification_pipeline() (+8 more)

### Community 7 - "Community 7"
Cohesion: 0.06
Nodes (34): Architekturentscheidung: Kein volles Hexagonal-Muster, Auflösungsreihenfolge (`PipelineParams.target_crs`), B1 — CRS handling (`_to_metric`), B2 — Statistical units join (`exposure_pipeline` + `spatial_utils.join_statistical_unit_attributes`), B3 — Flood layer filtering (`hazard_ma_pipeline`), B4 — Building ID extraction, B5 — Entropy weighting (`vulnerability_pipeline`), B6 — Degenerate geometry / empty spatial results (+26 more)

### Community 8 - "Community 8"
Cohesion: 0.1
Nodes (31): _add_common_args(), _build_parser(), _force_fixed_paths(), log(), main(), _patch_config_with_fixed_files(), _print_validation_report(), Local mode:     urban-pfr local hamburg_config.yaml     urban-pfr local config.y (+23 more)

### Community 9 - "Community 9"
Cohesion: 0.1
Nodes (29): _auth_headers(), decode_flatgeobuf_bytes(), download_flatgeobuf_pages_to_files(), dump_download_to_geopackage(), _encode_flatgeobuf_bytes(), fetch_ogc_features(), fetch_token(), _get_bytes() (+21 more)

### Community 10 - "Community 10"
Cohesion: 0.09
Nodes (23): BaseParallelProcess, BaseProcess, BaseScatterProcess, BatchUpperProcess, CharCountResult, Config, count_chars(), count_words() (+15 more)

### Community 11 - "Community 11"
Cohesion: 0.19
Nodes (25): apply_pragmas(), bad(), bench_bbox(), bench_full_scan(), bench_pk_lookup(), check_indexes(), check_pragmas(), check_writable() (+17 more)

### Community 12 - "Community 12"
Cohesion: 0.1
Nodes (23): BaseModel, TextModel, BuildingProperties, CopingCapacityWeights, FloodLayerProperties, HealpixParams, OutputParams, Pydantic parameter models for the pluvial flood risk pipeline.  All classes carr (+15 more)

### Community 13 - "Community 13"
Cohesion: 0.08
Nodes (25): building_gdf(), building_gdf_wb(), default_params(), exposure_df(), flood_30cm(), flood_gdf_full(), flood_gdf_none(), flood_multi_depth() (+17 more)

### Community 14 - "Community 14"
Cohesion: 0.16
Nodes (21): convert_geojson_to_gdf(), expand_boundary_by_meters(), filter_features_to_boundary(), filter_gdf_to_boundary(), join_statistical_unit_attributes(), project_crs_to_metric(), Spatial utility helpers shared across pipeline branches., Parse a GeoJSON features list into a GeoDataFrame.      Handles two common data (+13 more)

### Community 15 - "Community 15"
Cohesion: 0.09
Nodes (22): code:bash (cp .env.example .env), code:bash (# One-time: install poetry globally, isolated from any proje), code:bash (poetry add numpy                      # runtime → updates py), code:bash (poetry update            # update all within declared constr), code:bash (pre-commit install), code:bash (pre-commit run --all-files), code:bash (cp .env.example .env), code:block8 (├── src/{{ python_package_import_name }}/   # importable pac) (+14 more)

### Community 16 - "Community 16"
Cohesion: 0.12
Nodes (16): drop_invalid_features(), load_schema_file(), Input validation helpers for the pluvial flood risk pipeline., Load a JSON Schema file and return a referencing ``Resource``., Return a new FeatureCollection with invalid features removed.      A feature is, BasePfRProcess, _build_pfr_schema_registry(), _inject_bbox_params() (+8 more)

### Community 17 - "Community 17"
Cohesion: 0.12
Nodes (8): _bbox(), exec_body(), Integration tests for the PluvialFloodRiskProcess pipeline branches.  What is an, Return a closed GeoJSON Polygon for an axis-aligned bounding box., Minimal but realistic OGC exec_body with three Hamburg buildings.      Building, test_buildings_local_excludes_invalid_buildings(), test_regional_process_keeps_wgs84_downloads_with_local_target_crs(), test_regional_process_reads_inline_data_in_target_crs()

### Community 18 - "Community 18"
Cohesion: 0.1
Nodes (10): Closeness must increase monotonically with a single ascending indicator., Reproduce sensitivity calculation for two archetypal buildings., Unit tests for the TOPSIS algorithm., With a single indicator, the extreme buildings must score 0 and 1., All-identical rows → zero-column-sum → all closeness = 0., All-zero input → zero column sums → all closeness = 0., Without entropy the output must lie in [0, 1]., With entropy the output must lie in [0, 2]. (+2 more)

### Community 19 - "Community 19"
Cohesion: 0.1
Nodes (16): buildings_gdf(), expected(), exposure_result(), flood_30_gdf(), flood_all_gdf(), formula_hma(), Regression tests using the original Hamburg example data.  These tests load the, Buildings projected to ETRS89 / UTM-32N with footprint area column. (+8 more)

### Community 20 - "Community 20"
Cohesion: 0.1
Nodes (19): ANNEX:, ./bin/build-image, ./bin/execute, code:bash (git clone https://github.com/FAIR2Adapt/urban_pfr_toolbox_ha), code:bash (./bin/build-image), code:bash (pip install -e .), code:yaml (# YAML), Configure (+11 more)

### Community 21 - "Community 21"
Cohesion: 0.15
Nodes (17): _add_bivariate_legend(), classify_values(), create_risk_visualization(), get_cmap_and_norm(), head_tail_breaks(), _plot_combined_panel(), plot_pfrwb_building_level(), plot_risk_panel() (+9 more)

### Community 22 - "Community 22"
Cohesion: 0.11
Nodes (9): exposure_pipeline: GeoJSON exec_body (with geometry) → exposure dict.      This, The UTM-projected polygon area must be strictly positive., The three buildings share one stat unit (500 residents).         Their combined, Building 3 is type 2 (commercial GF) → R_G must be 0., Building 1 has 1 floor → R_G = R / 1 = R., Invariant: R_G ≤ R for every building., EMA is identical to R — all residents are at risk of mobility loss., Building 2 (4 floors, type 1) has a larger footprint × 4 floors.         Buildin (+1 more)

### Community 23 - "Community 23"
Cohesion: 0.12
Nodes (14): PipelineParams, Aggregated parameters for the full pluvial flood risk pipeline.      Construct w, classification_result(), Run the full risk pipeline using our own component outputs.      SV_PF comes fro, Head/Tail Breaks classification applied to the example risk values., Verify Eq. 14/15 by feeding back-calculated component values.      All buildings, Confirms SV_PF is shared: same implied factor for both risk indices., compute_risk(implied_SV_PF, EWB, HWB) == PFR_WB for all buildings. (+6 more)

### Community 24 - "Community 24"
Cohesion: 0.16
Nodes (10): Module 6 — Risk classification (Step 6, Head/Tail Breaks).  Implements iterative, Exposure pipeline — Step 2.  Computes building-level resident counts following V, Hazard pipeline — Module 3: Mobility & Accessibility (Steps 3.1–3.2).  Implement, Hazard pipeline — Module 4: Well-Being / Health (Steps 4.1–4.2).  Implements the, Algorithm modules for the pluvial flood risk pipeline., Module 5 — Risk calculation (Step 5, Eq. 14/15).  Implements the IPCC risk produ, Central field-name constants for the pluvial flood risk pipeline.  Mapping from, _batch_unit_items() (+2 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (16): aggregate_to_healpix(), _count_coords(), create_thiessen_polygons(), Aggregation helpers (HEALPix, Thiessen) for pluvial_flood_risk.  This module pro, Replace building footprint geometries with Thiessen (Voronoi) polygons.      Eac, Count total coordinate pairs in a Shapely geometry (recursively)., Aggregate building-level results to HEALPix cells on WGS84.      The function mi, build_enriched_features() (+8 more)

### Community 26 - "Community 26"
Cohesion: 0.11
Nodes (16): Input/output utilities for pluvial flood risk processes., _before_sleep_log(), fetch_flatgeobuf(), fetch_ogc_features(), fetch_references_cached(), _is_retryable(), _next_link(), _open_sessions() (+8 more)

### Community 27 - "Community 27"
Cohesion: 0.12
Nodes (8): Exposure pipeline against real example buildings.      Absolute resident counts, Building type 2 (commercial ground floor) must have R_G = 0., For single-storey type-1 buildings R_G must equal R., Multi-storey type-1 buildings: R_G = R / floors < R., EMA equals R for all buildings (Eq. 10)., Rank order of EWB matches original rank order (Spearman ≥ 0.7)., Building 4 (type=2) should have EWB=0 in both ours and expected., TestExposureExampleData

### Community 28 - "Community 28"
Cohesion: 0.12
Nodes (3): merge_results wires Step 5 and Step 6 on top of Steps 1–4., A coarse depth + min_buildings=1 groups all 3 nearby buildings          into at, TestMergeResultsIntegration

### Community 29 - "Community 29"
Cohesion: 0.12
Nodes (7): Vulnerability pipeline against real example buildings.      Absolute values diff, With entropy multiplier U ∈ [1, 2], scores lie in [0, 2]., Eq. 7 threshold shift prevents SV_PF from reaching zero., Building 7 (C=3, ES=2, WR=4, EDQ=1) should have the highest SVI., Building 8 has WR=1 and no other CC indicator: CCap should be low., SV_PF must increase with SVI (the power transform is monotone)., TestVulnerabilityExampleData

### Community 30 - "Community 30"
Cohesion: 0.16
Nodes (12): BasePfRProcess, ProcessInputs, Typed wrapper for the OGC exec_body inputs dict.      Extends ``PipelineParams``, PluvialFloodRiskCitywide, Pluvial flood risk at building level — city-wide data fan-out variant.      Part, Merge all partition results, run global classification, build output.          A, PluvialFloodRiskProcess, Pluvial flood risk at building level following Vogelbacher et al. (2025).      F (+4 more)

### Community 31 - "Community 31"
Cohesion: 0.2
Nodes (7): compute_mobility_flood_fractions(), Compute flooded area and fraction for each of the three mobility zones.      The, Step 3.1 — spatial preprocessing with synthetic geometry., With flood covering the entire north side, the 5 m buffer is partially         f, flooded_area / zone_area should equal the fraction (where area > 0)., Two buildings far apart should produce independent fractions., TestComputeMobilityFloodFractions

### Community 32 - "Community 32"
Cohesion: 0.14
Nodes (6): Verify the full run_hazard_wb_pipeline against example data.      Because buffer, All flooded_fraction_2m_*cm columns must be in [0, 1]., Buildings with all-null P2Flood in ArcPy should also have near-zero HWB., Spearman rank correlation between spatial HWB and expected HWB ≥ 0.7., where fraction > 0, flooded area must also be > 0., TestHazardWBSpatial

### Community 33 - "Community 33"
Cohesion: 0.2
Nodes (13): compute_global_stats(), compute_svi(), flood_susceptibility_transform(), Vulnerability pipeline — Steps 1.1-1.4.  Implements the TOPSIS-based Social Vuln, Step 1.3 — Social Vulnerability Index (SVI) via TOPSIS (Eq. 4-6).      Inputs: `, Step 1.4 — Non-linear flood susceptibility transform (Eq. 7).      .. math::, Extract columns, run :func:`topsis`, propagate NaN, return named Series.      Ro, Compute all city-wide statistics needed to run the vulnerability     pipeline on (+5 more)

### Community 34 - "Community 34"
Cohesion: 0.24
Nodes (7): Run the complete vulnerability pipeline (Steps 1.1-1.4).      Parameters     ---, run_vulnerability_pipeline(), All parameters controlling the vulnerability pipeline (Steps 1.1-1.4)., VulnerabilityParams, Every building must have SV_PF > 0 (zero-floor guarantee)., Smoke-test against the first 10 rows of the example data file., TestRunVulnerabilityPipeline

### Community 35 - "Community 35"
Cohesion: 0.15
Nodes (6): Verify Eq. 12 (HWB) by feeding ArcPy flood fractions into compute_hazard_wb., Building 1 has all depth classes heavily flooded; HWB ≈ 6.9987., All 37 buildings should match ArcPy HWB to < 1 % relative error., Buildings with no flood data should produce HWB = 0., Spearman rank correlation between formula HWB and expected HWB = 1.0., TestHazardWBFormula

### Community 36 - "Community 36"
Cohesion: 0.17
Nodes (10): compute_topsis_stats(), TOPSIS - Technique for Order of Preference by Similarity to Ideal Solution.  Pur, Compute TOPSIS closeness coefficients for a decision matrix.      Parameters, Compute the global statistics needed to replay this TOPSIS step on a partition., topsis(), GlobalStats, Internal pipeline models — not part of the user-facing process input schema.  Th, Global statistics needed to replay one TOPSIS step on a data partition.      Com (+2 more)

### Community 37 - "Community 37"
Cohesion: 0.15
Nodes (13): Inputs, Inputs, Inputs, Inputs, Modul 1 – Vulnerabilität (Social Vulnerability Index) ✅, Outputs, Outputs, Outputs (+5 more)

### Community 38 - "Community 38"
Cohesion: 0.3
Nodes (10): _build_payload(), _extract_outputs(), _load_example_geojson(), _load_json_from_url(), _post_execution(), End-to-end tests that execute the full API + worker + Redis pipeline.  These tes, _result_url_from_job(), test_e2e_full_pipeline() (+2 more)

### Community 39 - "Community 39"
Cohesion: 0.2
Nodes (4): BaseProcessResult, _fc_to_fgb(), PfRiskResult, Combined output of the pluvial flood risk pipeline.      Fields mirror the proce

### Community 40 - "Community 40"
Cohesion: 0.17
Nodes (12): code:python (C = d_from_min / (d_from_max + d_from_min)), code:python (M[i]          # Zeile i: alle Indikatorwerte eines einzelnen), code:block7 (Kinder (C)  Ältere (ES)), code:python (M[:, j]          # die gesamte Spalte j (alle Zeilen, Spalte), code:python (ideal_max = M.max(axis=0)   # axis=0: Spaltenmaxima → Worst-), Die Daten als Matrix, Erläuterung: NumPy-Matrixoperationen am Beispiel TOPSIS, NumPy-Muster auf einen Blick (+4 more)

### Community 41 - "Community 41"
Cohesion: 0.18
Nodes (3): vulnerability_pipeline: GeoJSON exec_body → vulnerability output dict., Building 1 has the highest indicator values → highest SVI., TestVulnerabilityPipelineIntegration

### Community 42 - "Community 42"
Cohesion: 0.18
Nodes (4): Regression tests for run_classification_pipeline on real example data., With the default 4 iterations and sufficiently varied PFR values,         all 4, Spearman correlation between PFR_WB and risk_class_well_being ≥ 0.9.          He, TestClassificationPipelineRegression

### Community 43 - "Community 43"
Cohesion: 0.18
Nodes (4): Structural regression tests for the full risk pipeline on example data.      Abs, Where the original HWB=0, our HWB should also be ~0 → PFR_WB ≈ 0., Spearman rank correlation between our PFR_WB and expected PFR_WB ≥ 0.5., TestRiskPipelineStructural

### Community 45 - "Community 45"
Cohesion: 0.2
Nodes (6): exposure_pipeline — run with statistical_units as separate input.      Verifies, Both input modes must yield numerically identical results., If neither mode is provided the process must fail with a clear message., Return a closed GeoJSON Polygon at the given lower-left corner., _rect(), TestExposurePipelineStatisticalUnitsJoin

### Community 46 - "Community 46"
Cohesion: 0.22
Nodes (8): code:bash (python3 -m pip install --user pipx), code:block2 (make test), code:bash (python3 -m venv venv), code:bash (python3 -m pip install --user pipx), Contributing, Environment setup, Running tests, Serving docs

### Community 47 - "Community 47"
Cohesion: 0.32
Nodes (7): _delaunay_neighbors(), Module 5.5 — Optional Delaunay-neighbour risk smoothing.  Reimplements the algor, Smooth PFR_WB/PFR_MA columns of *risk_df* using Delaunay neighbours.      No-op, Return an adjacency list built from the Delaunay triangulation of *coords*., Run one inverse-distance-weighted smoothing pass, NaN-safe.      A building's ow, _smooth_pass(), smooth_risk_values()

### Community 48 - "Community 48"
Cohesion: 0.25
Nodes (8): _domain(), _fetch_all_references_async(), _fetch_reference_async(), _open_session_pools(), Async context manager yielding {domain: [session, ...]} pools., Return scheme://netloc for grouping URLs by server., Dispatch a single ``{href, type}`` reference to the appropriate fetcher., Fetch all references concurrently, n_sessions sessions per domain.

### Community 49 - "Community 49"
Cohesion: 0.25
Nodes (8): decode_flatgeobuf_bytes(), _fetch_flatgeobuf_async(), _get_bytes(), Fetch raw bytes and Content-Crs from *url* via GET.      Same retry / exception, Log and re-raise a request error with context after retries are exhausted., Decode FlatGeobuf bytes into a GeoJSON FeatureCollection dict.      Uses geopand, Async implementation of fetch_flatgeobuf., _wrap_request_error()

### Community 50 - "Community 50"
Cohesion: 0.25
Nodes (8): _fetch_page_with_offset(), _get_json(), Fetch a single page using offset and limit parameters., Add or replace the 'offset' query parameter (OGC API Features)., Add or replace the 'limit' query parameter (OGC API Features)., Fetch JSON from *url* via GET, respecting *semaphore*.      Raises native aiohtt, _with_limit(), _with_offset()

### Community 51 - "Community 51"
Cohesion: 0.25
Nodes (8): _assert_allowed(), _fetch_ogc_features_async(), _fetch_pages_speculative(), Async implementation of fetch_ogc_features.      Paging strategy (in priority or, Return (count, source) from numberMatched, or (None, '')., Parallel-batch paging without prior knowledge of total item count.      Issues o, Raise SSRFBlockedError if the URL origin is not in the allowlist.      Parameter, _resolve_total_count()

### Community 52 - "Community 52"
Cohesion: 0.25
Nodes (3): SV_PF = (SV + 0.25*mean(SV))^2 for default params., Buildings with SVI=0 must still receive a non-zero SV_PF., TestFloodSusceptibilityTransform

### Community 53 - "Community 53"
Cohesion: 0.29
Nodes (5): compute_coping_capacity(), Step 1.2 — Coping capacity index via TOPSIS (Eq. 4-6).      Indicators: ``social, SVI computation needs sensitivity_index and coping_capacity_index., TestComputeCopingCapacity, TestComputeSvi

### Community 54 - "Community 54"
Cohesion: 0.25
Nodes (8): Geometrische Konstruktion (v2), Inputs, Inputs (v2 — vereinfacht gegenüber v1), Modul 3 – Gefährdung: Mobilität & Erreichbarkeit ✅, Outputs, Outputs, Step 3.1 – Räumliche Verschneidung Mobilitätsgefährdung ✅, Step 3.2 – Mobilitätsgefährdungsindex (Log-Normal) ✅

### Community 56 - "Community 56"
Cohesion: 0.33
Nodes (6): filter_features_to_intersecting_units(), Return features intersecting the union of *units* that intersect *boundary*., test_projected_buildings_intersect_wgs84_boundary_units(), test_projected_buildings_survive_exact_wgs84_boundary_filter(), test_projected_units_and_buildings_intersect_wgs84_boundary(), test_wgs84_features_survive_when_target_crs_is_projected()

### Community 57 - "Community 57"
Cohesion: 0.29
Nodes (6): _fetch_all_references_shim(), Positional-args shim around fetch_all_references for joblib caching., fetch_all_references(), _fetch_all_references_shim(), Fetch all ``{href, type}`` reference objects concurrently.      Creates *n_sessi, Positional-args shim around fetch_all_references for joblib caching.

### Community 58 - "Community 58"
Cohesion: 0.43
Nodes (3): compute_sensitivity(), Step 1.1 — Sensitivity index via TOPSIS (Eq. 4-6).      Indicators: ``children_u, TestComputeSensitivity

### Community 59 - "Community 59"
Cohesion: 0.29
Nodes (7): Flood Sensitivity Transformation, IPCC-Risikoformel (Potenzprodukt), Iterative Mittelwertklassifizierung (Head/Tail Breaks), Log-Normalverteilung / CDF (Gefährdungsberechnung), Methoden-Überblick (Vogelbacher et al., 2025), Shannon Entropy, TOPSIS *(Technique for Order of Preference by Similarity to Ideal Solution)*

### Community 60 - "Community 60"
Cohesion: 0.29
Nodes (6): Inputs, Modul 6 – Klassifizierung zur Visualisierung, Open ToDos (unstructured), Outputs, Step 6 – Iterative Mean Classification (Head/Tail Breaks), Steps

### Community 61 - "Community 61"
Cohesion: 0.29
Nodes (7): Inputs, Inputs, Modul 4 – Gefährdung: Wohlbefinden / Gesundheit ✅, Outputs, Outputs, Step 4.1 – Räumliche Verschneidung Gesundheitsgefährdung ✅, Step 4.2 – Gesundheitsgefährdungsindex (Log-Normal) ✅

### Community 62 - "Community 62"
Cohesion: 0.33
Nodes (3): _FixedUrlReferencePublisher, FastAPI / OGC API Processes entry point.  Start the API server:     uvicorn app:, Stub output-reference publisher that always returns a fixed href.      Discards

### Community 63 - "Community 63"
Cohesion: 0.33
Nodes (3): exposure_pipeline — spatial centroid-within join (no statistical_unit_id)., Spatial join must yield the same numeric results as attribute join., TestExposurePipelineSpatialJoin

### Community 64 - "Community 64"
Cohesion: 0.33
Nodes (4): BaseSettings, PfrSettings, Runtime settings for the pluvial flood risk pipeline.  All settings are read fro, Environment-driven settings for the pluvial flood risk pipeline.

### Community 65 - "Community 65"
Cohesion: 0.5
Nodes (4): enrich_geojson_with_building_type(), map_building_usage_type(), Map building usage category to type code and name.      Returns:         tuple:, Read a GeoJSON file, add mapped building usage columns, and write output.

### Community 66 - "Community 66"
Cohesion: 0.4
Nodes (4): build container image, code:{important}, code:bash (make build-image), Collection of useful commands

### Community 69 - "Community 69"
Cohesion: 0.5
Nodes (3): code:bash (python -m pytest -m "not e2e"), code:bash (python -m pytest -m integration), code:bash (RUN_E2E=1 python -m pytest -m e2e tests/test_e2e_pipeline.py)

### Community 70 - "Community 70"
Cohesion: 0.5
Nodes (3): code:block1 (poetry init), create python environment, How to setup your development environment

### Community 71 - "Community 71"
Cohesion: 0.5
Nodes (4): Inputs, Modul 2 – Exposition ✅, Outputs, Step 2 – Residents per Building / Ground Floor

### Community 72 - "Community 72"
Cohesion: 0.5
Nodes (4): Inputs, Modul 5 – Risikoberechnung ✅, Outputs, Step 5 – Risk Calculation

## Knowledge Gaps
- **512 isolated node(s):** `FastAPI / OGC API Processes entry point.  Start the API server:     uvicorn app:`, `Stub output-reference publisher that always returns a fixed href.      Discards`, `Return (mean_ms, stdev_ms) over `iterations` runs.`, `Raise SystemExit with a helpful message if the file cannot be written.`, `Map building usage category to type code and name.      Returns:         tuple:` (+507 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **37 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `HazardParams` connect `Community 0` to `Community 1`, `Community 4`, `Community 5`, `Community 6`, `Community 12`, `Community 13`, `Community 18`, `Community 19`, `Community 23`, `Community 27`, `Community 29`, `Community 30`, `Community 31`, `Community 32`, `Community 34`, `Community 35`, `Community 42`, `Community 43`, `Community 44`, `Community 52`, `Community 53`, `Community 58`?**
  _High betweenness centrality (0.090) - this node is a cross-community bridge._
- **Why does `PluvialFloodRiskProcess` connect `Community 30` to `Community 67`, `Community 68`, `Community 39`, `Community 41`, `Community 10`, `Community 45`, `Community 14`, `Community 16`, `Community 83`, `Community 22`, `Community 28`, `Community 63`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **Why does `ClassificationResult` connect `Community 6` to `Community 0`, `Community 1`, `Community 4`, `Community 5`, `Community 16`, `Community 18`, `Community 23`, `Community 24`, `Community 27`, `Community 29`, `Community 31`, `Community 32`, `Community 34`, `Community 35`, `Community 39`, `Community 42`, `Community 43`, `Community 44`, `Community 52`, `Community 53`, `Community 58`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Are the 55 inferred relationships involving `HazardParams` (e.g. with `TestTopsis` and `TestComputeSensitivity`) actually correct?**
  _`HazardParams` has 55 INFERRED edges - model-reasoned connections that need verification._
- **Are the 41 inferred relationships involving `RiskWeights` (e.g. with `TestTopsis` and `TestComputeSensitivity`) actually correct?**
  _`RiskWeights` has 41 INFERRED edges - model-reasoned connections that need verification._
- **Are the 33 inferred relationships involving `ClassificationResult` (e.g. with `TestTopsis` and `TestComputeSensitivity`) actually correct?**
  _`ClassificationResult` has 33 INFERRED edges - model-reasoned connections that need verification._
- **Are the 26 inferred relationships involving `VulnerabilityParams` (e.g. with `TestTopsis` and `TestComputeSensitivity`) actually correct?**
  _`VulnerabilityParams` has 26 INFERRED edges - model-reasoned connections that need verification._