# Refactoring Plan: `PluvialFloodRiskHamburg` — data fan-out, URL download, structured/binary output

**Status:** execution-ready
**Target framework:** `fastprocesses` 0.22.0
**Date:** 2026-06-22

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

### Phase 1 — URL download + OGC API Features paging
**New file:** `src/pluvial_flood_risk/io/remote.py`

Core download function used by **both** processes via `resolve_remote_inputs`:

```python
def fetch_ogc_features(
    url: str,
    *,
    allowed_origins: list[str] | None = None,   # None = no SSRF check (server-side only)
    page_limit: int = 1000,
    timeout: float = 30.0,
) -> dict:
    """Fetch a complete GeoJSON FeatureCollection from an OGC API Features
    items endpoint, following links[rel=next] until exhausted.

    The URL must be an OGC API Features items endpoint
    (e.g. .../collections/{id}/items). Accepts both application/geo+json and
    application/flatgeobuf responses; FlatGeobuf is decoded to a GeoJSON dict.
    SSRF-checks every hop when allowed_origins is supplied.
    """
    if allowed_origins is not None:
        _assert_allowed(url, allowed_origins)
    features: list[dict] = []
    next_url: str | None = _with_limit(url, page_limit)
    while next_url:
        if allowed_origins is not None:
            _assert_allowed(next_url, allowed_origins)
        fc = _fetch_page(next_url, timeout)   # dispatches on Content-Type
        features.extend(fc.get("features", []))
        next_url = _next_link(fc)
    return {"type": "FeatureCollection", "features": features}


def _assert_allowed(url: str, allowed_origins: list[str]) -> None:
    """Raise SSRFBlockedError if the URL origin is not in the allowlist."""
    ...

def _fetch_page(url: str, timeout: float) -> dict:
    """GET the URL; decode FlatGeobuf to GeoJSON dict or return JSON directly."""
    ...

def decode_flatgeobuf_bytes(data: bytes) -> dict:
    """Decode FlatGeobuf bytes into a GeoJSON FeatureCollection dict."""
    ...

def _next_link(fc: dict) -> str | None: ...
def _with_limit(url: str, limit: int) -> str: ...
```

SSRF allowlist for `PluvialFloodRiskProcess` is read from env `FP_ALLOWED_INPUT_ORIGINS`.
`PluvialFloodRiskHamburg` calls `fetch_ogc_features` with `allowed_origins=None` because
the URL originates from server-side configuration, not from the client.

### Phase 2 — input model
**Edit:** `src/pluvial_flood_risk/models/inputs.py`

- Inline `buildings` / `streets` / `flood_layers` accept **GeoJSON FeatureCollection
  objects only**. FlatGeobuf is supported exclusively via URL download — there is
  no inline base64 FlatGeobuf path.
- Add `calculation_boundary: dict | None` (single Polygon/MultiPolygon GeoJSON feature,
  Hamburg process only, `minOccurs: 0`).
- Add `GlobalStats` model carrying TOPSIS per-criterion L2 norms + `ideal_max` /
  `ideal_min` and `svi_mean`, to be injected into each partition.

**Validation pipeline** (fastprocesses hook order, relevant for URL inputs):
1. `validate_inputs` — runs on the raw wire value. When the client passes a
   `{href, type}` reference object, this validates the *object structure*, not the
   downloaded data.
2. `resolve_remote_inputs` — detects reference objects, downloads and decodes the
   data (GeoJSON or FlatGeobuf → GeoJSON dict), and substitutes the resolved value
   back into `exec_body["inputs"]`. SSRF check happens here.
3. `late_validate` — receives resolved inputs (actual GeoJSON FeatureCollections)
   and validates them against Pydantic models. This is the only place where
   downloaded data is structurally validated.

### Phase 3 — refactor global-stat functions (back-compatible)
**Edit:** `topsis.py`, `vulnerability.py`

```python
# topsis.py
@dataclass(frozen=True)
class TopsisStats:
    l2_norms: np.ndarray      # per criterion
    ideal_max: np.ndarray
    ideal_min: np.ndarray

def compute_topsis_stats(matrix: np.ndarray, weights: np.ndarray) -> TopsisStats: ...

def topsis(matrix, weights, *, stats: TopsisStats | None = None, apply_entropy=True):
    # stats is None -> compute from matrix (current behaviour, unchanged for old process)

# vulnerability.py
def flood_susceptibility_transform(svi, threshold_fraction, exponent,
                                   *, svi_mean: float | None = None):
    # svi_mean injected for partitioned runs; falls back to valid.mean() when None

def run_vulnerability_pipeline(df, params, *, global_stats: GlobalStats | None = None): ...
```

`classification.py` is **unchanged** — head/tail breaks run once in `merge_results`.

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

### Phase 5 — process description + registration
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
