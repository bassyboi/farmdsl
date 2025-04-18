"""farmdsl.py – Open Weed Locator‑first DSL runtime
====================================================
This edition wires the DSL directly into the **Open Weed Locator (OWL)** stack so that each loop can:
1. Grab a frame from the camera, run YOLO inference (Coral/RPi)
2. Expose `weed_bboxes`, `weed_density`, etc. as *sensor* keys usable in `WHEN` conditions
3. Map each bounding box to the correct boom nozzle and trigger spot‑spray actions.

New keywords?
-------------
_No extra syntax needed!_  We surface the detections as virtual sensors and add a new `spot_spray` action.

Example `.farm` script
----------------------
```farm
SETTINGS {
    model_path = "models/weed_v8.onnx",
    boom_width = 1.0m,
    nozzle_spacing = 0.25m,
    camera_id = 0
}

SCHEDULE every 3s
TASK infer_weeds    # capture + YOLO – automatic
WHEN weed_density > 0
THEN spot_spray
```
```
Run once for debugging:
```bash
farm run field_plan.farm --once
```
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
import sys
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import typer
from lark import Lark, Transformer, v_args
from pint import UnitRegistry
from rich.console import Console
from rich.table import Table

# --------------------------------------------------------------------------- #
# 🔧 Setup – globals & utilities
# --------------------------------------------------------------------------- #

ureg = UnitRegistry()
Q_ = ureg.Quantity
console = Console()
logger = logging.getLogger("farmdsl")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# --------------------------------------------------------------------------- #
# 🐍 Minimal OWL stub (replace with real import)
# --------------------------------------------------------------------------- #

class OWLInference:
    """Wrapper around Open Weed Locator model (YOLO).
    In production, import the real library; here we stub it."""

    def __init__(self, model_path: str, camera_id: int = 0):
        self.model_path = model_path
        self.camera_id = camera_id
        # TODO: load actual model and camera pipeline

    async def detect(self) -> List[Dict[str, Any]]:
        """Return list of bboxes: {x, y, w, h, confidence}"""
        # ✨ Stub – pretend we always see 2 weeds centred across boom
        return [
            {"cx": 0.25, "cy": 0.5, "w": 0.1, "h": 0.2, "conf": 0.9},
            {"cx": 0.75, "cy": 0.5, "w": 0.1, "h": 0.2, "conf": 0.85},
        ]

# --------------------------------------------------------------------------- #
# 📜 Grammar  (unchanged)
# --------------------------------------------------------------------------- #

GRAMMAR = r"""
    ?start: statement+

    statement: schedule_stmt
             | task_stmt
             | when_stmt
             | then_stmt
             | settings_block

    schedule_stmt: "SCHEDULE" TIME_SPEC                 -> schedule
    task_stmt    : "TASK" TASK_BODY                     -> task
    when_stmt    : "WHEN" BOOL_EXPR                     -> cond
    then_stmt    : "THEN" ACTION_BODY                   -> action
    settings_block: "SETTINGS" "{" setting_pair ("," setting_pair)* "}" -> settings

    setting_pair : NAME "=" VALUE                       -> setting

    TIME_SPEC    : /every\s+\d+[smhd]/i
    BOOL_EXPR    : /.+/
    TASK_BODY    : /.+/
    ACTION_BODY  : /.+/

    NAME  : /[a-zA-Z_][a-zA-Z0-9_]*/
    VALUE : /[^,}]+/

    %import common.WS
    %ignore WS
"""

# --------------------------------------------------------------------------- #
# 🏗️  AST dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class Schedule:
    every_seconds: int


@dataclass
class Task:
    body: str


@dataclass
class Condition:
    expr: str


@dataclass
class ActionNode:
    verb: str
    args: Tuple[str, ...]
    raw: str = field(repr=False)


@dataclass
class Settings:
    kv: Dict[str, Union[str, float, Q_]]

    def get(self, key: str, default: Any = None):
        return self.kv.get(key, default)


@dataclass
class Program:
    schedule: Schedule
    task: Task
    condition: Optional[Condition]
    action: ActionNode
    settings: Settings


# --------------------------------------------------------------------------- #
# 🔎 Parsing helpers
# --------------------------------------------------------------------------- #

action_re = re.compile(r"^(?P<verb>[a-zA-Z_]+)(?:\s+(?P<args>.+))?$")
unit_re = re.compile(r"^(?P<num>[0-9.]+)\s*(?P<unit>[a-zA-Z/%]+)$")


def _interval_to_seconds(token: str) -> int:
    _, num_unit = token.lower().split()
    num = int(num_unit[:-1])
    return num * {"s": 1, "m": 60, "h": 3600, "d": 86400}[num_unit[-1]]


def _parse_value(raw: str) -> Union[str, float, Q_]:
    raw = raw.strip()
    if m := unit_re.match(raw):
        return Q_(float(m.group("num")), m.group("unit"))
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_action(body: str) -> ActionNode:
    m = action_re.match(body.strip())
    if not m:
        raise ValueError(f"Invalid action syntax: {body}")
    verb = m.group("verb")
    args = tuple(m.group("args").split()) if m.group("args") else ()
    return ActionNode(verb, args, body)


# --------------------------------------------------------------------------- #
# 🚧 Transformer → AST (unchanged except settings parse)
# --------------------------------------------------------------------------- #

@v_args(inline=True)
class BuildAST(Transformer):
    def __init__(self):
        super().__init__()
        self._settings: Dict[str, Union[str, float, Q_]] = {}

    def schedule(self, time_spec):
        return Schedule(_interval_to_seconds(time_spec))

    def task(self, body):
        return Task(body.value.strip())

    def cond(self, expr):
        return Condition(expr.value.strip())

    def action(self, body):
        return _parse_action(body.value)

    def setting(self, name, value):
        self._settings[name.value.strip()] = _parse_value(value.value)

    def settings(self, *_pairs):
        return Settings(dict(self._settings))

    def start(self, *stmts):
        schedule = task = action = cond = settings = None
        for st in stmts:
            match st.__class__.__name__:
                case "Schedule":
                    schedule = st
                case "Task":
                    task = st
                case "ActionNode":
                    action = st
                case "Condition":
                    cond = st
                case "Settings":
                    settings = st
        if not (schedule and task and action):
            raise ValueError("Script must include SCHEDULE, TASK, THEN.")
        return Program(schedule, task, cond, action, settings or Settings({}))


def parse_script(text: str | Path) -> Program:
    text = Path(text).read_text() if isinstance(text, Path) else text
    tree = Lark(GRAMMAR, parser="lalr").parse(text)
    return BuildAST().transform(tree)

# --------------------------------------------------------------------------- #
# ⚙️  Action registry & hardware bridges
# --------------------------------------------------------------------------- #

action_registry: Dict[str, Callable[[Tuple[str, ...], "Runtime"], asyncio.Future]] = {}

def action(name: str):
    def _decorator(fn):
        action_registry[name] = fn
        return fn
    return _decorator


class SensorHub:
    """Aggregates primitive sensors plus OWL inference results."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._owl = OWLInference(
            model_path=str(settings.get("model_path", "weed.onnx")),
            camera_id=int(settings.get("camera_id", 0)),
        )
        self._cache: Dict[str, Any] = {}

    async def refresh(self):
        bboxes = await self._owl.detect()
        self._cache["weed_bboxes"] = bboxes
        # density = boxes per metre of boom width
        boom_w = self.settings.get("boom_width", Q_(1, "m"))
        self._cache["weed_density"] = len(bboxes) / boom_w.magnitude if boom_w else 0
        # demo raw sensors
        self._cache.update({"soil_moisture": 0.15, "temp": 18})

    async def read(self, key: str) -> Any:
        return self._cache.get(key)


class ActuatorHub:
    def __init__(self, settings: Settings):
        self.nozzle_spacing = settings.get("nozzle_spacing", Q_(0.25, "m"))
        self.boom_width = settings.get("boom_width", Q_(1.0, "m"))

    async def irrigate(self, depth: Q_):
        console.log(f"💧 Irrigating {depth}")

    async def spot_spray(self, bboxes: List[Dict[str, Any]]):
        for box in bboxes:
            nozzle_idx = self._bbox_to_nozzle(box["cx"])
            console.log(f"🚜 Spot‑spray nozzle {nozzle_idx} for bbox {box}")
        await asyncio.sleep(0.1)  # simulate latency

    def _bbox_to_nozzle(self, cx_norm: float) -> int:
        """Map bbox centre (0–1 across image) to nozzle index."""
        physical_x = cx_norm * self.boom_width.magnitude
        return int(math.floor(physical_x / self.nozzle_spacing.magnitude))


# Default actions
@action("irrigate")
async def _act_irrigate(args: Tuple[str, ...], rt: "Runtime") -> None:
    qty = Q_(float(args[0][:-2]), args[0][-2:]) if args else Q_(20, "mm")
    await rt.actuator.irrigate(qty)


@action("spot_spray")
async def _act_spot(args: Tuple[str, ...], rt: "Runtime") -> None:
    bboxes = rt.sensor._cache.get("weed_bboxes", [])
    if not bboxes:
        console.log("ℹ️  No weeds detected – skipping spray")
        return
    await rt.actuator.spot_spray(bboxes)


# --------------------------------------------------------------------------- #
# 🧠 Safe expression evaluator (unchanged)
# --------------------------------------------------------------------------- #

ALLOWED_NODES = {
    ast.Compare, ast.Name, ast.Constant, ast.Load, ast.And, ast.Or,
    ast.BoolOp, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq,
}


def eval_expr(expr: str, context: Dict[str, Any]) -> bool:
    tree = ast.parse(expr, mode="eval")
    if not all(isinstance(node, tuple(ALLOWED_NODES)) for node in ast.walk(tree)):
        raise ValueError("Disallowed expression components")
    return bool(eval(compile(tree, "<expr>", "eval"), {}, context))


# --------------------------------------------------------------------------- #
# ⏱️  Runtime
# --------------------------------------------------------------------------- #

class Runtime:
    def __init__(self, program: Program):
        self.program = program
        self.sensor = SensorHub(program.settings)
        self.actuator = ActuatorHub(program.settings)
        self.interval = program.schedule.every_seconds

    async def _collect_context(self) -> Dict[str, Any]:
        keys = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", self.program.condition.expr) if self.program.condition else []
        return {k: await self.sensor.read(k) for k in set(keys)}

    async def _condition_true(self) -> bool:
        if not self.program.condition:
            return True
        ctx = await self._collect_context()
        return eval_expr(self.program.condition.expr, ctx)

    async def _run_task(self):
        # Currently only one built‑in task: infer_weeds
        await self.sensor.refresh()
        logger.info(json.dumps({"event": "task", "weed_bboxes": self.sensor._cache.get("weed_bboxes")}))

    async def _run_action(self):
        handler = action_registry[self.program.action.verb]
        await handler(self.program.action.args, self)
        logger.info(json.dumps({"event": "action", "raw": self.program.action.raw}))

    async def run_once(self):
        await self._run_task()
        if await self._condition_true():
            await self._run_action()

    async def run_forever(self):
        console.log("▶️  Runtime started – Ctrl‑C to stop")
        try:
            while True:
                await self.run_once()
                await asyncio.sleep(self.interval)
        except KeyboardInterrupt:
            console.log("⏹️  Runtime stopped")


# --------------------------------------------------------------------------- #
# 🖥️  Typer CLI
# --------------------------------------------------------------------------- #

cli = typer.Typer(add_completion=False)


@cli.command()
def run(script: Path, once: bool = False):
    prog = parse_script(script)
    rt = Runtime(prog)
    asyncio.run(rt.run_once() if once else rt.run_forever())


@cli.command()
def validate(script: Path):
    try:
        parse_script(script)
        console.print("✅ Valid")
    except Exception as e:
        console.print(f"❌ {e}")
        raise typer.Exit(1)


@cli.command()
def docs():
    table = Table(title="FarmDSL + OWL cheat‑sheet")
    table.add_column("Sensor / Variable", style="bold green")
    table.add_column("Meaning")
    table.add_row("weed_density", "# bboxes per m of boom")
    table.add_row("weed_bboxes", "List[{cx,cy,w,h,conf}]")
    table.add_row("soil_moisture", "Stub sensor – replace")
    table.add_column("Action", style="bold blue")
    table.add_row("spot_spray", "Spray each detected bbox")
    console.print(table)


# --------------------------------------------------------------------------- #
# 🏃‍♂️  Entrypoint
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    cli()
