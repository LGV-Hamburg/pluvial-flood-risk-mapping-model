# Methoden-Überblick (Vogelbacher et al., 2025)

Das Paper implementiert das **IPCC-Risikokonzept** auf Gebäudeebene für urbane Sturzfluten:

> **Risiko = f(Gefährdung, Exposition, Vulnerabilität)**

Es werden zwei parallele Risikoindizes berechnet:
- **PFR_WB** – Pluvial Flood Risk to Well-Being (Gesundheit)
- **PFR_MA** – Pluvial Flood Risk to Mobility & Accessibility

---

## TOPSIS *(Technique for Order of Preference by Similarity to Ideal Solution)*

**Zweck:** Aggregation mehrerer sozioökonomischer Indikatoren (unterschiedliche Einheiten, verschiedene Gewichte) zu einem einzigen Index.

**Mechanismus:**
1. Normierung der Attributmatrix (Eq. 4): $z_{ij} = x_{ij} / \sqrt{\sum_i x_{ij}^2}$
2. Gewichtete Normwerte je Attribut
3. Bestimmung der positiven Ideallösung A⁺ (Minimum je Attribut) und negativen Ideallösung A⁻ (Maximum je Attribut)
4. Euklidische Abstände D⁺, D⁻ zu beiden Idealen (Eq. 5)
5. Closeness-Koeffizient: $C_i = D^- / (D^+ + D^-)$ ∈ [0, 1]

Im Paper eingesetzt für: Sensitivity (S), Coping Capacity (CC), SVI.

**Standardgewichte im Paper:**
- S: C = 0.3, ES = 0.7
- CC: WR = 0.5, EDQ = 0.5
- SVI: S = 0.5, CC = 0.5

---

## Shannon Entropy

**Zweck:** Berücksichtigung von Heterogenität der Indikatorausprägungen je Gebäude – Ergänzung zu TOPSIS.

**Mechanismus:** Für jede Alternative i mit n Attributen: $U_i = 2 + \frac{1}{\ln(n)} \sum_{j=1}^{n} \frac{z_{ij}}{\sum z_{ij}} \ln\!\left(\frac{z_{ij}}{\sum z_{ij}}\right)$

Ergebnis: Entropie-Index $U_i$ ∈ [1, 2], wobei 1 = vollständig homogen, 2 = maximal heterogen. Der TOPSIS-Score wird mit $U_i$ multipliziert. Optional zuschaltbar.

---

## Flood Sensitivity Transformation

**Zweck:** (a) Annäherung der rechtsschiefen SVI-Verteilung an eine Normalverteilung; (b) Vermeidung von Nullrisiko bei SVI = 0.

**Formel (Eq. 7):** $SV_{PF} = \left(U_i \cdot SV + \frac{\text{Mean}(U_i \cdot SV)}{4}\right)^{\text{Exponent}}$

Der Schwellenwert (Standardwert: ¼ des Mittelwerts) und der Exponent (Standard: 2) sind konfigurierbar.

---

## Log-Normalverteilung / CDF (Gefährdungsberechnung)

**Zweck:** Übersetzung eines überfluteten Flächenanteils in eine Gefährdungswahrscheinlichkeit.

**Formel (Eq. 11):** $F_X(x) = \Phi\!\left(\frac{\ln(x) - \mu}{\sigma}\right)$, mit μ = 0, σ = 0.25

- **HWB (Eq. 11/12):** Summe der CDF-Werte über alle Wassertiefenklassen 30–100 cm (10-cm-Schritte) im 2-m-Puffer: $H_{WB} = \sum F_X(4 \cdot P_{2m,\text{depth}})$
- **HMA (Eq. 13):** Maximum der CDF-Werte aus drei Pufferzonen (5 m Gebäudepuffer, 15 m Straßenpuffer, 15–30 m Straßenpuffer): $H_{MA} = \max[F_X(4 \cdot A_{5m,30cm}),\; F_X(4 \cdot R_{15m,30cm}),\; F_X(4 \cdot R_{30m,30cm})]$

Flooded road areas < 4 m² werden ignoriert.

---

## IPCC-Risikoformel (Potenzprodukt)

**PFRWB (Eq. 14):** $PFR_{WB} = (SV_{PF})^a \cdot (E_{WB})^b \cdot (H_{WB})^c$

**PFRMA (Eq. 15):** $PFR_{MA} = (SV_{PF})^a \cdot (E_{MA})^b \cdot (H_{MA})^c$

Exponent a, b, c = optionale Gewichtung der Subindizes (Standard: 1).

---

## Iterative Mittelwertklassifizierung (Head/Tail Breaks)

**Zweck:** Klassifizierung der Risikowerte in ordinale Klassen (kein Risiko → sehr hohes Risiko) für die Kartendarstellung. Geeignet für schiefseitig verteilte (heavy-tailed) Daten.

**Mechanismus:** Mittelwert berechnen → nur Werte oberhalb behalten → wiederholen. Jede Iteration liefert eine Klassengrenze. Die absolute Höhe der Werte ist nicht entscheidend, sondern die relative Gradation.

---

# Steps

Gesamtformel: **Risiko = Gefährdung^w₁ × Exposition^w₂ × Vulnerabilität^w₃**

---

## Modul 1 – Vulnerabilität (Social Vulnerability Index)

### Step 1.1 – Sensitivity (TOPSIS)
_Skript: `1_Step1_Calculating_the_Sensitivity_using_TOPSIS.py`_

#### Inputs
- `ES` - no of elderly singles over 65
- `C` - no of children under 10 years
- Weights for each input (Summe = 1, je ein Wert pro Feld), default: `ES: 0,7` `C: 0,3` (Vogelbach et al, Zeile 155)
- Entropy used (True/False – ob Entropie-Gewichtung angewendet wird)

#### Outputs
- `Sen` (Sensitivity-Index, TOPSIS-Closeness-Koeffizient × Entropie)

---

### Step 1.2 – Coping Capacity (TOPSIS)
_Skript: `1_Step2_Calculating_the_Coping_Capacity_using_TOPSIS.py`_

#### Inputs
- `WR` - no of recipients of social welfare
- `EDQ` - no of residents without school diploma within the last three years
- Weights for each input (Summe = 1), default: `WR: 0,5` `EDQ: 0,5`
- Entropy used (True/False)

#### Outputs
- `CCap` (Coping Capacity Index)

---

### Step 1.3 – Social Vulnerability Index / SVI (TOPSIS)
_Skript: `1_Step3_Calculating_the_SVI_using_TOPSIS.py`_

#### Inputs
- `Sen` - Sensitivity (aus Step 1.1)
- `CCap` - coping capacity (aus Step 1.2)
- Weights (Summe = 1)
- Entropy used (True/False)

#### Outputs
- `SV` (Social Vulnerability Index, aggregierter TOPSIS-Score)

---

### Step 1.4 – Flood Susceptibility Transformation
_Skript: `1_Step4_Flood_succeptibility_transformation.py`_

Nichtlineare Transformation: `SV_PF = (SV + Threshold × mean(SV))^Exponent`

#### Inputs
- `SV` (aus Step 1.3)
- Threshold (z. B. 0.25 → entspricht `mean(SV)/4`)
- Transformation / Exponent (z. B. 2 für quadratische Verstärkung)

#### Outputs
- `SV_PF` (Social Vulnerability – Potential for Flooding): SVI related to PF

---

## Modul 2 – Exposition

### Step 2 – Residents per Building / Ground Floor
_Skript: `2_Step2_Exposure_Residents.py`_

Formel: `R = (Floors − (Building_type − 1)) × Shape_Area / LivingArea × Residents`
Formel Erdgeschoss: `R_G = −(Building_type − 2) × R / Floors`

#### Inputs
- `Building_ExampleLayer` (Gebäude-Feature-Class mit Feldern: `Floors`, `Building_type`, `Shape_Area`, `StatisticalUnit`)
- `StatisticalExampleUnit` (Lookup-Tabelle mit Feldern: `StatisticalUnit`, `Residents`, `LivingArea`)

#### Outputs
- `StatisticalUnit` (zugeordnete statistische Einheit je Gebäude)
- `Residents` (Einwohner der statistischen Einheit, vom Lookup)
- `LivingArea` (Wohnfläche der statistischen Einheit, vom Lookup)
- `Area_house` (Nutzfläche des Gebäudes: Netto-Etagen × Grundfläche)
- `R` (Einwohner je Gebäude, flächengewichtet)
- `R_G` (Einwohner im Erdgeschoss – Expositionsindex)

---

## Modul 3 – Gefährdung: Mobilität & Erreichbarkeit

### Step 3.1 – Räumliche Verschneidung Mobilitätsgefährdung
_Skript: `3_Step1_Calculating_the_Hazard_for_Mobility_and_Accessability.py`_

Erzeugt konzentrische Pufferzonen um Gebäude, verschneidet diese mit Straßen- und Überflutungslayern und tabelliert den überfluteten Flächenanteil je Zone.

#### Inputs
- Buildings geometry (Gebäude-Feature-Class mit Feld `ID`)
- Buffer 1 (z. B. 5 m) – direkte Umgebung (Überflutung)
- Buffer 2 (z. B. 15 m) – nahe Zugangswege (Straßen × Überflutung)
- Buffer 3 (z. B. 30 m) – erweiterte Zugangswege (Straßen × Überflutung)
- Street Layer 1 (für 15-m-Zone)
- Street Layer 2 (für 30-m-Zone, analog zum vorherigen)
- Flood Layer für 5-m-Zone (z. B. 30-cm-Szenario)
- Flood Layer für 15-m-Zone
- Flood Layer für 30-m-Zone

#### Outputs
- `F30A5m` / `F30P5m` – überflutete Fläche [m²] / Anteil [%] im 5-m-Puffer
- `F30A15m` / `F30P15m` – überflutete Fläche / Anteil im 15-m-Straßenbereich
- `F30A30m` / `F30P30m` – überflutete Fläche / Anteil im 15–30-m-Straßenbereich

---

### Step 3.2 – Mobilitätsgefährdungsindex (Log-Normal)
_Skript: `3_Step2_Calculating_the_Hazard_for_Mobility_and_Accessability.py`_

Formel: `HMA = lognorm.cdf(max(Anteil_5m, Anteil_15m, Anteil_30m) × 4, σ)`

#### Inputs
- `F30A5m`, `F30P5m` (aus Step 3.1)
- `F30A15m`, `F30P15m` (aus Step 3.1)
- `F30A30m`, `F30P30m` (aus Step 3.1)
- Minimum Area overlap (Mindestverschnittfläche für Straßenbewertung)
- Shape parameter σ (Log-Normal-Formparameter)

#### Outputs
- `HMA` (Hazard Mobility & Accessibility Index, Wert zwischen 0 und 1)

---

## Modul 4 – Gefährdung: Wohlbefinden / Gesundheit

### Step 4.1 – Räumliche Verschneidung Gesundheitsgefährdung
_Skript: `4_Step1_Calculating_Hazard_WB.py`_

Erzeugt einen 2-m-Puffer um jedes Gebäude und tabelliert den überfluteten Flächenanteil für mehrere Wassertiefenszenarien.

#### Inputs
- Buildings geometry (Gebäude-Feature-Class mit Feld `ID`)
- Ordner mit Überflutungs-Shapefiles (ein Polygon-Layer je Tiefenstufe)
- Dateinamens-Muster (Standard: `Flood_*`)

#### Outputs
- `P2{Name}` – überfluteter Anteil [%] im 2-m-Puffer je Tiefenstufe (z. B. `P2Flood_30cm`)
- `A2{Name}` – überflutete Fläche [m²] im 2-m-Puffer je Tiefenstufe (z. B. `A2Flood_30cm`)
- (je Tiefenstufe ein Feldpaar, typisch: 20, 30, 40, 50, 60, 70, 80, 90, 100 cm)

---

### Step 4.2 – Gesundheitsgefährdungsindex (Log-Normal)
_Skript: `4_Step2_Calculating_Hazard_WB.py`_

Formel: `HWB = Σ lognorm.cdf(P2Flood_Xcm × 4 / 100, σ)` über alle Tiefenstufen

#### Inputs
- `P2Flood_20cm` … `P2Flood_100cm` (aus Step 4.1, bis zu 9 Felder)
- Shape parameter σ (Log-Normal-Formparameter)

#### Outputs
- `HWB` (Hazard Well-Being Index)

---

## Modul 5 – Risikoberechnung

### Step 5 – Risk Calculation
_Skript: `5_Step5_Risk_Calculation.py`_

Formel: `PFR_WB = HWB^w₁ × R_G^w₂ × SV_PF^w₃`

#### Inputs
- `HWB` (Gefährdungsindex aus Step 4.2)
- `R_G` (Expositionsindex aus Step 2)
- `SV_PF` (Vulnerabilitätsindex aus Step 1.4)
- `Exponent_Hazard` (w₁)
- `Exponent_Exposure` (w₂)
- `Exponent_Vulnerability` (w₃)

#### Outputs
- `PFR_WB` (Pluvial Flood Risk – Well-Being, Risikoindex je Gebäude)

---

## Modul 6 – Klassifizierung zur Visualisierung

### Step 6 – Iterative Mean Classification (Head/Tail Breaks)
_Skript: `6_Step6_Calculating_classes_for_visualization.py`_

Iterativer Algorithmus: Mittelwert berechnen → nur Werte oberhalb behalten → wiederholen. Liefert Klassengrenzen für die kartografische Darstellung.

#### Inputs
- Tabelle / Feature-Class
- Field (das Risikofeld, z. B. `PFR_WB`)
- n_iterations (Anzahl der Iterationsschritte / gewünschte Klassenanzahl − 1)

#### Outputs
- Klassengrenzen-Werte (als ArcGIS-Message ausgegeben: `Calculated Means: [...]`)
-	PFR_WB (Risk_Field)

---

# Implementierungsplan: Open-Source Python Pipeline mit OGC API

**Ziel:** Den Algorithmus aus Vogelbacher et al. (2025) als selbst-enthaltene Python-Pipeline neu implementieren: arcpy → geopandas/numpy/scipy, saubere Modulstruktur, Pydantic-Validierung, explizite Feldnamen, OGC API Processes via `fastprocesses`. Alle Schritte des Papers laufen ohne weitere Benutzerinteraktion durch; Ein- und Ausgabe erfolgen als GeoJSON über eine einzige OGC-Prozess-Schnittstelle.

---

## Phase 1 — Projektgerüst & Konventionen

**1.1** Paketstruktur unter `src/pluvial_flood_risk/` anlegen:

```bash
models/
    inputs.py ← Pydantic-Parameter-Modelle
    field_names.py ← Zentrale Feldnamens-Konstanten
algorithms/
    vulnerability.py ← Steps 1.1–1.4
    exposure.py ← Step 2
    hazard_wb.py ← Steps 4.1–4.2
    hazard_ma.py ← Steps 3.1–3.2
    risk.py ← Step 5
    classification.py ← Step 6
processes/
    flood_risk_process.py ← OGC BaseProcess
pipeline.py ← Gesamtorchestrierung

app.py ← FastAPI-Einstiegspunkt
```


**1.2** `pyproject.toml` ergänzen: `geopandas`, `shapely`, `scipy`, `numpy`, `pandas`

**1.3** Feldnamen-Mapping (`field_names.py`) als zentrale Konstanten:

| Alt (arcpy/CSV) | Neu (explizit) |
|----------------|----------------|
| `WR` | `social_welfare_recipients` |
| `C` | `children_under_10` |
| `ES` | `elderly_singles_over_65` |
| `EDQ` | `no_highschool_diploma` |
| `Sen` | `sensitivity_index` |
| `CCap` | `coping_capacity_index` |
| `SV` | `social_vulnerability_index` |
| `SV_PF` | `social_vulnerability_flood_potential` |
| `Floors` | `floors_above_ground` |
| `Building type` | `building_type` |
| `StatisticalUnit` | `statistical_unit_id` |
| `Residents` | `residents_in_statistical_unit` |
| `LivingArea` | `living_area_m2_statistical_unit` |
| `Area_house` | `building_living_area_m2` |
| `R` | `residents_per_building` |
| `R_G` / `EWB` | `residents_ground_floor` |
| `EMA` | `exposure_mobility_accessibility` |
| `HMA` | `hazard_mobility_accessibility` |
| `HWB` | `hazard_well_being` |
| `PFR_WB` | `pluvial_flood_risk_well_being` |
| `PFR_MA` | `pluvial_flood_risk_mobility_accessibility` |

---

## Phase 2 — Pydantic-Modelle (`models/inputs.py`)

Validierte Parameterklassen mit Paper-Defaults:

- `VulnerabilityWeights` — Gewichte für C (0.7), ES (0.3), WR (0.5), EDQ (0.5), Sensitivity (0.5), Coping Capacity (0.5); `apply_entropy: bool = True`
- `FloodSensitivityParams` — `threshold_fraction: float = 0.25`, `exponent: int = 2`
- `HazardParams` — Pufferweiten 5/15/30/2 m, `min_street_flood_area_m2: float = 4.0`, `lognormal_shape: float = 0.25`
- `RiskWeights` — Exponenten a, b, c (Standard: 1.0)
- `PipelineParams` — aggregiert alle obigen Klassen

---

## Phase 3 — Algorithmus-Module (arcpy → open-source)

arcpy-Ersetzungsmatrix:

| arcpy | Open-Source |
|-------|-------------|
| `Buffer` / `PairwiseBuffer` | `gdf.buffer(d).dissolve(by='id')` |
| `Erase` (A−B) | `gpd.overlay(A, B, how='difference')` |
| `TabulateIntersection` | `gpd.overlay(..., how='intersection')` → `.area` |
| `JoinField` | `gdf.merge(df, on='id', how='left')` |
| `SearchCursor` / `UpdateCursor` | pandas-Vektoroperationen |

**3.1** `algorithms/vulnerability.py`
- Kernfunktion `calculate_topsis(data, weights, apply_entropy)` — Eq. 4–6
- Wrapper: `calculate_sensitivity()`, `calculate_coping_capacity()`, `calculate_svi()`
- `apply_flood_sensitivity_transform()` — Eq. 7

**3.2** `algorithms/exposure.py`
- `calculate_exposure(buildings_gdf)` — Eq. 8–10 vektorisiert

**3.3** `algorithms/hazard_wb.py`
- `compute_flood_intersection_wb(buildings_gdf, flood_layers, buffer_m)` — 2-m-Puffer × Tiefenszenarien
- `calculate_hazard_wb(gdf, flood_depth_columns, shape)` — Eq. 11/12

**3.4** `algorithms/hazard_ma.py`
- `compute_flood_intersection_ma(buildings_gdf, streets_gdf, flood_gdf, params)` — konzentrische Puffer
- `calculate_hazard_ma(gdf, params)` — Eq. 13

**3.5** `algorithms/risk.py`
- `calculate_risk(gdf, params)` — Eq. 14/15

**3.6** `algorithms/classification.py`
- `iterative_mean_breaks(values, n_iterations)` — Klassengrenzen
- `classify_risk(gdf, risk_column, n_iterations)` — ordinale Klasse als Spalte

---

## Phase 4 — Pipeline-Orchestrierung (`pipeline.py`)

`run_pipeline(buildings_gdf, streets_gdf, flood_layers_wb, flood_layers_ma, params: PipelineParams) -> GeoDataFrame`

Sequenzielle Ausführung:
1. Pydantic-Validierung
2. `calculate_sensitivity()`
3. `calculate_coping_capacity()`
4. `calculate_svi()`
5. `apply_flood_sensitivity_transform()`
6. `calculate_exposure()`
7. `compute_flood_intersection_wb()` → `calculate_hazard_wb()`
8. `compute_flood_intersection_ma()` → `calculate_hazard_ma()`
9. `calculate_risk()`
10. `classify_risk()` (für WB und MA)

---

## Phase 5 — OGC API Processes (`processes/flood_risk_process.py`)

`PluvialFloodRiskProcess(BaseProcess)` mit `@register_process("pluvial_flood_risk")`

**Inputs:**
- `buildings` — GeoJSON FeatureCollection (`application/geo+json`)
- `streets` — GeoJSON FeatureCollection
- `flood_layers_wb` — Array von GeoJSON-Objekten (je Tiefenstufe 20–100 cm)
- `flood_layers_ma` — GeoJSON FeatureCollection (30-cm-Szenario)
- `params` — JSON-Objekt (optional, alle Felder mit Defaults aus `PipelineParams`)

**Outputs:**
- `risk_features` — GeoJSON FeatureCollection mit allen berechneten Spalten
- `classification_breaks_wb` / `classification_breaks_ma` — Arrays von Klassengrenzen

`execute()`: GeoJSON → GDF → Pydantic-Validierung → `run_pipeline()` → GeoJSON; Fortschritt via `job_progress_callback`

---

## Phase 6 — Tests

- Unit-Tests je Algorithmus-Modul gegen Referenzwerte aus `data/example/Building_ExampleLayer.csv`
- Integrationstest: vollständiger `run_pipeline()`-Durchlauf mit Beispieldaten
- Prozess-Test: synthetischer GeoJSON-Input, Prüfung Output-Struktur

---

## Entscheidungen & Scope

- **Ein OGC-Prozess** für die gesamte Pipeline (kein separater Prozess je Modul)
- GeoJSON↔GDF-Konvertierung ausschließlich in `processes/`; Algorithmen arbeiten intern immer auf GeoDataFrames
- Optionales Feldnamen-Mapping als Prozess-Input für abweichende Quell-Feldnamen (TODO: ausdetaillieren)
- **Außerhalb des Scope:** Hydrologische Simulation, Datenbankanbindung, Frontend