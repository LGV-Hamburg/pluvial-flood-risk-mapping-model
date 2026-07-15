#!/bin/bash

set -euo pipefail

DATA_DIR="/home/python-dev/code/python/flood-risk-mapping-model/data/hamburg"

# API_URL="${API_URL:-http://localhost:8000/processes/pluvial-flood-risk-citywide/execution}"
API_URL="${API_URL:-https://ump-prod.germanywestcentral.cloudapp.azure.com/processes/fair2adapt:pluvial-flood-risk-citywide/execution}"

# OGC API Features items endpoints for the three remote inputs.
# The /items suffix is required by the process (normative OGC API Features path).
# streets: nutzung=Fahrbahn filters to road surfaces only; f=json requests GeoJSON.
BUILDINGS_URL="https://geodata.germanywestcentral.cloudapp.azure.com/buildings_footprint/collections/buildings/items?f=json&filter=floors_above_ground%20>%200&bbox=9.9763%2C53.5624%2C10.0010%2C53.5782"
STREETS_URL="https://api.hamburg.de/datasets/v1/feinkartierung_strasse/collections/strassenflaechen/items?f=json&nutzung=Fahrbahn&bbox=9.9763%2C53.5624%2C10.0010%2C53.5782"
FLOOD_LAYERS_URL="https://geodata.germanywestcentral.cloudapp.azure.com/flood_levels/collections/sri7/items?f=json&bbox=9.9763%2C53.5624%2C10.0010%2C53.5782"

if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required but not installed." >&2
    exit 1
fi

generate_payload() {
    jq -n \
        --slurpfile statistical_units "${DATA_DIR}/statistische-gebiete.geojson" \
        --arg buildings_url "$BUILDINGS_URL" \
        --arg streets_url "$STREETS_URL" \
        --arg flood_layers_url "$FLOOD_LAYERS_URL" \
        '{
            inputs: {
                buildings: {
                    href: $buildings_url,
                    type: "application/geo+json"
                },
                streets: {
                    href: $streets_url,
                    type: "application/geo+json"
                },
                flood_layers: {
                    href: $flood_layers_url,
                    type: "application/geo+json"
                },
                statistical_units: $statistical_units[0],
                target_crs: "http://www.opengis.net/def/crs/EPSG/0/25832",
                vulnerability_parameters: {
                    sensitivity_weights: {
                        children_under_10: 0.501,
                        elderly_singles_over_65: 0.499
                    }
                },
            },
            outputs: {
                voronoi_diagram: {
                    format: {
                        mediaType: "application/geo+json",
                        schema: "https://geojson.org/schema/FeatureCollection.json"
                    },
                    transmissionMode: "reference"
                },
                classification_breaks_wb: {
                    transmissionMode: "value"
                },
                classification_breaks_ma: {
                    transmissionMode: "value"
                },
            },
            "response": "document"
        }'
}

if [[ "${1:-}" == "--print" ]]; then
    generate_payload
    exit 0
fi

generate_payload | curl -X POST -v \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "$API_URL"
