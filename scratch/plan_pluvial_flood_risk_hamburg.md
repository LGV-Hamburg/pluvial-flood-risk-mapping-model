# Refactoring Plan: `PluvialFloodRiskHamburg` — data fan-out, URL download, structured/binary output

**Status:** Phase 2 complete, Phase 3–7 pending
**Target framework:** `fastprocesses` 0.22.0
**Date:** 2026-06-22

---

## Implementation Progress

| Phase | Status | Notes |
|-------|--------|-------|
| 0: Process description update | ✅ Complete | Added `{href, type}` reference branches to buildings, streets, flood_layers |
| 1: OGC Features paging + SSRF | ✅ Complete | `src/pluvial_flood_risk/io/remote.py` — async parallel paging via itemCount, SSRF allowlist, FlatGeobuf decode, semaphore rate-limiting |
| 2: Input model updates | ✅ Complete | `GlobalStats`; `ProcessInputs.calculation_boundary`; typed geojson-pydantic `FeatureCollection` aliases; `late_validate` + `resolve_remote_inputs` on `PluvialFloodRiskProcess`; `fetch_flatgeobuf` in io layer |
| 3: Global-stat refactors | ✅ Complete | `TopsisStats` + `GlobalStats` in `models/pipeline.py`; `compute_topsis_stats` in `topsis.py`; `compute_global_stats` + injectable `global_stats`/`svi_mean` in `vulnerability.py` |
| 4: Hamburg process | ✅ Complete | `PluvialFloodRiskCitywide(BasePfRProcess, BaseParallelProcess)` in `citywide_process.py`; `BasePfRProcess` mixin extracted to `_pfr_base.py`; `_ChunkResult` partial model; `split_inputs`/`execute_single`/`merge_results` implemented |
| 5: Hamburg YAML + registration | ✅ Complete | Process description + Django/FastAPI route registration (YAML file and `citywide_process.py` created) |
| 6: Binary output | ✅ Complete | `PfRiskHamburgResult(BaseProcessResult)` + serializers |
| 7: Tests | Pending | Phases 1–8 test coverage |

---

## 1. Objective

Add a new OGC API process `PluvialFloodRiskHamburg` built on the
**`BaseParallelProcess` data fan-out** mechanism (Option B). It must:

1. Download buildings, streets, and flood layers automatically from server-configured
   OGC API Features endpoints. Inline data may be provided to **override** any
   individual source (e.g. for testing). No URL input is exposed to the user.
2. Keep statistical units and all parameters **client-provided inline**.
3. Accept an optional **`calculation_boundary`** polygon that restricts which region
   is scored and returned. **Global steps still run on the whole building dataset.**
4. Produce **structured output** via `BaseProcessResult`, advertising both
   `application/geo+json` and the compact binary `application/flatgeobuf` so upstream
   clients can consume large results as binary bytes instead of bloated GeoJSON.
5. The existing `PluvialFloodRiskProcess` (`BaseScatterProcess`) is evolved in
   parallel: it gains a `resolve_remote_inputs` override so users may supply
   buildings, streets, and flood layers as an OGC API Features **`items` endpoint
   URL** (normative requirement) instead of inline data. FlatGeobuf is supported
   as a download format; inline inputs remain GeoJSON only.

The two processes map to distinct input models:

| | `PluvialFloodRiskProcess` (evolved) | `PluvialFloodRiskHamburg` (new) |
|---|---|---|
| Input source | User-announced `{href, type}` reference object | Server-side configured URL |
| User controls URL | Yes (SSRF allowlist enforced) | No — inline data overrides only |
| SSRF exposure | Needs allowlist | None |
| FlatGeobuf input | Via download (URL path only) | Via download (URL path only) |

---

## 2. Why a new process (framework constraint)

A process class inherits **exactly one** base class. `BaseScatterProcess`
(operation fan-out via `@parallel_step`) and `BaseParallelProcess` (data fan-out via
`split_inputs` / `execute_single` / `merge_results`) are **siblings and cannot be
mixed**. The Celery worker selects the orchestration based on which base class the
process subclasses. Therefore the four `@parallel_step` operation workers cannot run
*inside* an `execute_single` call.

Instead, `execute_single` calls the framework-agnostic **pipeline functions**
(`run_vulnerability_pipeline`, `run_exposure_pipeline`, `run_hazard_wb_pipeline`,
`run_hazard_ma_pipeline`, `run_risk_pipeline`) sequentially on one partition. We trade
4-way operation parallelism for **N-way data parallelism across partitions**, which is
what scales at city size.

---

## 3. Algorithm: global vs partitionable (grounded in code)

| Step | File | Scope | Reason |
|------|------|-------|--------|
| 1.1–1.3 TOPSIS | `topsis.py` | **GLOBAL** | L2 norm denominator `sum(M[:,j]**2)` and `ideal_max/min = M.max/min(axis=0)` span all rows |
| 1.4 SV_PF transform | `vulnerability.py` | **GLOBAL** | `svi_mean = valid.mean()` is a city-wide mean |
| 2 Exposure | `exposure.py` | **PARTITIONABLE** by statistical unit | `R = living_area / living_area_unit * residents_unit`; unit sums already present, no cross-unit dependency |
| 3 Hazard MA | `hazard_ma.py` | **PARTITIONABLE + halo** | Buffers out to 15–30 m ring (Zone 3) need neighbouring streets/flood |
| 4 Hazard WB | `hazard_wb.py` | **PARTITIONABLE + halo** | 2 m building buffer |
| 5 Risk product | `risk.py` | **FULLY LOCAL** | `PFR = SV_PF^a * E^b * H^c` per building |
| 6 Head/Tail breaks | `classification.py` | **GLOBAL** | `mean(arr)` over the whole-city distribution |

**Halo radius = 30 m** (max MA Zone-3 ring; covers the 2 m WB buffer too).
**Partition unit = statistical unit** (natural for exposure; tiles would have to be
unit-complete anyway).

Three-phase execution:
1. **Coordinator (`split_inputs`)** — load full dataset once, compute global stats
   (TOPSIS norms/ideals, `mean(SVI)`), run the global vulnerability pipeline, optionally
   clip to `calculation_boundary`, partition by statistical unit, attach a 30 m halo of
   streets/flood per partition, inject stats into each work item.
2. **Workers (`execute_single`)** — exposure + hazard WB + hazard MA + risk on one
   partition using injected stats; discard halo-only rows from the output.
3. **Coordinator (`merge_results`)** — concatenate scalar risk columns, run head/tail
   classification city-wide, build the `BaseProcessResult`.

---

## 4. fastprocesses 0.22.0 capabilities used (verified in installed package)

- **Hook pipeline** (worker invocation order):
  1. `validate_inputs` — validates the **wire-format** input against the process
     description JSON schema. When the client sends a `{href, type}` reference
     object, this validates the *object structure* (required keys, `type` enum), not
     the data behind the URL.
  2. `resolve_remote_inputs(exec_body) -> exec_body` — fetches URL-valued inputs
     and replaces them with the downloaded data. SSRF checks live here. Raise
     `SSRFBlockedError` / `ValueError` for user-facing errors. Cache key uses the
     **original** exec_body (URI strings).
  3. `late_validate(inputs) -> bool` — receives **resolved** inputs (actual
     FeatureCollections). Validate downloaded data against Pydantic models here.
     Raise `ValueError` to fail the job with a user-facing message.
- `BaseProcessResult(BaseModel)` (`fastprocesses.core.output_protocol`) — one field per
  output id + `output_serializers: ClassVar = {output_id: {media_type: method_name}}`;
  each serializer method returns **bytes**. `merge_results` **must** return a
  `BaseProcessResult` subclass.
- `serialize_result()` + `OutputSchemaResolver` (`core/outputs_handler.py`,
  `core/output_schema_resolver.py`) — resolve the requested output media type against the
  process description `oneOf` branches. Recognises `application/geo+json` (OGC hint
  `geojson-feature-collection`) and binary `application/flatgeobuf`. Priority order:
  geo+json → flatgeobuf → json → png.
  - `response=raw` → single output, `Response(content=bytes, media_type=…)` — the
    compact binary path.
  - `response=document` → JSON qualified-value wrappers; binary outputs base64-encoded.

> **Caveat:** `raw` mode builds the full payload in memory in a single FastAPI
> `Response` — it is not a chunked `StreamingResponse`, and `transmissionMode:"reference"`
> is resolved but not acted on. Clients get **compact binary** (FlatGeobuf) they can
> stream-consume. True chunked/range streaming of multi-GB bodies would need a later
> object-storage + href follow-up (out of scope here).

---

## 5. Phased implementation

### Phase 1 — URL download + OGC API Features paging ✅
**Implementation:** `src/pluvial_flood_risk/io/remote.py` + `src/pluvial_flood_risk/io/__init__.py`

Core download functions used by **both** processes via `resolve_remote_inputs`:

#### Main API
```python
def fetch_ogc_features(
    url: str,
    *,
    allowed_origins: list[str] | None = None,  # None = no SSRF check (server-side only)
    page_limit: int = 1000,
    timeout: float = 30.0,
) -> dict:
    """Fetch a complete GeoJSON FeatureCollection from an OGC API Features
    items endpoint. Sync wrapper around async implementation using asyncio.run().
    
    Uses the OGC collections endpoint to detect itemCount and performs parallel
    paging via offset/limit. Falls back to sequential next-link following if
    itemCount is unavailable.
    """
```

#### Features
- **Parallel paging** — detects `itemCount` from OGC collections endpoint; computes
  required page offsets; fetches all pages concurrently with `asyncio.gather()`.
- **Fallback sequential** — when `itemCount` unavailable, follows `links[rel=next]`
  until exhausted (back-compat with older servers).
- **FlatGeobuf support** — transparently decodes binary FlatGeobuf responses via
  `geopandas.read_file()` (avoids `flatgeobuf.load` crashes on paged responses).
- **SSRF protection** — when `allowed_origins` is supplied, validates every URL hop
  against the allowlist and raises `SSRFBlockedError` if blocked.
- **Sync interface** — wraps internal async implementation in `asyncio.run()` so
  it works in the sync `resolve_remote_inputs` hook.

#### Usage
- **`PluvialFloodRiskProcess`** (generic) — calls with `allowed_origins` env var
  (e.g. `FP_ALLOWED_INPUT_ORIGINS = ['https://trusted.example.com/']`).
- **`PluvialFloodRiskHamburg`** — calls with `allowed_origins=None` since URLs
  come from server config, not untrusted user input.

#### Helper functions (internal)
- `decode_flatgeobuf_bytes(data: bytes) -> dict` — decode FlatGeobuf → GeoJSON dict
- `_assert_allowed(url: str, allowed_origins: list[str]) -> None` — SSRF check
- `_with_offset(url: str, offset: int) -> str` — add/replace OGC offset param
- `_with_limit(url: str, limit: int) -> str` — add/replace OGC limit param
- `_next_link(fc: dict) -> str | None` — extract rel=next from FeatureCollection links

### Phase 2 — input model + validation hooks on PluvialFloodRiskProcess ✅
**Edited:** `src/pluvial_flood_risk/models/inputs.py`, `src/pluvial_flood_risk/processes/flood_risk_process.py`, `src/pluvial_flood_risk/io/remote.py`

- Inline `buildings` / `streets` / `flood_layers` accept **GeoJSON FeatureCollection
  objects only**. FlatGeobuf is supported exclusively via URL download — there is
  no inline base64 FlatGeobuf path.
- Added `calculation_boundary: dict | None` (optional Polygon/MultiPolygon GeoJSON).
- Added `GlobalStats` dataclass (placeholder — **superseded by Phase 3**; correct definition is three per-step `TopsisStats` instances plus `svi_mean`).
- Added typed `FeatureCollection` aliases using `geojson-pydantic`:
  `BuildingsFeatureCollection`, `StreetsFeatureCollection`,
  `FloodLayersFeatureCollection`, `StatisticalUnitsFeatureCollection`.
- Added `late_validate(inputs)` to `PluvialFloodRiskProcess` — validates resolved
  FeatureCollections via the typed aliases (geojson-pydantic `model_validate`).
- Added `resolve_remote_inputs(exec_body)` to `PluvialFloodRiskProcess`:
  - Detects `{href, type}` reference objects in `buildings`, `streets`, `flood_layers`.
  - Reads SSRF allowlist from `FP_ALLOWED_INPUT_ORIGINS` env var (JSON array or
    comma-separated). Raises `ValueError` if not set when a URL is supplied.
  - Dispatches to `fetch_ogc_features` (GeoJSON) or `fetch_flatgeobuf` (FlatGeobuf).
- Added `fetch_flatgeobuf(url, *, allowed_origins, timeout)` to `io/remote.py` and
  exported from `io/__init__.py` — fetches raw bytes and decodes via
  `decode_flatgeobuf_bytes`.

**Validation pipeline** (fastprocesses hook order, relevant for URL inputs):
1. `validate_inputs` — runs on the raw wire value. When the client passes a
   `{href, type}` reference object, this validates the *object structure*, not the
   downloaded data.
2. `resolve_remote_inputs` — detects reference objects, downloads and decodes the
   data (GeoJSON or FlatGeobuf → GeoJSON dict), and substitutes the resolved value
   back into `exec_body["inputs"]`. SSRF check happens here.
3. `late_validate` — receives resolved inputs (actual GeoJSON FeatureCollections)
   and validates them via typed geojson-pydantic aliases. This is the only place
   where downloaded data is structurally validated.

### Phase 3 — refactor global-stat functions (back-compatible)
**Edit:** `topsis.py`, `vulnerability.py`, `models/inputs.py`

#### Why `GlobalStats` as defined in Phase 2 is wrong

The vulnerability pipeline calls `topsis()` **three times**, each on a matrix with a
different number of columns:

| Step | Function | Columns | n_criteria |
|------|----------|---------|------------|
| 1.1 | `compute_sensitivity` | children_under_10, elderly_singles_over_65 [, single_parents] | 2 or 3 |
| 1.2 | `compute_coping_capacity` | social_welfare_recipients, no_highschool_diploma | 2 |
| 1.3 | `compute_svi` | sensitivity_index, coping_capacity_index | 2 |

Each call produces its own L2 norms, ideal_max, and ideal_min of length `n_criteria`
for **that step**. A single flat `l2_norms: np.ndarray` cannot hold all three —
the arrays have different lengths and different semantics. The Phase 2 `GlobalStats`
placeholder must be replaced.

#### Correct object hierarchy

```python
# models/inputs.py  (replaces the Phase 2 GlobalStats placeholder)

@dataclass(frozen=True)
class TopsisStats:
    """Global stats needed to replay one TOPSIS step on a data partition.

    Computed from the full (city-wide) decision matrix for that step.
    Arrays have length == n_criteria for the specific TOPSIS invocation.

    l2_norms   : sqrt(sum(M[:,j]**2))  — used in Step 1 to normalise columns
    ideal_max  : col-wise max of normalised+weighted M  — used in Step 2
    ideal_min  : col-wise min of normalised+weighted M  — used in Step 2
    """
    l2_norms: np.ndarray    # shape (n_criteria,)
    ideal_max: np.ndarray   # shape (n_criteria,)  — after normalisation+weighting
    ideal_min: np.ndarray   # shape (n_criteria,)  — after normalisation+weighting

@dataclass(frozen=True)
class GlobalStats:
    """All city-wide statistics needed to run the vulnerability pipeline
    on a data partition without introducing partition-boundary bias.

    Computed once in split_inputs() on the full building dataset.
    Injected verbatim into each execute_single() work item.
    """
    sensitivity: TopsisStats       # step 1.1 — n_criteria = 2 or 3
    coping_capacity: TopsisStats   # step 1.2 — n_criteria = 2
    svi: TopsisStats               # step 1.3 — n_criteria = 2
    svi_mean: float                # step 1.4 — city-wide mean(SVI) for threshold floor
```

#### Changes to `topsis.py`

```python
def compute_topsis_stats(
    matrix: np.ndarray,
    weights: np.ndarray,
) -> TopsisStats:
    """Run normalisation (Step 1) and ideal extraction (Step 2) on the
    full-dataset matrix and return the stats without computing distances.
    """
    ...

def topsis(
    matrix: np.ndarray,
    weights: np.ndarray,
    *,
    stats: TopsisStats | None = None,
    apply_entropy: bool = True,
) -> np.ndarray:
    # When stats is None: compute norms and ideals from matrix (current behaviour,
    # unchanged for the existing scatter process).
    # When stats is provided: use injected norms and ideals instead — the partition
    # matrix is normalised with city-wide norms, distances are measured against
    # city-wide ideals.
    ...
```

#### Changes to `vulnerability.py`

```python
def flood_susceptibility_transform(
    svi: pd.Series,
    threshold_fraction: float,
    exponent: float,
    *,
    svi_mean: float | None = None,   # injected for partitioned runs
) -> pd.Series:
    # When svi_mean is None: compute from svi.dropna().mean() (current behaviour).
    # When provided: use the city-wide mean — avoids partition skew.
    ...

def compute_global_stats(
    df: pd.DataFrame,
    params: VulnerabilityParams,
) -> GlobalStats:
    """Compute all city-wide statistics required for partitioned vulnerability runs.

    Runs steps 1.1–1.3 on the full DataFrame to extract TopsisStats for each
    TOPSIS invocation, then runs step 1.4 to obtain the city-wide SVI mean.
    The full pipeline result (with SVI column) is NOT returned — call
    run_vulnerability_pipeline() separately for the actual output columns.
    """
    ...

def run_vulnerability_pipeline(
    df: pd.DataFrame,
    params: VulnerabilityParams,
    *,
    global_stats: GlobalStats | None = None,
) -> pd.DataFrame:
    # When global_stats is None: each TOPSIS step computes its own norms and
    # ideals from df (current behaviour, unchanged for the scatter process).
    # When provided: each step receives its per-step TopsisStats and svi_mean.
    ...
```

#### `classification.py` — unchanged
Head/tail breaks always run once in `merge_results` on the full merged dataset.
No global-stat injection needed.

#### Serialisation note
`GlobalStats` contains numpy arrays and must be serialised to pass through Celery.
Use `dataclasses.asdict()` + `arr.tolist()` when building the work item in
`split_inputs`, and reconstruct with `np.array(...)` at the start of `execute_single`.

### Phase 4 — new process
**New file:** `src/pluvial_flood_risk/processes/hamburg_process.py`

```python
@register_process("pluvial_flood_risk_hamburg")
class PluvialFloodRiskHamburg(BaseParallelProcess):
    process_description = ProcessDescription.from_yaml(
        "pluvial_flood_risk_hamburg_process_description.yaml"
    )
    schema_registry = _build_schema_registry()   # reuse the 7 JSON schemas

    # --- remote input resolution (Hamburg: server-side URLs, inline overrides) -----
    # Server-configured URLs (no client SSRF exposure, no allowlist needed).
    _BUILDINGS_URL: ClassVar[str] = settings.hamburg_buildings_url
    _STREETS_URL: ClassVar[str] = settings.hamburg_streets_url
    _FLOOD_LAYERS_URL: ClassVar[str] = settings.hamburg_flood_layers_url

    def resolve_remote_inputs(self, exec_body: dict) -> dict:
        inputs = dict(exec_body["inputs"])
        # Download only when the client did NOT supply inline data.
        if "buildings" not in inputs:
            inputs["buildings"] = fetch_ogc_features(self._BUILDINGS_URL)
        if "streets" not in inputs:
            inputs["streets"] = fetch_ogc_features(self._STREETS_URL)
        if "flood_layers" not in inputs:
            inputs["flood_layers"] = fetch_ogc_features(self._FLOOD_LAYERS_URL)
        return {**exec_body, "inputs": inputs}

    # --- validate the downloaded data -----------------------------------
    def late_validate(self, inputs: dict) -> bool:
        BuildingCollection.model_validate(inputs["buildings"])
        StreetCollection.model_validate(inputs["streets"])
        return True

    # --- coordinator: load once, global stats, partition + halo ----------
    def split_inputs(self, exec_body: dict) -> list[dict]:
        inp = ProcessInputs.from_exec_body(exec_body)
        blds = self._to_metric_gdf(inp.buildings)

        # global stats on the WHOLE dataset regardless of calculation_boundary
        stats = compute_global_stats(blds, inp.vulnerability_parameters)
        vuln = run_vulnerability_pipeline(blds, inp.vulnerability_parameters, global_stats=stats)

        # optional AOI clip decides which buildings get scored / returned
        scored = self._clip(vuln, inp.calculation_boundary) if inp.calculation_boundary else vuln

        streets = self._to_metric_gdf(inp.streets)
        flood = self._to_metric_gdf(inp.flood_layers)

        items: list[dict] = []
        for unit_id, unit_blds in scored.groupby(STATISTICAL_UNIT_ID):
            halo = unit_blds.buffer(30.0).union_all()        # 30 m halo
            items.append(self._make_chunk(
                unit_blds=unit_blds,
                halo_streets=streets.clip(halo),
                halo_flood=flood.clip(halo),
                stats=stats,
                params=inp,
            ))
        return items

    # --- worker: one partition -----------------------------------------
    def execute_single(self, item: dict, job_progress_callback=None) -> ChunkResult:
        blds = ...      # rehydrate partition GDF (already has vulnerability columns)
        df = run_exposure_pipeline(blds)
        df = run_hazard_wb_pipeline(df, halo_flood, params)
        df = run_hazard_ma_pipeline(df, halo_streets, halo_flood, params)
        df = run_risk_pipeline(df, params.risk_weights)
        return ChunkResult(
            risk_features=df.to_geojson_dict(),    # partition rows only (halo discarded)
            risk_wb=df[PFR_WB].tolist(),
            risk_ma=df[PFR_MA].tolist(),
        )

    # --- coordinator: merge + GLOBAL classification --------------------
    def merge_results(self, results: list[dict]) -> "PfRiskHamburgResult":
        feats = [f for r in results for f in r["risk_features"]["features"]]
        wb = pd.Series([v for r in results for v in r["risk_wb"]])
        ma = pd.Series([v for r in results for v in r["risk_ma"]])
        return PfRiskHamburgResult(
            risk_buildings={"type": "FeatureCollection", "features": feats},
            classification_breaks_wb=run_classification_pipeline(wb, n_iter),
            classification_breaks_ma=run_classification_pipeline(ma, n_iter),
        )
```

`ChunkResult(BaseModel)` is a plain partial result (unchanged contract).

### Phase 5 — process description + registration ✅
**New file:** `pluvial_flood_risk_hamburg_process_description.yaml`

**`pluvial_flood_risk_process_description.yaml` (existing process — evolved):**
- `buildings`, `streets`, `flood_layers` each gain a second `oneOf` branch: the
  existing inline GeoJSON branch plus an OGC "input by reference" branch:
  ```yaml
  - type: object
    required: [href, type]
    properties:
      href:
        type: string
        format: uri
        description: >-
          OGC API Features items endpoint URL. The server paginates through
          all pages and downloads the complete collection. Must be an items
          endpoint (e.g. .../collections/{id}/items). GeoJSON and FlatGeobuf
          responses are both accepted; the server decodes FlatGeobuf
          transparently.
      type:
        type: string
        enum:
          - application/geo+json
          - application/flatgeobuf
  ```
  `validate_inputs` validates this reference object; `resolve_remote_inputs`
  performs the download; `late_validate` validates the resolved FeatureCollection.

**`pluvial_flood_risk_hamburg_process_description.yaml` (new process):**
- `buildings`, `streets`, `flood_layers` are **optional** (`minOccurs: 0`),
  accepting only inline GeoJSON. Absence means "use the server-configured source".
- Add optional `calculation_boundary` input.
- Remove the reference-object branch entirely — no user-controlled URL.
- Declare the `risk_buildings` output schema as a `oneOf` advertising both formats:

```yaml
risk_buildings:
  title: Risk buildings
  schema:
    oneOf:
      - type: object
        format: geojson-feature-collection      # -> application/geo+json
      - type: string
        contentMediaType: application/flatgeobuf # -> binary
        contentEncoding: binary
```

Register the new process so it loads alongside the existing one.

### Phase 6 — structured / binary output
**In** `hamburg_process.py`:

```python
from fastprocesses.core.output_protocol import BaseProcessResult

class PfRiskHamburgResult(BaseProcessResult):
    risk_buildings: dict                  # canonical in-memory GeoJSON
    classification_breaks_wb: list[float]
    classification_breaks_ma: list[float]

    output_serializers: ClassVar = {
        "risk_buildings": {
            "application/geo+json":   "risk_buildings_geojson",
            "application/flatgeobuf": "risk_buildings_fgb",
        },
        "classification_breaks_wb": {"application/json": "_breaks_wb_json"},
        "classification_breaks_ma": {"application/json": "_breaks_ma_json"},
    }

    def risk_buildings_geojson(self) -> bytes:
        return json.dumps(self.risk_buildings).encode()

    def risk_buildings_fgb(self) -> bytes:
        gdf = gpd.GeoDataFrame.from_features(
            self.risk_buildings["features"], crs="EPSG:4326"
        )
        buf = io.BytesIO()
        gdf.to_file(buf, driver="FlatGeobuf")
        return buf.getvalue()

    def _breaks_wb_json(self) -> bytes:
        return json.dumps(self.classification_breaks_wb).encode()

    def _breaks_ma_json(self) -> bytes:
        return json.dumps(self.classification_breaks_ma).encode()
```

Clients request compact binary with `response=raw` + a single output
`{"risk_buildings": {"format": {"mediaType": "application/flatgeobuf"}}}`.

### Phase 7 — tests
1. `fetch_ogc_features` against a mock 3-page OGC Features server → asserts all features
   accumulated; SSRF block on a disallowed origin.
2. `compute_topsis_stats` + `topsis(stats=...)` numerically equals single-shot `topsis()`
   on the same matrix (global-stat equivalence).
3. `split_inputs` → `execute_single` → `merge_results` serial fallback (no broker) on
   `data/test/` fixtures equals the existing scatter-process output within tolerance
   (partition-invariance proof).
4. `calculation_boundary` clips output buildings, but global breaks are still computed
   over the full dataset.
5. `late_validate` rejects a malformed downloaded FeatureCollection.
6. **Output:** `serialize_result(PfRiskHamburgResult(...), {"risk_buildings":
   {"format": {"mediaType": "application/flatgeobuf"}}}, "raw", desc)` returns a
   `Response` whose body is valid FlatGeobuf (re-readable by `geopandas`), and the
   `application/geo+json` path returns valid GeoJSON.
7. **Input format parity:** `PluvialFloodRiskProcess` accepts buildings/streets/
   flood_layers as inline GeoJSON or as a `{href, type}` reference (GeoJSON or
   FlatGeobuf download). `PluvialFloodRiskHamburg` accepts inline GeoJSON override
   or uses server-configured URLs. Both yield equivalent results within tolerance
   on the same fixture data.
8. **`resolve_remote_inputs` / `late_validate` pipeline:** test that when a
   `{href, type}` reference is supplied to `PluvialFloodRiskProcess`, `validate_inputs`
   passes on the reference object, `resolve_remote_inputs` replaces it with the
   downloaded FeatureCollection, and `late_validate` catches a malformed response.


### phase 8 - Add new outputs

Dropped / incomplete input data/features:
- buildings
- statistical units

Notes about self-intersecting/ overwise invalid geometries

### phase 9 - store validated data optionally in tmp path

- within k8s: data store within a PVC would allow for a faster pipeline (a cache)
- cache could be invalidated when older than x days or weak, data willbe redownloaded then

---

## 6. New / changed files

| File | Change |
|------|--------|
| `src/pluvial_flood_risk/io/remote.py` | **NEW** — OGC API Features paged fetch; FlatGeobuf decode; SSRF allowlist |
| `src/pluvial_flood_risk/models/inputs.py` | add `calculation_boundary`, `GlobalStats`; inline inputs GeoJSON-only |
| `src/pluvial_flood_risk/algorithms/topsis.py` | `compute_topsis_stats`; `topsis(stats=…)` |
| `src/pluvial_flood_risk/algorithms/vulnerability.py` | inject `svi_mean` / `global_stats` |
| `src/pluvial_flood_risk/algorithms/classification.py` | unchanged (runs in merge) |
| `src/pluvial_flood_risk/processes/hamburg_process.py` | **NEW** — `PluvialFloodRiskHamburg` + `PfRiskHamburgResult`; server-side URL download in `resolve_remote_inputs` |
| `src/pluvial_flood_risk/processes/flood_risk_process.py` | add `resolve_remote_inputs` + `late_validate` overrides for user-supplied `{href, type}` reference inputs |
| `pluvial_flood_risk_hamburg_process_description.yaml` | **NEW** — optional inline overrides, `calculation_boundary`, dual-format output; no reference-object branch |
| `pluvial_flood_risk_process_description.yaml` | add `{href, type}` reference branch to `buildings`, `streets`, `flood_layers`; extend `type` enum to include `application/flatgeobuf` |
| `tests/` | new tests for Phases 1–8 |

---

## 7. Locked decisions

- **Output transport:** `BaseProcessResult` + `output_serializers` (GeoJSON + FlatGeobuf).
  Object-storage/href streaming is a possible later follow-up, not in scope.
- **Partition unit:** statistical unit.
- **Halo radius:** 30 m (max MA Zone-3 ring; covers 2 m WB buffer).
- **SSRF allowlist:** env-based (`FP_ALLOWED_INPUT_ORIGINS`); applies to `PluvialFloodRiskProcess` only.
- **Downloaded datasets:** buildings, streets, flood_layers. Statistical units and
  all parameter objects remain client-provided inline in both processes.
- **FlatGeobuf input:** download path only (server detects response `Content-Type` and
  decodes transparently). No inline base64 FlatGeobuf input.
- **`PluvialFloodRiskProcess`** — user supplies `{href, type}` reference object;
  `type` enum: `application/geo+json` | `application/flatgeobuf`; `href` must be
  an OGC API Features items endpoint.
- **`PluvialFloodRiskHamburg`** — source URLs configured server-side (env); inline
  GeoJSON overrides each source independently; no user URL input.
