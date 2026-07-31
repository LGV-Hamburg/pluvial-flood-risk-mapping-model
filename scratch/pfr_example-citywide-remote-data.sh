#!/bin/bash

set -euo pipefail

DATA_DIR="/home/python-dev/code/python/flood-risk-mapping-model/data/hamburg"

# 2x2km
# bbox=9.9678%2C53.5421%2C10.0196%2C53.5601
# 5x5km
# bbox=9.9284%2C53.5286%2C10.0590%2C53.5736
# 10x10km
# bbox=9.8938%2C53.5050%2C10.0928%2C53.5960
# 15x15km
# bbox=9.8802%2C53.4836%2C10.1072%2C53.6186
# 20x20km
# bbox=9.8423%2C53.4611%2C10.1451%2C53.6411

API_URL="${API_URL:-http://localhost:8000/processes/pluvial-flood-risk-citywide/execution}"
# API_URL="${API_URL:-https://ump-prod.germanywestcentral.cloudapp.azure.com/v1.0/processes/fair2adapt:pluvial-flood-risk-citywide/execution}"

# OGC API Features items endpoints for the three remote inputs.
# The /items suffix is required by the process (normative OGC API Features path).
# streets: nutzung=Fahrbahn filters to road surfaces only; f=json requests GeoJSON.
BUILDINGS_URL="https://geodata.germanywestcentral.cloudapp.azure.com/buildings_footprint/collections/buildings/items?f=json&filter=floors_above_ground%20>%200&bbox=9.8802%2C53.4836%2C10.1072%2C53.6187&crs=http://www.opengis.net/def/crs/EPSG/0/25832"
STREETS_URL="https://api.hamburg.de/datasets/v1/feinkartierung_strasse/collections/strassenflaechen/items?f=json&nutzung=Fahrbahn&bbox=9.8802%2C53.4836%2C10.1072%2C53.6187&crs=http://www.opengis.net/def/crs/EPSG/0/25832"
FLOOD_LAYERS_URL="https://geodata.germanywestcentral.cloudapp.azure.com/flood_levels/collections/sri7/items?f=json&bbox=9.8802%2C53.4836%2C10.1072%2C53.6187&crs=http://www.opengis.net/def/crs/EPSG/0/25832"

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
                hazard_parameters: {
                    buffer_building_m: 2,
                    buffer_zone1_m: 5,
                    buffer_zone2_m: 15,
                    buffer_zone3_m: 30,
                    lognormal_shape: 0.25,
                    ma_flood_level_cm: 30,
                    min_street_flood_area_m2: 4
                },
                output_settings: {
                    decimal_precision: 3,
                    include_input_building_properties: true
                },
                risk_weights: {
                    exposure_exponent: 1,
                    hazard_exponent: 1,
                    vulnerability_exponent: 1
                },
                vulnerability_parameters: {
                    sensitivity_weights: {
                        children_under_10: 0.5,
                        elderly_singles_over_65: 0.5
                    }
                },
            },
            outputs: {
                voronoi_diagram: {
                    format: {
                        mediaType: "application/flatgeobuf"
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
