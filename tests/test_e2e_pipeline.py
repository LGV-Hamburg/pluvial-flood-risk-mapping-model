"""End-to-end tests that execute the full API + worker + Redis pipeline.

These tests are opt-in by design. They build the execution payload from example
GeoJSON files and post it to the running OGC API process endpoint.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "test"
DEFAULT_API_URL = "http://localhost:8000/processes/pluvial_flood_risk/execution"


def _load_json_from_url(url: str, timeout: int = 10) -> dict:
    with urlopen(url, timeout=timeout) as response:  # nosec B310
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _load_example_geojson(file_name: str) -> dict:
    file_path = DATA_DIR / file_name
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_payload() -> dict:
    return {
        "inputs": {
            "buildings": _load_example_geojson("buildings.geojson"),
            "streets": _load_example_geojson("streets.geojson"),
            "flood_layers": _load_example_geojson("flood_levels.geojson"),
        }
    }


def _post_execution(payload: dict, api_url: str, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _extract_outputs(payload: dict) -> dict | None:
    if isinstance(payload.get("outputs"), dict):
        return payload["outputs"]
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    if isinstance(payload.get("risk_features"), dict):
        return payload
    return None


def _result_url_from_job(payload: dict, api_url: str) -> str | None:
    links = payload.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            rel = link.get("rel")
            href = link.get("href")
            if rel in {"results", "result"} and isinstance(href, str):
                return href

    job_id = payload.get("jobID") or payload.get("job_id") or payload.get("id")
    if not isinstance(job_id, str):
        return None

    # OGC API Processes default pattern.
    base = api_url.split("/processes/")[0]
    return f"{base}/jobs/{job_id}/results"


def _wait_for_outputs(initial_payload: dict, api_url: str) -> dict:
    outputs = _extract_outputs(initial_payload)
    if outputs is not None:
        return outputs

    result_url = _result_url_from_job(initial_payload, api_url)
    if result_url is None:
        raise AssertionError(
            "No inline outputs and no resolvable result URL/job id in API response."
        )

    timeout_s = int(os.getenv("E2E_TIMEOUT_SECONDS", "120"))
    poll_interval_s = float(os.getenv("E2E_POLL_INTERVAL_SECONDS", "2"))
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        payload = _load_json_from_url(result_url)
        outputs = _extract_outputs(payload)
        if outputs is not None:
            return outputs

        status = str(payload.get("status", "")).lower()
        if status in {"failed", "dismissed", "error"}:
            raise AssertionError(f"E2E job failed with status={status!r}: {payload}")

        time.sleep(poll_interval_s)

    raise AssertionError(
        f"Timed out waiting for e2e results at {result_url!r} after {timeout_s}s"
    )


@pytest.fixture(scope="module")
def e2e_enabled() -> None:
    if os.getenv("RUN_E2E") != "1":
        pytest.skip("Set RUN_E2E=1 to enable e2e tests.")


@pytest.mark.e2e
def test_e2e_generates_valid_payload() -> None:
    payload = _build_payload()
    assert "inputs" in payload
    assert set(payload["inputs"].keys()) == {"buildings", "flood_layers", "streets"}


@pytest.mark.e2e
@pytest.mark.integration
def test_e2e_full_pipeline(e2e_enabled: None) -> None:
    api_url = os.getenv("API_URL", DEFAULT_API_URL)

    # Fast preflight so failures are actionable before we execute the script.
    try:
        _load_json_from_url("http://localhost:8000/openapi.json", timeout=5)
    except (URLError, json.JSONDecodeError):
        pytest.skip(
            "API is not reachable at http://localhost:8000. Start API and worker."
        )

    response_payload = _post_execution(_build_payload(), api_url)
    outputs = _wait_for_outputs(response_payload, api_url)

    # Accept both direct outputs and flat payload shape.
    risk_features = outputs.get("risk_features") if isinstance(outputs, dict) else None
    if risk_features is None and isinstance(response_payload, dict):
        risk_features = response_payload.get("risk_features")

    assert isinstance(risk_features, dict)
    assert risk_features.get("type") == "FeatureCollection"
    assert isinstance(risk_features.get("features"), list)
    assert len(risk_features["features"]) > 0
