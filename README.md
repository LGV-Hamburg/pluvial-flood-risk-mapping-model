# Urban Pluvial Flood Risk Assessment

`pluvial-flood-risk` is a **production-ready Python library and OGC API Processes service** for
building-level urban pluvial flood risk assessment. It implements the IPCC risk framework from
[Vogelbacher et al. (2026, preprint)](https://doi.org/10.5194/egusphere-2025-6362) as a composable,
fully typed, interoperable open-source package.

The package is designed to be **imported and extended by other projects** (`src/pluvial_flood_risk`)
and to be **deployed as a standards-compliant HTTP service** that any OGC-capable client or
geospatial data platform can consume.

---

## What this library does

It calculates per-building pluvial flood risk indices following the multiplicative IPCC formula:

$$\text{Risk} = \text{Hazard}^a \times \text{Exposure}^b \times \text{Vulnerability}^c$$

The four sub-pipelines are:

| Step | Component | Method |
|---|---|---|
| 1 | **Vulnerability** | TOPSIS - Sensitivity, Coping Capacity, Social Vulnerability Index |
| 2 | **Exposure** | Resident count joined from statistical units |
| 3 | **Hazard (Mobility & Accessibility, Well-Being)** | Building-flood polygon intersection + lognormal depth-damage |
| 3 | **Hazard (Mobility & Accessibility, HMA)** | Street-buffer flood saturation ratio |
| 4 | **Risk** | Multiplicative combination of components; optional HEALPix spatial rollup |

Results are returned as a GeoJSON `FeatureCollection` of enriched building features, with all intermediate index scores attached as properties.

---

## Features

- **OGC API Processes (WPS 2.0) service** - standard `POST /processes/{id}/execution` endpoint; synchronous and asynchronous execution; job management via job IDs
- **Two execution strategies** - *scatter-gather* (parallel sub-pipelines per area) and *city-wide fan-out* (parallel execution per statistical unit); choose based on workload size
- **OGC API Features input** - remote buildings, streets and flood layers fetched directly from OGC-compliant data servers with CQL2 filtering, bbox clipping and transparent FlatGeoBuf decoding
- **Strict multi-layer input validation** - JSON Schema (Draft 2020-12) + Pydantic v2 type constraints; cross-field weight-sum validation; optional lenient mode that drops invalid features instead of aborting
- **Partition-safe TOPSIS** - city-wide normalisation statistics are computed once and broadcast to all workers, preventing partition-boundary bias in vulnerability scoring
- **Fully typed public API** - all inputs and parameters are Pydantic `BaseModel` instances with paper-derived defaults (Vogelbacher et al., 2025); `py.typed` marker for IDE and mypy support
- **Configurable pipeline parameters** - TOPSIS weights, buffer distances, flood depth thresholds, lognormal shape parameter, output precision - all overridable per request
- **Horizontally scalable** - Celery worker pool + Redis broker; add workers to increase calculation speed for large compuation areas (whole cities) without code changes
- **Environment-variable configuration** - origin allowlists, page limits, cache directory, chunk size; safe defaults for production with opt-in overrides for development

---

## Design decisions

**Reproducibility** - The full algorithm is implemented in pure Python with no proprietary dependencies. Every parameter carries its source citation and paper-derived default, making results independently verifiable and replicable outside of any specific tooling environment.

**Interoperability** - The pipeline is exposed as an OGC API Processes service with standard endpoints following REST principals. Any OGC-capable client, geospatial data platform or web frontend can invoke the calculation without understanding the internals.

**Live data integration** - Input layers can be fetched directly from OGC API Features servers at request time (with CQL2 filtering, bbox clipping and FlatGeoBuf decoding), decoupling the calculation from static file exports and enabling always-current results.

**Correctness under parallelism** - City-wide TOPSIS normalisation statistics are computed once over the complete dataset and broadcast as an immutable snapshot to all workers. This ensures consistent vulnerability scoring regardless of how the work is partitioned across machines.

**Explicit input contracts** - All inputs and parameters are validated through two complementary layers: JSON Schema (Draft 2020-12) for structural GeoJSON correctness, and Pydantic v2 models for value-level constraints (e.g. weight sums, positive integers, valid ranges). Validation errors surface immediately with precise messages rather than propagating silently.

**Horizontal scalability** - The pipeline is split into independent Celery tasks connected via a Redis broker. Adding workers increases throughput linearly - city-wide datasets are partitioned by statistical unit and processed concurrently without any change to the calling interface.

**Importable library design** - The package follows the `src/` layout and ships a `py.typed` marker, making every algorithm, parameter model and utility directly importable by other Python projects. Downstream tools can compose sub-pipelines, swap individual steps, or reuse the Pydantic models as a typed configuration layer.

**Unobserved operation** — The package is built for automated, unattended execution. Every
module emits structured log records via Python's standard `logging` hierarchy
(`logger = logging.getLogger(__name__)`), so operators can route output to any handler —
console, file, log aggregator — at whatever verbosity the deployment requires. Validation
warnings (e.g. dropped invalid features), remote fetch progress, partition boundaries and
TOPSIS statistics are all observable without touching source code. No warnings are suppressed.
This makes the service suitable for running inside CI pipelines, scheduled jobs or production
infrastructure where no human watches the terminal.

**Fully tested** - The test suite covers the algorithm at three levels: unit tests for each
pipeline step in isolation (`test_pluvial_flood_risk.py`), regression tests against the original synthetic Hamburg example dataset (`test_example_data.py`), and end-to-end tests against the running OGC API service (`test_e2e_pipeline.py`). The regression tests load the same GeoJSON fixtures used by the reference toolbox and verify ordering, range and rank-correlation properties - including documented, deliberate deviations from the ArcPy baseline (e.g. geodetically correct EPSG:25832 area calculations instead of the Web Mercator approximations in the original). All tests run without Redis using `pytest -m regression`; the full E2E suite activates via `RUN_E2E=1`.

---

## Prerequisites

Depending on the package manager chosen when the project was scaffolded, install the corresponding tool **once** on your machine before proceeding.

| Package manager | Install |
|---|---|
| **poetry** | `pipx install poetry` - [python-poetry.org](https://python-poetry.org) |
| **conda / mamba** | [Miniforge](https://github.com/conda-forge/miniforge) |

For Docker workflows you also need [Docker](https://docs.docker.com/get-docker/).


Copy dotenv example file:

```bash
cp .env.example .env
```

---

## Development setup

### poetry

Create a virtual environment with your preferred venv tool. Use the [environment.yml](environment.yml) file coming with this project if you use conda / mamba based venvs.

Poetry manages PyPI dependencies and creates `.venv/` inside the project root
(configured in `poetry.toml`). Python itself comes from your system, `pyenv`, or a
conda base environment - it must **not** be installed inside the project venv.

```bash
# One-time: install poetry globally, isolated from any project
pipx install poetry

# Inside the project folder - creates .venv/ and installs all deps
poetry install

# Activate the venv for an interactive session
poetry shell

# Or prefix individual commands
poetry run pytest tests/
```

Add a new dependency:

```bash
poetry add numpy                      # runtime → updates pyproject.toml + poetry.lock
poetry add --group dev pytest-mock    # dev-only
```

Update dependencies:

```bash
poetry update            # update all within declared constraints
poetry update numpy      # update a single package
git add poetry.lock && git commit -m 'chore: update poetry lockfile'
```

## Common development tasks

All tasks are available via `make`. Run `make` with no arguments to execute
`lock → build-image → run-local`.

| Command | What it does |
|---|---|
| `make test` | Run the test suite |
| `make lint` | Lint with ruff |
| `make format` | Auto-format with ruff |
| `make typecheck` | Type-check with mypy |
| `make lock` | Update the dependency lockfile |
| `make build-image` | Build the Docker image |
| `make push-registry` | Push the image to the container registry |
| `make run-local` | Start the app locally via docker-compose |
| `make build-docs` | Build the Jupyter Book documentation |
| `make clean-docs` | Remove the documentation build output |

---

## Pre-commit hooks

Code quality checks run automatically on every `git commit`. Install the hooks once after cloning:

```bash
pre-commit install
```

Run all hooks manually at any time:

```bash
pre-commit run --all-files
```

The hooks cover: **ruff** (lint + auto-fix), **ruff-format**, **mypy**, and standard
file hygiene (trailing whitespace, YAML validity, merge-conflict markers, large files).

---

## Docker

Copy `.env.example` to `.env` and fill in the values (copier does this automatically on
first scaffold):

```bash
cp .env.example .env
```

| Command | What it does |
|---|---|
| `make build-image` | Builds `${CONTAINER_REGISTRY}/analytics/${IMAGE_NAME}:${IMAGE_TAG}` |
| `make run-local` | Runs the image via `docker-compose-dev.yaml` |
| `make push-registry` | Pushes to the container registry |

Key Docker files:

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build (adapts to the chosen package manager) |
| `docker-compose-build.yaml` | CI/CD image build |
| `docker-compose-dev.yaml` | Local dev/test run with secrets and env vars |
| `docker-compose.yaml` | Production deployment spec |

---

## Project structure

```
├── src/{{ python_package_import_name }}/   # importable package
├── tests/                                   # pytest test suite
├── docs/                                    # Jupyter Book documentation
{% if is_datascience_project -%}
├── data/
│   ├── raw/                                 # original, immutable source data
│   ├── interim/                             # intermediate transformed data
│   ├── processed/                           # final, analysis-ready data
│   └── external/                            # data from third-party sources
{% endif -%}
├── assets/                                  # images, figures
├── reports/                                 # generated reports and outputs
├── examples/                                # usage examples
├── scratch/                                 # throwaway experiments (not tested)
└── secrets/                                 # local credentials - never committed
```

---

## VS Code setup

Recommended extensions (install via the Extensions panel or `.vscode/extensions.json`):

| Extension | Purpose |
|---|---|
| `ms-python.python` + `ms-python.pylance` | Python language support |
| `charliermarsh.ruff` | Linting and formatting (replaces black + isort) |
| `ms-python.mypy-type-checker` | Type checking |

Formatting on save and import sorting are pre-configured in `.vscode/settings.json`.

---

## Updating the template

To pull in improvements from the project template into this project:

```bash
copier update . --trust
```

Copier merges template changes with your local edits. Review the diff carefully before committing.

