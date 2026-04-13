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

## Modul 1 – Vulnerabilität (Social Vulnerability Index) ✅

### Step 1.1 – Sensitivity (TOPSIS) ✅
_Skript: `1_Step1_Calculating_the_Sensitivity_using_TOPSIS.py`_

#### Inputs
- `ES` - no of elderly singles over 65
- `C` - no of children under 10 years
- Weights for each input (Summe = 1, je ein Wert pro Feld), default: `ES: 0,7` `C: 0,3` (Vogelbach et al, Zeile 155)
- Entropy used (True/False – ob Entropie-Gewichtung angewendet wird)

#### Outputs
- `Sen` (Sensitivity-Index, TOPSIS-Closeness-Koeffizient × Entropie)

---

### Step 1.2 – Coping Capacity (TOPSIS) ✅
_Skript: `1_Step2_Calculating_the_Coping_Capacity_using_TOPSIS.py`_

#### Inputs
- `WR` - no of recipients of social welfare
- `EDQ` - no of residents without school diploma within the last three years
- Weights for each input (Summe = 1), default: `WR: 0,5` `EDQ: 0,5`
- Entropy used (True/False)

#### Outputs
- `CCap` (Coping Capacity Index)

---

### Step 1.3 – Social Vulnerability Index / SVI (TOPSIS) ✅
_Skript: `1_Step3_Calculating_the_SVI_using_TOPSIS.py`_

#### Inputs
- `Sen` - Sensitivity (aus Step 1.1)
- `CCap` - coping capacity (aus Step 1.2)
- Weights (Summe = 1)
- Entropy used (True/False)

#### Outputs
- `SV` (Social Vulnerability Index, aggregierter TOPSIS-Score)

---

### Step 1.4 – Flood Susceptibility Transformation ✅
_Skript: `1_Step4_Flood_succeptibility_transformation.py`_

Nichtlineare Transformation: `SV_PF = (SV + Threshold × mean(SV))^Exponent`

#### Inputs
- `SV` (aus Step 1.3)
- Threshold (z. B. 0.25 → entspricht `mean(SV)/4`)
- Transformation / Exponent (z. B. 2 für quadratische Verstärkung)

#### Outputs
- `SV_PF` (Social Vulnerability – Potential for Flooding): SVI related to PF

---

## Modul 2 – Exposition ✅

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

> **Referenzskript für die Implementierung: `v2`-Versionen** (`3_Step1_…_v2.py`, `3_Step2_…_v2.py`)
> Step 2 ist in beiden Versionen identisch. Step 1 wurde vereinfacht (siehe unten).

### Step 3.1 – Räumliche Verschneidung Mobilitätsgefährdung
_Skript: `3_Step1_Calculating_the_Hazard_for_Mobility_and_Accessability_v2.py`_

Erzeugt konzentrische Pufferzonen um Gebäude, verschneidet diese mit Straßen- und Überflutungslayern und tabelliert den überfluteten Flächenanteil je Zone.

#### Inputs (v2 — vereinfacht gegenüber v1)
- Buildings geometry (Gebäude-Feature-Class mit Feld `ID`)
- Buffer 1 (z. B. 5 m) – direkte Umgebung (Überflutung ohne Straßen)
- Buffer 2 (z. B. 15 m) – innere Straßenzone
- Ringbreite äußere Zone (z. B. 15 m → ergibt 15–30-m-Ring ab Gebäude) ← _neu in v2, ersetzt festen Buffer 3_
- **Ein** Street Layer (für beide Straßenzonen) ← _v1 hatte zwei getrennte Layer_
- Flood Layer für 15m/30m-Straßenzonen ← _v1 hatte drei getrennte Flood-Layer_
- Flood Layer für 5m-Zone (kann abweichen)

#### Outputs
- `F30A5m` / `F30P5m` – überflutete Fläche [m²] / Anteil [%] im 5-m-Puffer
- `F30A15m` / `F30P15m` – überflutete Fläche / Anteil im 15-m-Straßenbereich
- `F30A30m` / `F30P30m` – überflutete Fläche / Anteil im 15–30-m-Straßenbereich

#### Geometrische Konstruktion (v2)
1. `Buffer(buildings, 15m, OUTSIDE_ONLY)` → B15
2. `MultipleRingBuffer(B15, +ringbreite, OUTSIDE_ONLY)` → B1530 (äußerer Ring)
3. `Intersect([streets, B1530])` → BuS30 (Straßen im äußeren Ring)
4. `TabulateIntersection(BuS30, flood_layer)` → F30A30m / F30P30m
5. `Erase(B15, buildings)` → Bu15m (innerer Ring ohne Gebäudefläche)
6. `Intersect([streets, Bu15m])` → BuS15
7. `TabulateIntersection(BuS15, flood_layer)` → F30A15m / F30P15m
8. `Buffer(buildings, 5m, OUTSIDE_ONLY)` → B5 → `Erase(B5, buildings)` → Bu5
9. `TabulateIntersection(Bu5, flood_layer_5m)` → F30A5m / F30P5m

> **Hinweis für Implementierung:** v1 enthielt ein zweites `Erase` auf BuS30, das bei dicht stehenden Gebäuden zu minimal anderen Zonengeometrien führte. v2 verzichtet darauf — das ist sowohl einfacher als auch konsistenter. Bei der geopandas-Umsetzung analog vorgehen.

---

### Step 3.2 – Mobilitätsgefährdungsindex (Log-Normal)
_Skript: `3_Step2_Calculating_the_Hazard_for_Mobility_and_Accessability_v2.py`_

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
- `HazardParams` — Pufferweiten 5/15/30/2 m, `min_street_flood_area_m2: float = 4.0`, `lognormal_shape: float = 0.25`, `ma_flood_level_cm: int = 30`
- `RiskWeights` — Exponenten a, b, c (Standard: 1.0)
- `PipelineParams` — aggregiert alle obigen Klassen; enthält `target_crs: str | None = None` (siehe unten)

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

## Phase 4 — Sub-Pipelines (`pipeline.py`)

Die vier unabhängigen Berechnungsäste werden als eigenständige Funktionen implementiert, die jeweils ein GeoDataFrame mit den neu berechneten Spalten zurückgeben:

- `run_vulnerability_pipeline(buildings_gdf, params) -> GeoDataFrame` — Steps 1.1–1.4
- `run_exposure_pipeline(buildings_gdf) -> GeoDataFrame` — Step 2
- `run_hazard_wb_pipeline(buildings_gdf, flood_layers_wb, params) -> GeoDataFrame` — Steps 4.1–4.2
- `run_hazard_ma_pipeline(buildings_gdf, streets_gdf, flood_layers_ma, params) -> GeoDataFrame` — Steps 3.1–3.2

Dazu eine Merge-Funktion:
- `merge_and_finalize(partial_results, buildings_gdf, params) -> GeoDataFrame` — führt die Teilergebnisse zusammen, berechnet Risk (Eq. 14/15) und Klassifizierung

Diese Funktionen haben keine Kenntnis von Celery oder OGC — sie sind direkt unit- und integrierbar testbar.

---

## Phase 5 — OGC API Processes (`processes/flood_risk_process.py`)

**Parallelisierungsstrategie: Operation Fan-out (`BaseScatterProcess`)**

Die vier Berechnungsäste sind voneinander unabhängig und arbeiten auf demselben Input — das entspricht exakt dem Scatter-Gather-Muster von `BaseScatterProcess`. Jeder `@parallel_step` läuft auf einem eigenen Celery-Worker.

```
@parallel_step vulnerability_pipeline  ─┐
@parallel_step exposure_pipeline        ┤
@parallel_step hazard_wb_pipeline       ┤  → merge_results → Risk + Classification
@parallel_step hazard_ma_pipeline      ─┘
```

`PluvialFloodRiskProcess(BaseScatterProcess)` mit `@register_process("pluvial_flood_risk")`

**Inputs** (in `ProcessDescription`):
- `buildings` — GeoJSON FeatureCollection (`application/geo+json`)
- `streets` — GeoJSON FeatureCollection
- `flood_layers_wb` — Array von GeoJSON-Objekten (je Tiefenstufe 20–100 cm)
- `flood_layers_ma` — GeoJSON FeatureCollection (30-cm-Szenario)
- `params` — JSON-Objekt (optional, alle Felder mit Defaults aus `PipelineParams`)

**Outputs:**
- `risk_features` — GeoJSON FeatureCollection mit allen berechneten Spalten
- `classification_breaks_wb` / `classification_breaks_ma` — Arrays von Klassengrenzen

**Implementierungsstruktur:**
```python
@register_process("pluvial_flood_risk")
class PluvialFloodRiskProcess(BaseScatterProcess):

    @parallel_step
    def vulnerability_pipeline(self, exec_body: dict) -> dict:
        # GeoJSON → GDF → run_vulnerability_pipeline() → GDF → JSON-Records
        ...

    @parallel_step
    def exposure_pipeline(self, exec_body: dict) -> dict:
        ...

    @parallel_step
    def hazard_wb_pipeline(self, exec_body: dict) -> dict:
        ...

    @parallel_step
    def hazard_ma_pipeline(self, exec_body: dict) -> dict:
        ...

    def merge_results(self, results: dict[str, dict]) -> RiskResult:
        # Teilergebnisse zusammenführen → merge_and_finalize() → GeoJSON
        ...
```

Jeder `@parallel_step` deserialisiert das GeoJSON selbst (GeoJSON → GDF), ruft die zugehörige Sub-Pipeline auf und gibt nur die neu berechneten Spalten (als Records-JSON) zurück — nicht das gesamte GeoJSON. `merge_results` fügt alles auf dem Original-GDF zusammen.

---

## Phase 6 — Tests (TDD-Ansatz)

Eingangsdaten kommen in Produktion von einer OGC API Processes-Schnittstelle. Tests sollen die Business-Logik unabhängig davon verifizieren können.

### Testpyramide

**Unit-Tests — Kernlogik (TDD):** `tests/algorithms/`
- Funktionen auf Numpy-/Pandas-Ebene ohne räumliche Abhängigkeiten
- Referenzwerte direkt aus den Paper-Gleichungen oder aus `Building_ExampleLayer.csv` berechnet
- Werden **vor** der Implementierung geschrieben (TDD)
- Beispiele:
  - `test_topsis_closeness_coefficients()` — Eq. 5 gegen manuell berechnete Werte
  - `test_entropy_homogeneous_input_returns_1()` — Grenzfall: alle Attribute gleich → U=1
  - `test_flood_sensitivity_transform_prevents_zero_risk()` — SV=0 → SV_PF > 0
  - `test_lognorm_hazard_wb_summation()` — Eq. 12 gegen scipy-Referenz
  - `test_iterative_mean_breaks_known_values()` — bekannte Eingabe, bekannte Grenzen
  - `test_risk_product_exponents()` — Eq. 14/15 gegen manuelles Potenzprodukt

**Unit-Tests — Räumliche Vorverarbeitung:** `tests/algorithms/` (synthetische Geometrien)
- In-Memory-GeoDataFrames mit einfachen Rechtecken (Gebäude, Straßen, Flood-Polygone)
- Prüft korrekte Buffer-Abstände, Overlay-Logik, Anteilsberechnung
- Kein TDD nötig, aber Smoke-Tests für Regressionssicherheit

**Integrationstests:** `tests/test_pipeline.py`
- Vollständiger `run_pipeline()`-Durchlauf mit `data/example/Building_ExampleLayer.csv`
- Prüft: alle erwarteten Ausgabespalten vorhanden, keine NaN in Pflichtspalten, Wertebereiche [0, 1] für Indices

**Prozess-Tests:** `tests/test_process.py`
- Synthetisches minimales GeoJSON-Input-Objekt
- Prüft Output-Struktur (GeoJSON FeatureCollection, Pflicht-Properties vorhanden)
- Kein Inhalt geprüft (das erledigen Unit-Tests)

### Fixtures (`tests/conftest.py`)
- `sample_buildings_gdf` — GeoDataFrame aus `Building_ExampleLayer.csv`
- `minimal_buildings_gdf` — 3-Gebäude-GDF für schnelle Unit-Tests
- `default_pipeline_params` — `PipelineParams` mit Paper-Defaults

## Koordinatensystem-Handling (CRS-Strategie)

GeoJSON ist laut RFC 7946 auf WGS84 (EPSG:4326, geografische Koordinaten in Grad) festgelegt. OGC API Features Part 2 (CRS) erlaubt jedoch auch andere CRS — z. B. lokale projizierte Systeme — via Content-Crs-Header im HTTP-Response. Da unsere Pipeline räumliche Berechnungen durchführt (Puffer in Metern, Flächenanteil), muss intern mit einem **projizierten, metrischen CRS** gearbeitet werden.

### Auflösungsreihenfolge (`PipelineParams.target_crs`)

| Szenario | Verhalten |
|----------|-----------|
| `target_crs = "EPSG:25832"` (explizit) | Alle Layer werden in diesen CRS reprojiziert |
| `target_crs = None`, Input bereits projiziert | CRS des Inputs wird unverändert verwendet |
| `target_crs = None`, Input geografisch (EPSG:4326) | Auto-Detect über `geopandas.estimate_utm_crs()` |

### Implementierung

**`PipelineParams.target_crs: str | None = None`** — optionaler EPSG-String (z. B. `"EPSG:25832"`).

**`PluvialFloodRiskProcess._to_metric(gdf, target_crs, fallback_gdf)`** — zentraler Helfer in der Prozessschicht:

```python
@staticmethod
def _to_metric(gdf, target_crs, fallback_gdf=None):
    if target_crs is not None:
        return gdf.to_crs(target_crs)             # explizit vorgegeben
    if gdf.crs is not None and gdf.crs.is_projected:
        return gdf                                 # schon metrisch
    reference = gdf if not gdf.empty else fallback_gdf
    return gdf.to_crs(reference.estimate_utm_crs()) # Auto-UTM
```

Alle drei räumlichen Branches (`exposure_pipeline`, `hazard_ma_pipeline`, künftig `hazard_wb_pipeline`) rufen `_to_metric()` auf. Der `fallback_gdf`-Parameter stellt sicher, dass leere Layer (z. B. ein leerer Flood-Layer) durch die Gebäudegeometrie geerdet werden.

### OGC API: `target_crs` als Parameter

Im `params`-Objekt der Prozessbeschreibung ist `target_crs` als optionaler String-Parameter dokumentiert:

```json
"target_crs": {
  "type": "string",
  "examples": ["EPSG:25832", "EPSG:32632"],
  "description": "EPSG code for spatial calculations. Omit to auto-detect UTM."
}
```

**Empfehlung für Nutzer:**
- **Hamburg / Norddeutschland:** `EPSG:25832` (ETRS89 UTM-32N)
- **Großbritannien:** `EPSG:27700` (British National Grid)
- **Nordamerika (NYC):** `EPSG:2263` (NY State Plane)
- **Unbekanntes Gebiet / WGS84-Input:** weglassen (Auto-UTM)

---

## Architekturentscheidung: Kein volles Hexagonal-Muster

**Fazit: Nicht empfehlenswert** für diese Codebase — aber die Kernidee des Musters wird selektiv angewendet.

**Warum nicht vollständiges Hexagonal/Clean Architecture:**
- Zu wenige Module (~8 in `algorithms/` + 1 Pipeline + 1 Prozess) — kein Nutzen für abstrakte Ports
- Kein DB-Layer, kein zweites Interface, kein Bedarf für Dependency Injection
- Abstrakte Interfaces würden Boilerplate ohne konkreten Mehrwert erzeugen

**Was trotzdem umgesetzt wird (Kernprinzip selektiv):**

Die Codebase gliedert sich in drei natürliche Schichten, die durch Konvention — nicht durch abstrakte Klassen — getrennt bleiben:

| Schicht | Inhalt | Abhängigkeiten | Testbarkeit |
|---------|--------|----------------|-------------|
| **Kernlogik** | TOPSIS, Entropy, lognorm-CDF, Risikoprodukt, Mean-Breaks | nur `numpy`/`scipy` | trivial per TDD — `np.ndarray` rein, Skalar/Array raus |
| **Räumliche Vorverarbeitung** | Buffer, Overlay, Tabulate Intersection | `geopandas`/`shapely` | synthetische In-Memory-GeoDataFrames |
| **OGC-Adapter** | GeoJSON↔GDF, Prozessregistrierung | `fastprocesses` | dünner Adapter, kaum eigene Logik |

**Konvention:** Kernlogik-Funktionen (z. B. `calculate_topsis`, `hazard_wb_from_percentages`) nehmen **keine GeoDataFrames** als Argumente — ausschließlich `np.ndarray`, `pd.Series` oder skalare Werte. Spatial-Funktionen rufen die Kernlogik auf, nicht umgekehrt. Die OGC-Prozessklasse ruft ausschließlich die Pipeline auf, enthält selbst keine Berechnungslogik.

Das gibt 80 % der Testbarkeitsvorteile von Hexagonal ohne den Overhead.

---

## Code-Qualitäts-Richtlinien

### Lesbarkeit & numpy/pandas-Stil
- **Keine Python-Loops über Zeilen**, wo numpy-Vektoroperationen verfügbar sind (z. B. `np.sqrt(np.sum(..., axis=1))` statt `for i in range(m)`)
- **Keine interleaved Index-Arithmetik** (z. B. `D[2*i]` / `D[2*i+1]`): semantisch verschiedene Größen bekommen eigene benannte Arrays/Variablen
- **`np.where`** statt `if/else` innerhalb von Schleifen für elementweise Bedingungen
- Zwischenergebnisse als benannte Variablen statt verschachtelter Ausdrücke

### Performanz (Low Hanging Fruits)
- Alle Schleifen über Zeilen/Spalten der Datenmatrix durch vektorisierte numpy-Operationen ersetzen
- Keine redundante Berechnung von `max`, `min`, `sum` mehrfach über dieselbe Spalte
- Räumliche Operationen (Buffer, Overlay): `geopandas` intern bereits auf `shapely` vektorisiert — keine manuellen Schleifen über Geometrien

### Formel-Angleichung ans Paper
- Jede Kernberechnung erhält einen Kommentar mit Gleichungsnummer aus Vogelbacher et al. (2025), z. B.:
  ```python
  # Eq. 4 — Normierung der Attributmatrix
  normalized = data / np.sqrt(np.sum(data**2, axis=0))

  # Eq. 5 — Euklidischer Abstand zur positiven/negativen Ideallösung
  distance_to_best = np.sqrt(np.sum((weighted_normalized - ideal_best) ** 2, axis=1))
  distance_to_worst = np.sqrt(np.sum((weighted_normalized - ideal_worst) ** 2, axis=1))

  # Eq. 5 — TOPSIS Closeness-Koeffizient C ∈ [0, 1]
  total_distance = distance_to_best + distance_to_worst
  closeness = np.where(total_distance > 0, distance_to_worst / total_distance, 0.0)
  ```
- Variablennamen entsprechen soweit möglich den Symbolen im Paper (z. B. `distance_to_best` für $D^+$, `closeness` für $C_i$)
- Abweichungen vom Paper (z. B. Entropy-Multiplikation, Threshold-Addition) werden kommentiert mit Begründung

---

## Entscheidungen & Scope

- **Ein OGC-Prozess** für die gesamte Pipeline (kein separater Prozess je Modul)
- GeoJSON↔GDF-Konvertierung ausschließlich in `processes/`; Algorithmen arbeiten intern immer auf GeoDataFrames
- Kernlogik-Funktionen haben keine räumlichen Abhängigkeiten → direkt per TDD entwickeln
- Optionales Feldnamen-Mapping als Prozess-Input für abweichende Quell-Feldnamen (TODO: ausdetaillieren)
- **Außerhalb des Scope:** Hydrologische Simulation, Datenbankanbindung, Frontend

---

## Erläuterung: NumPy-Matrixoperationen am Beispiel TOPSIS

Für Einsteiger: Was passiert bei den NumPy-Operationen in `topsis.py` konkret?

### Die Daten als Matrix

Jede Zeile = ein Gebäude, jede Spalte = ein Indikator. `M[i, j]` = Wert von Gebäude `i` für Indikator `j`.

```
         Kinder (C)  Ältere (ES)
Gebäude A    1.0        0.0      ← Zeile 0
Gebäude B    2.0        1.0      ← Zeile 1
Gebäude C    3.0        4.0      ← Zeile 2
```

### Schritt 1 — Spaltennormierung + Gewichtung (Eq. 4)

Warum: Indikatoren haben unterschiedliche Skalen → faire Vergleichbarkeit durch L2-Normierung (jede Spalte bekommt Länge 1), dann Multiplikation mit dem zugehörigen Gewicht.

```python
M[:, j]          # die gesamte Spalte j (alle Zeilen, Spalte j)
col_sq_sum       # Summe der quadrierten Spaltenwerte
M[:, j] / sqrt(col_sq_sum) * weight[j]   # normiert + gewichtet
```

### Schritt 2 — Abstände zur Worst-Case- und Best-Case-Referenz (Eq. 5)

```python
ideal_max = M.max(axis=0)   # axis=0: Spaltenmaxima → Worst-Case je Attribut
ideal_min = M.min(axis=0)   # Spaltenminima → Best-Case je Attribut

d_from_max = np.sqrt(np.sum((M - ideal_max)**2, axis=1))
#                            ↑ Broadcasting: ideal_max wird von jeder Zeile subtrahiert
#                                            axis=1: über Spalten summieren → ein Wert je Gebäude
```

**Broadcasting:** `M - ideal_max` subtrahiert die einzeilige Referenz automatisch von *jeder* Zeile in M — kein Python-Loop nötig.

**`axis=0` vs. `axis=1`:**
- `axis=0` → über Zeilen hinweg kollabieren → ein Ergebnis je Spalte (z. B. Spaltenmaxima)
- `axis=1` → über Spalten hinweg kollabieren → ein Ergebnis je Zeile/Gebäude (z. B. Abstandssumme)

### Schritt 3 — Closeness-Koeffizient

```python
C = d_from_min / (d_from_max + d_from_min)
# → C ≈ 0: Gebäude nahe am Best-Case (wenig vulnerabel)
# → C ≈ 1: Gebäude nahe am Worst-Case (sehr vulnerabel)
np.where(denom > 0, ..., 0.0)   # elementweiser if/else ohne Python-Loop
```

### Schritt 4 — Shannon-Entropie-Index U_i (Eq. 6)

```python
M[i]          # Zeile i: alle Indikatorwerte eines einzelnen Gebäudes
p = row / row.sum()             # Anteile je Attribut (sollen auf 1 summieren)
U[i] = 2.0 + (1/ln_n) * sum(p * log(p))   # Shannon-Entropie-Formel
```

- Alle Attribute gleich → maximale Homogenität → U nahe 1
- Ein Attribut dominiert, Rest ≈ 0 → maximale Heterogenität → U = 2
- Enthält eine Zeile eine Null → `log(0)` undefiniert → U = 2 als konservative Annahme

### NumPy-Muster auf einen Blick

| Ausdruck | Bedeutung |
|---|---|
| `M[:, j]` | Gesamte Spalte j |
| `M[i]` | Gesamte Zeile i (ein Gebäude) |
| `M - ideal_max` | Subtraktion einer Zeile von jeder Zeile (Broadcasting) |
| `np.sum(..., axis=0)` | Über Zeilen summieren → ein Wert je Spalte |
| `np.sum(..., axis=1)` | Über Spalten summieren → ein Wert je Zeile/Gebäude |
| `a ** 2` | Jedes Element quadrieren (kein Loop) |
| `np.where(cond, a, b)` | Elementweiser bedingter Ausdruck |