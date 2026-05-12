#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DATA_DIR="${SCRIPT_DIR}/data/processed"
API_URL="${API_URL:-http://localhost:8000/processes/pluvial_flood_risk/execution}"

# API_URL="${API_URL:-https://ump-prod.germanywestcentral.cloudapp.azure.com/processes/flood-risk:pluvial_flood_risk/execution}"

if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required but not installed." >&2
    exit 1
fi

generate_payload() {
    jq -n \
        --slurpfile buildings "${DATA_DIR}/sozialdaten_buildings_bbox.geojson" \
        --slurpfile streets "${DATA_DIR}/strassenflaechen_bbox.geojson" \
        --slurpfile flood_layers "${DATA_DIR}/flood_levels_sri7_bbox.geojson" \
        '{
            inputs: {
                buildings: $buildings[0],
                streets: $streets[0],
                flood_layers: $flood_layers[0],
                params: {target_crs: "EPSG:25832"}
            }
        }'
}

if [[ "${1:-}" == "--print" ]]; then
    generate_payload
    exit 0
fi

generate_payload | curl -X POST \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "$API_URL"
