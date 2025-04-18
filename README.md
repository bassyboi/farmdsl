# FarmDSL 🌾 + Open Weed Locator (OWL)

Domain‑specific language runtime that turns weed detections into automated spot‑spray commands on your boom sprayer. This README walks you from **pip install** → **first spray** and shows how to extend the DSL for new sensors or actuators.

---

## 1  Install
```bash
# Python ≥ 3.9 on Raspberry Pi, Jetson, or laptop
python -m pip install farmdsl lark pint rich typer
```
> **Tip:** for Coral Edge TPU, make sure the OWL repo and its YOLO runtime are already set up; FarmDSL just imports it.

---

## 2  Create your first plan
`field_plan.farm`
```farm
SETTINGS {
    model_path    = "models/weed_v8.onnx",  # YOLO model
    boom_width    = 1.0m,                    # physical boom width
    nozzle_spacing = 0.25m,                  # distance between nozzles
    camera_id     = 0                        # /dev/video0
}

SCHEDULE every 3s              # loop rate
TASK infer_weeds               # capture + YOLO (built‑in)
WHEN weed_density > 0          # virtual sensor from OWL
THEN spot_spray                # smart actuation
```

Run once for debugging:
```bash
farm run field_plan.farm --once
```
Run forever (Ctrl‑C to stop):
```bash
farm run field_plan.farm
```

---

## 3  What you can reference in `WHEN`
| Variable         | Type           | Description                                      |
|------------------|----------------|--------------------------------------------------|
| `weed_density`   | `float`        | weeds per metre of boom                          |
| `weed_bboxes`    | `List[dict]`   | full YOLO bbox list                              |
| `soil_moisture`  | `float`        | stub — replace with real sensor                  |
| `temp`           | `float (°C)`   | stub — replace with real sensor                  |

Example complex condition:
```farm
WHEN weed_density > 2 or (soil_moisture < 0.12 and temp > 20)
```

---

## 4  Built‑in actions
| Action        | Syntax example              | Effect                                            |
|---------------|-----------------------------|---------------------------------------------------|
| `spot_spray`  | `spot_spray`                | Sprays each weed bbox with correct nozzle         |
| `irrigate`    | `irrigate 15mm`             | Applies water depth across whole boom             |

Add your own:
```python
from farmdsl import action

@action("fert_burst")
async def fert_burst(args, rt):
    rate = float(args[0]) if args else 5
    await rt.actuator.fertiliser(rate)
```
Then call in DSL:
```farm
THEN fert_burst 7
```

---

## 5  CLI cheat‑sheet
```bash
farm run <plan.farm>        # execute (Ctrl‑C to stop)
farm run <plan.farm> --once # single iteration
farm validate <plan.farm>   # syntax check only
farm docs                   # command & variable reference
```

---

## 6  Extending sensors
Override `SensorHub.refresh()` or subclass it to pull:
- Soil probes (Modbus)
- Weather API data
- GPS/GNSS heading for variable‑rate logic

Any key you insert into `self._cache` becomes usable in `WHEN` conditions.

---

## 7  Development & tests
```bash
git clone https://github.com/yourname/farmdsl.git
cd farmdsl
python -m pip install -e .[dev]  # pytest, ruff, black
pytest                           # all green?
```

---

## 8  Troubleshooting
| Symptom                          | Fix                                                        |
|----------------------------------|------------------------------------------------------------|
| `No handler for action`          | Misspelt verb or forgot `@action` annotation               |
| `weed_density` always 0          | Check camera feed, model path, and OWL install             |
| All nozzles fire same index      | Verify `boom_width` & `nozzle_spacing` settings            |

---

Made with ❤️ for weed‑free paddocks. PRs welcome!

