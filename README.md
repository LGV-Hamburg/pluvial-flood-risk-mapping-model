# Urban Pluvial Flood Risk Assessment 



---

## Prerequisites

Depending on the package manager chosen when the project was scaffolded, install
the corresponding tool **once** on your machine before proceeding.

| Package manager | Install |
|---|---|
| **pixi** *(default)* | `curl -fsSL https://pixi.sh/install.sh \| bash` — [pixi.sh](https://pixi.sh) |
| **poetry** | `pipx install poetry` — [python-poetry.org](https://python-poetry.org) |
| **mamba** | [Miniforge](https://github.com/conda-forge/miniforge) |

For Docker workflows you also need [Docker](https://docs.docker.com/get-docker/).

---

## Development setup

### pixi

Pixi manages Python, all conda-forge and PyPI dependencies, and the virtual environment in a
single tool. The environment lives at `.pixi/envs/` and is **not** committed to git.

```bash
# Install all dependencies (creates the environment from pixi.lock)
pixi install

# Open a shell with dev tools available
pixi shell -e dev

# Or run a single task without activating the shell
pixi run -e dev test
pixi run -e dev lint
pixi run -e dev format
pixi run -e dev typecheck
```

Add a new dependency:

```bash
pixi add numpy                     # conda-forge, runtime
pixi add --pypi requests           # PyPI, runtime
pixi add --feature dev pytest-mock # dev-only
```

Update all dependencies and regenerate the lockfile:

```bash
pixi update
git add pixi.lock && git commit -m 'chore: update pixi lockfile'
```

---

### poetry

Poetry manages PyPI dependencies and creates `.venv/` inside the project root
(configured in `poetry.toml`). Python itself comes from your system, `pyenv`, or a
conda base environment — it must **not** be installed inside the project venv.

```bash
# One-time: install poetry globally, isolated from any project
pipx install poetry

# Inside the project folder — creates .venv/ and installs all deps
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

---

### mamba / conda

Mamba manages both the virtual environment and the packages.

```bash
# Create a project-local environment
mamba env create -f environment.yaml -p .venv
mamba activate .venv/

# Install dev dependencies on top
mamba env update --prefix .venv --file environment.dev.yaml

# Recreate / update after environment files change
mamba env update --prefix .venv --file environment.yaml --prune
```

Add a new dependency: edit `environment.yaml`, then run `mamba env update --prune`.

---

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
└── secrets/                                 # local credentials — never committed
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

