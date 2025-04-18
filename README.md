# FarmDSL 🌾 v0.5
**Open‑source domain‑specific language for smart‑farm automation**

![CI](https://github.com/yourname/farmdsl/actions/workflows/ci.yml/badge.svg)  ![PyPI](https://img.shields.io/pypi/v/farmdsl)

---

## 🔥 What’s new in v0.5
| Area | Upgrade |
|------|---------|
| **Grammar** | `LET`, `ZONE`, `INFER`, and line comments `#` (fully backward‑compatible) |
| **Sensors** | Soil moisture & depth, tractor speed, water‑rate, GPS lat/lon |
| **Actuators** | Spot‑spray, plow‑depth, chemical application (200 actives) |
| **Telemetry** | MQTT streams (`<topic>/sensors`, `<topic>/spray`) |
| **Logs** | GeoJSON for every spray / control action |

---

## 1  Install
```bash
python -m pip install farmdsl paho-mqtt geojson open_weed_locator
```
> Optional libs auto‑stub if absent; you can prototype on any machine.

---

## 2  Quick‑start script
```farm
# vars & polygons
LET target_moisture = 0.18
ZONE Block7 { [148.10,-34.00], [148.12,-34.00], [148.12,-34.02], [148.10,-34.02] }

SETTINGS {
    model_path     = "models/weed_v8.onnx",  # OWL model
    boom_width     = 1.2m,                    # physical boom width
    nozzle_spacing = 0.3m,                    # nozzle distance
    mqtt_host      = "192.168.1.50",         # telemetry broker
    mqtt_topic     = "farm/field1",          # base topic
    geojson_out    = "operations.geojson"     # log file
}

SCHEDULE every 5s         # loop every 5 seconds
TASK sense_all            # refresh sensors + OWL inference
WHEN weed_density > 0 or soil_moisture < target_moisture
THEN spot_spray           # action
```
Run once:
```bash
farm run demo.farm --once
```
Continuous:
```bash
farm run demo.farm
```

---

## 3  Language reference
### 3.1 Keywords
| Keyword | Syntax | What it does |
|---------|--------|--------------|
| `SCHEDULE` | `every <int>[s|m|h|d]` | Sets loop interval (seconds, minutes, hours, days). |
| `TASK` | free text (`sense_all`, `infer_weeds`) | Invoked each loop to read sensors / run inference. Custom tasks coming soon. |
| `WHEN` | boolean expression | Gate—if true, `THEN` executes. Uses sensor vars + `LET` vars. |
| `THEN` | `<action> [args…]` | Calls an action from the runtime registry. |
| `SETTINGS` | `key = value` lines | Configure model paths, hardware geometry, telemetry, etc. |
| `LET` | `LET name = value` | Defines a variable available in expressions (supports units). |
| `ZONE` | `ZONE name { [lon,lat], … }` | Defines a polygon for spatial logic (planned). |
| `INFER` | `INFER weed ON camera_1 [WITH path]` | Manual inference request (planned). |
| `#` | `# any comment` | Comment—ignored by parser. |

### 3.2 Settings keys
| Key | Type / Unit | Purpose |
|-----|-------------|---------|
| `model_path` | string (file) | YOLO/OWL model used for weed detection. |
| `boom_width` | length | Total boom width for nozzle mapping. |
| `nozzle_spacing` | length | Distance between spray nozzles. |
| `mqtt_host` | string | MQTT broker hostname/IP. |
| `mqtt_port` | int (default 1883) | Broker port. |
| `mqtt_topic` | string | Base topic under which messages publish. |
| `geojson_out` | string (file) | Where to write GeoJSON logs. |
| *(any key)* | – | Free to reference in code via `prog.settings.get()`.

### 3.3 Sensors (auto‑populated)
| Variable | Unit / Type | Meaning |
|----------|-------------|---------|
| `weed_density` | float (1/m) | Bounding boxes per metre of boom. |
| `weed_bboxes` | list[dict] | Full YOLO bbox list. |
| `soil_moisture` | ratio (0‑1) | Volumetric soil water content. |
| `soil_depth` | length | Depth probe reading. |
| `tractor_speed` | speed | Current machine ground speed. |
| `water_rate` | volume/area | Irrigation flow rate. |
| `gps_lat`, `gps_lon` | degrees | Current GPS coordinates. |
| *LET vars* | any | Your own variables defined with `LET`. |

### 3.4 Actions
| Action | Args | Effect |
|--------|------|--------|
| `spot_spray` | none | Fires correct nozzle for each weed bbox. |
| `set_plow_depth` | `<depth>` e.g. `0.25m` | Raises/lowers plow to depth. |
| `apply_chemical` | `<name> <rate>` | Applies chemical (validated against catalogue) at rate (e.g. `3l/ha`). |
| *(custom)* | – | Decorate a coroutine with `@action("verb")`. |

---

## 4  Telemetry & logging
| Channel | Contents | How to use |
|---------|----------|-----------|
| **MQTT** | JSON of sensors (every loop) → `<topic>/sensors`; spray events → `<topic>/spray` | Subscribe with any MQTT client for live dashboards. |
| **GeoJSON** | Point features for every spray nozzle activation | Import into QGIS, ArcGIS, etc. for post‑analysis. |

---

## 5  Chemical catalogue
200 common actives (herbicides, insecticides, fungicides) baked in. `apply_chemical` warns if a name isn’t recognised.

---

## 6  Development workflow
```bash
git clone https://github.com/yourname/farmdsl.git
cd farmdsl
python -m pip install -e .[dev]  # tooling, tests
pytest                          # all green?
```

### Formatting & linting
* **ruff**, **black**, **mypy**: `pre-commit install` hooks keep code clean.

---

## 7  Roadmap
* Spatial gating: only spray inside `ZONE` polygons (point‑in‑polygon).
* Runtime support for `INFER` and multi‑camera.
* Real sensor drivers (Modbus, CAN‑bus, RTK‑GPS).
* VS Code extension (syntax + snippets).

PRs & issues welcome — let’s automate agriculture together! 🌱
