#!/usr/bin/env python3
"""Generate the AURA × ARISE Grafana dashboards.

Dashboards query the Orion-LD TROE tables in TimescaleDB directly (no helper
views), so they work on a bare ARISE stack. Run after editing:

    python3 fiware/grafana/build_dashboards.py

Output: fiware/grafana/dashboards/*.json (provisioned read-only into Grafana).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"

# Intent-event merge SQL, shared with the monitor (dependency-free module, loaded by path).
_spec = importlib.util.spec_from_file_location(
    "intent_sql", Path(__file__).resolve().parents[1] / "monitor" / "aura_monitor" / "intent_sql.py")
_intent_sql = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_intent_sql)
merged_intent_events = _intent_sql.merged_intent_events
CONSOLE = "http://localhost:8000/admin/aura"   # AURA dashboard in WireCloud
REPORTS_URL = "http://localhost:8090"          # aura-monitor serves printable mission reports

PG = {"type": "grafana-postgresql-datasource", "uid": "aura-timescale"}
INF = {"type": "yesoreyeram-infinity-datasource", "uid": "aura-mintaka"}

# One palette for every dashboard.
C = {
    "cyan": "#38bdf8",
    "teal": "#2dd4bf",
    "green": "#22c55e",
    "lime": "#a3e635",
    "amber": "#f59e0b",
    "orange": "#fb923c",
    "red": "#ef4444",
    "violet": "#a78bfa",
    "pink": "#f472b6",
    "blue": "#60a5fa",
    "slate": "#64748b",
}

# Robot-side attributes come from Orion-LD's native DDS bridge (ARISE D2.1, Phase I): each mapped ROS 2
# topic is one attribute whose value is the whole ROS message (compound). See fiware/orion/config-dds.json.
ROBOT = "urn:ngsi-ld:Robot:$robot"            # battery (BatteryState), odometry (Odometry), imu (Imu), status (ArtabanStatus)
AIR = "urn:ngsi-ld:AirQualityObserved:$robot"  # reading (EnvironmentReading)
INTENT = "urn:ngsi-ld:HRIEvent:intent"         # intentEvent (aura_interfaces/IntentEvent)
STT = "urn:ngsi-ld:HRIEvent:stt"               # transcript (std_msgs/String)
MODULE = "urn:ngsi-ld:HRIModuleStatus:"        # <module>: status (diagnostic_msgs/DiagnosticStatus)

T = "coalesce(observedat, ts)"  # event time: observedAt when present, else TROE insert time (DDS samples)
# Not $__timeFilter(T): Grafana splits macro arguments at commas, which breaks coalesce(...).
TF = f"{T} BETWEEN $__timeFrom() AND $__timeTo()"

# IntentEvent.decision is a uint8 constant.
DECISION_TEXT = ("CASE (compound->>'decision')::int WHEN 0 THEN 'ACCEPT' WHEN 1 THEN 'CONFIRM' "
                 "WHEN 2 THEN 'REJECT' WHEN 3 THEN 'IGNORE' END")


def j(path: str, cast: str = "float") -> str:
    """Field of a compound (ROS message) value, e.g. j('pose.pose.position.x')."""
    parts = path.split(".")
    expr = f"compound #>> '{{{','.join(parts)}}}'" if len(parts) > 1 else f"compound->>'{path}'"
    return f"({expr})::{cast}" if cast else f"({expr})"


def kv(key: str, cast: str = "") -> str:
    """One metric of a module status: aura_interfaces/ModuleStatus (keys[] + values[]), or the older
    diagnostic_msgs/DiagnosticStatus list of {key, value}."""
    flat = (f"(SELECT t.v FROM jsonb_array_elements_text(compound->'values') WITH ORDINALITY AS t(v, i) "
            f"WHERE compound->'keys'->>(t.i::int - 1) = '{key}')")
    legacy = f"(SELECT kv->>'value' FROM jsonb_array_elements(compound->'values') kv WHERE kv->>'key' = '{key}')"
    expr = f"COALESCE({flat}, {legacy})"
    return f"{expr}::{cast}" if cast else expr


def attr(name: str) -> str:
    """Match an attribute by short name regardless of how it was expanded."""
    return f"id LIKE '%/{name}'"


# --------------------------------------------------------------------------- SQL


def sql_series(entity: str, name: str, alias: str, column: str = "number") -> str:
    return (
        f'SELECT {T} AS "time", {column} AS "{alias}"\n'
        f"FROM attributes\n"
        f"WHERE entityid = '{entity}' AND {attr(name)} AND {TF}\n"
        f"ORDER BY 1"
    )


def sql_latest(entity: str, name: str, alias: str, column: str = "number") -> str:
    return (
        f'SELECT {column} AS "{alias}"\n'
        f"FROM attributes\n"
        f"WHERE entityid = '{entity}' AND {attr(name)}\n"
        f"ORDER BY ts DESC LIMIT 1"
    )


def sql_latest_like(entity_prefix: str, name: str, alias: str) -> str:
    return (
        f'SELECT number AS "{alias}"\n'
        f"FROM attributes\n"
        f"WHERE entityid LIKE '{entity_prefix}%' AND {attr(name)}\n"
        f"ORDER BY ts DESC LIMIT 1"
    )


def sql_jseries(entity: str, name: str, fields: dict[str, str]) -> str:
    """Time series of expressions over a compound attribute: {sql expression: alias}."""
    cols = ", ".join(f'{expr} AS "{alias}"' for expr, alias in fields.items())
    return (
        f'SELECT {T} AS "time", {cols}\n'
        f"FROM attributes\n"
        f"WHERE entityid = '{entity}' AND {attr(name)} AND {TF}\n"
        f"ORDER BY 1"
    )


def sql_jlatest(entity: str, name: str, expr: str, alias: str) -> str:
    """Latest value of an expression over a compound attribute."""
    return (
        f'SELECT {expr} AS "{alias}"\n'
        f"FROM attributes\n"
        f"WHERE entityid = '{entity}' AND {attr(name)}\n"
        f"ORDER BY ts DESC LIMIT 1"
    )


# One row per decision: the robot adapter re-publishes an IntentEvent (same event_id and stamp) to add a false
# activation or the time to safe stop; counting raw TROE rows would count that stop two or three times.
# The merge SQL is shared with the monitor's aura_intent_events view and inlined, so panels work without it.
EVENTS = (
    f"FROM ({merged_intent_events(attr('intentEvent'))}) AS intent_events\n"
    f"WHERE {TF}"
)
SUCCESS = "CASE WHEN compound->>'outcome' IN ('correct','recovered') THEN 1.0 ELSE 0.0 END"
DECIDED = "(compound->>'decision')::int <> 3"
CONFIRMED = "CASE WHEN (compound->>'decision')::int = 1 THEN 1.0 ELSE 0.0 END"


# --------------------------------------------------------------------------- panels


class Grid:
    """Left-to-right, top-to-bottom placement on Grafana's 24-column grid."""

    def __init__(self) -> None:
        self.x = 0
        self.y = 0
        self.row_h = 0
        self.ids = 0

    def place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.newline()
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def newline(self) -> None:
        if self.x:
            self.y += self.row_h
        self.x = 0
        self.row_h = 0

    def next_id(self) -> int:
        self.ids += 1
        return self.ids


def target(sql: str, ref: str = "A", fmt: str = "table") -> dict:
    return {
        "datasource": PG,
        "refId": ref,
        "editorMode": "code",
        "format": fmt,
        "rawQuery": True,
        "rawSql": sql,
    }


def thresholds(*steps: tuple[float | None, str]) -> dict:
    return {
        "mode": "absolute",
        "steps": [{"color": C.get(c, c), "value": v} for v, c in steps],
    }


def text_map(mapping: dict[str, tuple[str, str]]) -> list:
    """Value mapping: raw text -> (display text, palette colour)."""
    return [
        {
            "type": "value",
            "options": {
                k: {"text": t, "color": C.get(c, c), "index": i}
                for i, (k, (t, c)) in enumerate(mapping.items())
            },
        }
    ]


def color_override(name: str, color: str) -> dict:
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": C.get(color, color)}}],
    }


def panel(g: Grid, ptype: str, title: str, w: int, h: int, targets: list, *,
          desc: str = "", unit: str | None = None, decimals: int | None = None,
          th: dict | None = None, mappings: list | None = None, minv=None, maxv=None,
          overrides: list | None = None, options: dict | None = None,
          custom: dict | None = None, color: dict | None = None, ds=PG,
          transformations: list | None = None) -> dict:
    defaults: dict = {}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if th:
        defaults["thresholds"] = th
    if mappings:
        defaults["mappings"] = mappings
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    if custom:
        defaults["custom"] = custom
    if color:
        defaults["color"] = color
    p = {
        "id": g.next_id(),
        "type": ptype,
        "title": title,
        "description": desc,
        "datasource": ds,
        "gridPos": g.place(w, h),
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": options or {},
    }
    if transformations:
        p["transformations"] = transformations
    return p


def row(g: Grid, title: str) -> dict:
    g.newline()
    p = {"id": g.next_id(), "type": "row", "title": title, "collapsed": False,
         "gridPos": {"x": 0, "y": g.y, "w": 24, "h": 1}, "panels": []}
    g.y += 1
    return p


def text(g: Grid, w: int, h: int, content: str, title: str = "") -> dict:
    return {"id": g.next_id(), "type": "text", "title": title, "gridPos": g.place(w, h),
            "options": {"mode": "markdown", "content": content}, "transparent": True}


def stat_opts(text_mode: str = "value", graph: str = "area", color_mode: str = "background_solid",
              fields: str = "", values: bool = False) -> dict:
    return {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": fields, "values": values},
        "colorMode": color_mode,
        "graphMode": graph,
        "justifyMode": "center",
        "textMode": text_mode,
        "orientation": "auto",
        "wideLayout": True,
        "showPercentChange": False,
    }


def stat(g: Grid, title: str, sql: str, w: int = 4, h: int = 4, **kw) -> dict:
    # fields "/.*/" so text values (modes, states, ids) are shown, not only numbers
    opts = stat_opts(graph=kw.pop("graph", "none"), color_mode=kw.pop("color_mode", "background_solid"),
                     fields="/.*/")
    return panel(g, "stat", title, w, h, [target(sql)], options=opts, **kw)


def ts_custom(fill: int = 18, line: int = 2, draw: str = "line", points: str = "never",
              gradient: str = "opacity", interp: str = "smooth", axis: str = "auto",
              span: bool = True) -> dict:
    return {
        "drawStyle": draw,
        "lineInterpolation": interp,
        "lineWidth": line,
        "fillOpacity": fill,
        "gradientMode": gradient,
        "showPoints": points,
        "pointSize": 5,
        "spanNulls": span,
        "axisPlacement": axis,
        "axisBorderShow": False,
        "thresholdsStyle": {"mode": "off"},
    }


def timeseries(g: Grid, title: str, targets: list, w: int = 12, h: int = 8, *,
               colors: dict[str, str] | None = None, legend: str = "list", fill: int = 18,
               draw: str = "line", points: str = "never", interp: str = "smooth",
               threshold_style: str | None = None, extra_overrides: list | None = None, **kw) -> dict:
    custom = ts_custom(fill=fill, draw=draw, points=points, interp=interp)
    if threshold_style:
        custom["thresholdsStyle"] = {"mode": threshold_style}
    overrides = [color_override(n, c) for n, c in (colors or {}).items()] + (extra_overrides or [])
    opts = {
        "legend": {"displayMode": legend, "placement": "bottom", "showLegend": True,
                   "calcs": ["lastNotNull", "mean", "max"] if legend == "table" else []},
        "tooltip": {"mode": "multi", "sort": "desc"},
    }
    return panel(g, "timeseries", title, w, h, targets, custom=custom, overrides=overrides,
                 options=opts, color={"mode": "palette-classic"}, **kw)


def dashboard(uid: str, title: str, desc: str, panels: list, *, time_from: str = "now-30m",
              refresh: str = "5s", tags: list | None = None, templating: list | None = None) -> dict:
    return {
        "uid": uid,
        "title": title,
        "description": desc,
        "tags": ["aura", "arise", "fiware"] + (tags or []),
        "timezone": "browser",
        "editable": False,
        "graphTooltip": 1,
        "liveNow": False,
        "refresh": refresh,
        "schemaVersion": 39,
        "version": 1,
        "style": "dark",
        "time": {"from": time_from, "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m", "15m"]},
        "fiscalYearStartMonth": 0,
        "links": [
            {"title": "AURA dashboards", "type": "dashboards", "tags": ["aura"], "asDropdown": False,
             "includeVars": True, "keepTime": True, "icon": "external link", "targetBlank": False},
            {"title": "AURA dashboard (WireCloud)", "type": "link", "url": CONSOLE, "icon": "external link",
             "targetBlank": True, "tooltip": "AURA HRI condition-monitoring widgets in FIWARE WireCloud"},
            {"title": "Orion-LD", "type": "link", "url": "http://localhost:1026/ngsi-ld/v1/entities?local=true&limit=100",
             "icon": "info", "targetBlank": True, "tooltip": "NGSI-LD entities in the context broker"},
        ],
        "templating": {"list": templating if templating is not None else [robot_var()]},
        "annotations": {"list": [
            {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True,
             "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts",
             "type": "dashboard"},
        ]},
        "panels": panels,
    }


def robot_var() -> dict:
    return {
        "name": "robot", "label": "Robot", "type": "custom", "query": "artaban-01",
        "current": {"selected": True, "text": "artaban-01", "value": "artaban-01"},
        "options": [{"selected": True, "text": "artaban-01", "value": "artaban-01"}],
        "hide": 0, "includeAll": False, "multi": False,
        "description": "Robot id suffix used in NGSI-LD entity ids (urn:ngsi-ld:Robot:<robot>)",
    }


def header(g: Grid, subtitle: str) -> dict:
    return text(g, 24, 3, (
        f"### Artaban · AURA — {subtitle}\n"
        "`ROS 2 / Vulcanexus` → `Orion-LD (NGSI-LD, DDS bridge)` → `TROE · TimescaleDB` → "
        "`Mintaka` → **Grafana** &nbsp;·&nbsp; Powered by FIWARE · ARISE all-in-one middleware "
        f"&nbsp;·&nbsp; [Open AURA dashboard (WireCloud) ↗]({CONSOLE})"
    ))


MODE_MAP = text_map({
    "supervised": ("SUPERVISED", "cyan"), "teleop": ("TELEOP", "violet"),
    "autonomous": ("AUTONOMOUS", "green"), "safe-stop": ("SAFE-STOP", "red"),
    "docked": ("DOCKED", "slate"),
})
MISSION_MAP = text_map({
    "idle": ("IDLE", "slate"), "navigating": ("NAVIGATING", "cyan"), "inspecting": ("INSPECTING", "green"),
    "paused": ("PAUSED", "amber"), "returning": ("RETURNING", "blue"), "charging": ("CHARGING", "teal"),
    "safe-stop": ("SAFE-STOP", "red"),
})
GAIT_MAP = text_map({
    "stand": ("STAND", "slate"), "walk": ("WALK", "cyan"), "trot": ("TROT", "green"),
    "stairs": ("STAIRS", "violet"), "sit": ("SIT", "slate"),
})
SAFETY_MAP = text_map({
    "nominal": ("NOMINAL", "green"), "warning": ("WARNING", "amber"),
    "protective-stop": ("PROTECTIVE STOP", "red"),
})
ENGAGE_MAP = text_map({
    "ENGAGED": ("ENGAGED", "green"), "ATTENDING": ("ATTENDING", "amber"), "DISENGAGED": ("DISENGAGED", "slate"),
})
RECOG_MAP = text_map({
    "acoustic": ("acoustic", "cyan"), "acoustic+thermal": ("acoustic + thermal", "teal"),
    "fused": ("fused", "violet"), "gesture-fallback": ("gesture fallback", "amber"),
})
COND_MAP = text_map({
    "clean": ("clean", "green"), "noise": ("noise > 75 dB", "amber"), "mask": ("full-face mask", "orange"),
    "noise+mask": ("noise + mask", "red"),
})
DECISION_MAP = text_map({
    "ACCEPT": ("ACCEPT", "green"), "CONFIRM": ("CONFIRM", "amber"), "REJECT": ("REJECT", "red"),
    "IGNORE": ("IGNORE", "slate"),
})
OUTCOME_MAP = text_map({
    "correct": ("correct", "green"), "recovered": ("recovered", "teal"), "misrecognized": ("misrecognized", "orange"),
    "false_activation": ("false activation", "red"), "rejected": ("rejected", "slate"),
})
SEVERITY_MAP = text_map({
    "critical": ("CRITICAL", "red"), "high": ("HIGH", "orange"), "medium": ("MEDIUM", "amber"),
    "low": ("LOW", "blue"), "informational": ("INFO", "slate"),
})
STATUS_MAP = text_map({
    "active": ("active", "red"), "acknowledged": ("acknowledged", "amber"), "resolved": ("resolved", "green"),
})
VERDICT_MAP = text_map({"pass": ("PASS", "green"), "watch": ("WATCH", "amber"), "fail": ("FAIL", "red")})


def cell_color(name: str, mappings: list, mode: str = "color-background") -> dict:
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [
            {"id": "mappings", "value": mappings},
            {"id": "custom.cellOptions", "value": {"type": mode, "mode": "basic"}},
        ],
    }


def cell_gauge(name: str, unit: str, th: dict, minv=0, maxv=100) -> dict:
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [
            {"id": "unit", "value": unit},
            {"id": "min", "value": minv},
            {"id": "max", "value": maxv},
            {"id": "thresholds", "value": th},
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "lcd", "valueDisplayMode": "text"}},
        ],
    }


def cell_props(name: str, **props) -> dict:
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in props.items()]}


# Thresholds shared with the aura-monitor alert rules (docs/DATA_MODEL.md).
TH_SUCCESS = thresholds((None, "red"), (80, "amber"), (90, "green"))
TH_FALSE_ACT = thresholds((None, "green"), (3, "amber"), (5, "red"))
TH_REJECT = thresholds((None, "green"), (7, "amber"), (10, "red"))
TH_LOOPS = thresholds((None, "green"), (1.5, "amber"), (2.0, "red"))
TH_LAT = thresholds((None, "green"), (450, "amber"), (600, "red"))
TH_CONF = thresholds((None, "red"), (0.7, "amber"), (0.8, "green"))
TH_ECE = thresholds((None, "green"), (0.05, "amber"), (0.1, "red"))
TH_TTSS = thresholds((None, "green"), (800, "amber"), (1200, "red"))
TH_BATT = thresholds((None, "red"), (12, "orange"), (25, "amber"), (40, "green"))
TH_JOINT = thresholds((None, "teal"), (55, "amber"), (70, "red"))


# =========================================================================== 01 telemetry


def telemetry() -> dict:
    g = Grid()
    P: list = [header(g, "Robot telemetry")]
    status = lambda f, cast="float": j(f, cast)

    P.append(row(g, "Status (/artaban/status · /battery_state)"))
    P.append(panel(g, "gauge", "Battery", 4, 7,
                   [target(sql_jlatest(ROBOT, "battery", f"100 * {j('percentage')}", "Battery"))],
                   unit="percent", decimals=0, minv=0, maxv=100, th=TH_BATT,
                   desc="State of charge: Robot.battery (sensor_msgs/BatteryState).percentage",
                   options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                            "showThresholdLabels": False, "showThresholdMarkers": True, "minVizHeight": 75,
                            "sizing": "auto"}))
    for title, field, mapping in [("Operation mode", "operation_mode", MODE_MAP), ("Mission state", "mission_state", MISSION_MAP),
                                  ("Gait", "gait", GAIT_MAP), ("Safety", "safety_state", SAFETY_MAP)]:
        P.append(stat(g, title, sql_jlatest(ROBOT, "status", status(field, ""), field), w=5, h=3, mappings=mapping,
                      th=thresholds((None, "slate"))))
    # second line of the status block (to the right of the gauge)
    g.x, g.y, g.row_h = 4, g.y + 3, 4
    P.append(stat(g, "Mission", sql_jlatest(ROBOT, "status", status("mission_id", ""), "mission"), w=4, h=4,
                  th=thresholds((None, "blue")), color_mode="value"))
    P.append(panel(g, "bargauge", "Mission progress", 4, 4,
                   [target(sql_jlatest(ROBOT, "status", status("mission_progress"), "Progress"))], unit="percent",
                   minv=0, maxv=100, decimals=0, th=thresholds((None, "cyan")),
                   options={"displayMode": "lcd", "orientation": "horizontal", "showUnfilled": True,
                            "valueMode": "color", "namePlacement": "hidden",
                            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}))
    P.append(stat(g, "Waypoint", sql_jlatest(ROBOT, "status", status("current_waypoint", ""), "wp"), w=6, h=4,
                  th=thresholds((None, "violet")), color_mode="value"))
    P.append(stat(g, "E-stop", sql_jlatest(
        ROBOT, "status", f"CASE WHEN {status('e_stop', 'boolean')} THEN 'ENGAGED' ELSE 'released' END", "E-stop"),
        w=6, h=4, th=thresholds((None, "slate")),
        mappings=text_map({"ENGAGED": ("ENGAGED", "red"), "released": ("released", "green")})))

    P.append(row(g, "Power (/battery_state · sensor_msgs/BatteryState)"))
    P.append(timeseries(g, "Battery state of charge", [target(
        sql_jseries(ROBOT, "battery", {f"100 * {j('percentage')}": "SoC"}), fmt="time_series")],
        w=8, h=8, unit="percent", minv=0, maxv=100, th=TH_BATT, threshold_style="area",
        colors={"SoC": "green"}, desc="BatteryState.percentage; shaded bands follow the batteryLow alert thresholds"))
    P.append(timeseries(g, "Voltage · current", [target(
        sql_jseries(ROBOT, "battery", {j("voltage"): "Voltage", j("current"): "Current"}), fmt="time_series")],
        w=8, h=8, colors={"Voltage": "cyan", "Current": "violet"}, fill=6,
        extra_overrides=[cell_props("Voltage", unit="volt"),
                         cell_props("Current", unit="amp", custom__axisPlacement="right")]))
    P.append(timeseries(g, "Power draw · battery temperature", [target(
        sql_jseries(ROBOT, "battery", {f"{j('voltage')} * {j('current')}": "Power", j("temperature"): "Battery temp"}),
        fmt="time_series")],
        w=8, h=8, colors={"Power": "amber", "Battery temp": "red"}, fill=10,
        desc="Power = voltage × current (negative while charging)",
        extra_overrides=[cell_props("Power", unit="watt"),
                         cell_props("Battery temp", unit="celsius", custom__axisPlacement="right",
                                    custom__fillOpacity=0)]))

    P.append(row(g, "Locomotion (/odom · /imu/data)"))
    P.append(panel(g, "xychart", "Position trail · site map (60 × 40 m)", 8, 10, [target(
        f"SELECT {j('pose.pose.position.x')} AS \"x (m)\", {j('pose.pose.position.y')} AS \"y (m)\"\n"
        f"FROM attributes\nWHERE entityid = '{ROBOT}' AND {attr('odometry')} AND {TF}\nORDER BY ts")],
        desc="Odometry pose (nav_msgs/Odometry) in the site frame over the selected time range",
        custom={"show": "points+lines", "pointSize": {"fixed": 4}, "lineWidth": 1,
                "fillOpacity": 40, "axisPlacement": "auto"},
        color={"mode": "fixed", "fixedColor": C["cyan"]},
        overrides=[cell_props("x (m)", min=0, max=60), cell_props("y (m)", min=0, max=40)],
        options={"mapping": "auto", "series": [{}], "legend": {"showLegend": False},
                 "tooltip": {"mode": "single"}}))
    P.append(timeseries(g, "Speed", [target(
        sql_jseries(ROBOT, "odometry", {j("twist.twist.linear.x"): "Speed"}), fmt="time_series")],
        w=8, h=5, unit="velocityms", decimals=2, colors={"Speed": "cyan"},
        desc="Odometry twist.twist.linear.x"))
    q = {k: j(f"orientation.{k}") for k in "xyzw"}
    roll = f"degrees(atan2(2 * ({q['w']} * {q['x']} + {q['y']} * {q['z']}), 1 - 2 * ({q['x']}^2 + {q['y']}^2)))"
    pitch = f"degrees(asin(greatest(-1, least(1, 2 * ({q['w']} * {q['y']} - {q['z']} * {q['x']})))))"
    P.append(timeseries(g, "IMU roll · pitch", [target(
        sql_jseries(ROBOT, "imu", {roll: "Roll", pitch: "Pitch"}), fmt="time_series")],
        w=8, h=5, unit="degree", decimals=1, colors={"Roll": "violet", "Pitch": "pink"}, fill=0,
        desc="From the sensor_msgs/Imu orientation quaternion"))
    g.x, g.y = 8, g.y + 5
    P.append(timeseries(g, "Odometry (mission)", [target(
        sql_jseries(ROBOT, "status", {status("odometry_m"): "Distance"}), fmt="time_series")],
        w=8, h=5, unit="lengthm", decimals=0, colors={"Distance": "teal"}, interp="linear"))
    P.append(stat(g, "Distance walked", sql_jlatest(ROBOT, "status", status("odometry_m"), "m"), w=4, h=5,
                  unit="lengthm", decimals=0, th=thresholds((None, "teal")), color_mode="value", graph="none"))
    P.append(stat(g, "Speed now", sql_jlatest(ROBOT, "odometry", j("twist.twist.linear.x"), "m/s"), w=4, h=5,
                  unit="velocityms", decimals=2, th=thresholds((None, "cyan")), color_mode="value"))

    P.append(row(g, "Actuators · 12 joints (ArtabanStatus.joint_temperatures)"))
    legs = ["FL", "FR", "RL", "RR"]
    parts = ["hip", "thigh", "calf"]
    cols = ",\n  ".join(f"max(t) FILTER (WHERE n = '{l}_{p}') AS \"{l} {p}\"" for l in legs for p in parts)
    P.append(panel(g, "bargauge", "Joint temperatures (latest)", 12, 11, [target(
        "WITH s AS (\n"
        f"  SELECT compound FROM attributes WHERE entityid = '{ROBOT}' AND {attr('status')} ORDER BY ts DESC LIMIT 1\n"
        "), z AS (\n"
        "  SELECT name.value AS n, (temp.value #>> '{}')::float AS t\n"
        "  FROM s, jsonb_array_elements_text(s.compound->'joint_names') WITH ORDINALITY AS name(value, i)\n"
        "  JOIN jsonb_array_elements(s.compound->'joint_temperatures') WITH ORDINALITY AS temp(value, i) USING (i)\n"
        ")\n"
        f"SELECT {cols}\nFROM z")],
        desc="joint_names zipped with joint_temperatures — FL/FR/RL/RR legs × hip/thigh/calf actuators. "
             "Red > 70 °C triggers jointOverTemp.",
        unit="celsius", decimals=1, minv=20, maxv=90, th=TH_JOINT,
        options={"displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
                 "valueMode": "color", "namePlacement": "left", "sizing": "manual", "minVizHeight": 14,
                 "maxVizHeight": 22,
                 "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}))
    P.append(timeseries(g, "Hottest joint", [target(
        sql_jseries(ROBOT, "status", {status("max_joint_temperature"): "Max joint temp"}), fmt="time_series")],
        w=12, h=11, unit="celsius", decimals=1, th=TH_JOINT, threshold_style="line+area",
        colors={"Max joint temp": "orange"}))

    P.append(row(g, "Edge compute · comms"))
    P.append(timeseries(g, "Edge computer load", [target(sql_jseries(ROBOT, "status", {
        status("cpu_load"): "CPU", status("gpu_load"): "GPU", status("memory_usage"): "Memory"}), fmt="time_series")],
        w=8, h=8, unit="percent", minv=0, maxv=100, fill=8,
        colors={"CPU": "cyan", "GPU": "violet", "Memory": "slate"},
        desc="Edge AI computer running the AURA acoustic skill (ONNX/TensorRT)"))
    P.append(timeseries(g, "SoC temperature", [target(
        sql_jseries(ROBOT, "status", {status("soc_temperature"): "SoC temp"}), fmt="time_series")],
        w=4, h=8, unit="celsius", th=thresholds((None, "green"), (75, "amber"), (85, "red")),
        threshold_style="line", colors={"SoC temp": "amber"}))
    P.append(timeseries(g, "Link quality", [target(sql_jseries(ROBOT, "status", {
        status("link_latency_ms"): "Latency", status("packet_loss"): "Packet loss"}), fmt="time_series")],
        w=8, h=8, fill=8, colors={"Latency": "blue", "Packet loss": "red"},
        extra_overrides=[cell_props("Latency", unit="ms"),
                         cell_props("Packet loss", unit="percent", custom__axisPlacement="right")],
        desc="commsDegraded fires at packet loss > 5 % or latency > 250 ms"))
    P.append(stat(g, "Wi-Fi RSSI", sql_jlatest(ROBOT, "status", status("wifi_rssi"), "dBm"), w=4, h=8, unit="dBm",
                  decimals=0, th=thresholds((None, "red"), (-75, "amber"), (-65, "green")), graph="area"))

    P.append(row(g, "Environment · hazardous-site sensing (/artaban/environment)"))
    P.append(timeseries(g, "Noise level (LAeq)", [target(
        sql_jseries(AIR, "reading", {j("la_eq"): "LAeq", j("la_max"): "LAmax"}), fmt="time_series")],
        w=8, h=8, unit="dB", decimals=1, colors={"LAeq": "amber", "LAmax": "slate"}, fill=12,
        th=thresholds((None, "green"), (75, "amber"), (85, "red")), threshold_style="line",
        desc="EnvironmentReading.la_eq. > 75 dB degrades acoustic recognition; > 85 dB raises noiseHazard"))
    P.append(timeseries(g, "Gas (H₂S · CO · CH₄)", [target(sql_jseries(AIR, "reading", {
        j("h2s_ppm"): "H₂S ppm", j("co_ppm"): "CO ppm", j("ch4_lel"): "CH₄ %LEL"}), fmt="time_series")],
        w=8, h=8, fill=6, colors={"H₂S ppm": "red", "CO ppm": "amber", "CH₄ %LEL": "violet"},
        th=thresholds((None, "transparent"), (10, "red")), threshold_style="line",
        desc="AirQualityObserved.reading. gasDetected (critical) when H₂S > 10 ppm or CH₄ > 10 %LEL"))
    P.append(stat(g, "O₂", sql_jlatest(AIR, "reading", j("o2_vol"), "O₂"), w=4, h=4, unit="percent", decimals=1,
                  th=thresholds((None, "red"), (19.5, "green"), (23.5, "red")), graph="area"))
    P.append(stat(g, "Thermal hotspot", sql_jlatest(AIR, "reading", j("thermal_max"), "°C"), w=4, h=4,
                  unit="celsius", decimals=1, th=thresholds((None, "green"), (60, "amber"), (90, "red")), graph="area"))
    g.x, g.y = 16, g.y + 4
    P.append(stat(g, "Ambient", sql_jlatest(AIR, "reading", j("temperature"), "°C"), w=4, h=4, unit="celsius",
                  decimals=1, th=thresholds((None, "cyan")), color_mode="value", graph="area"))
    P.append(stat(g, "Humidity", sql_jlatest(AIR, "reading", j("relative_humidity"), "%"), w=4, h=4, unit="percent",
                  decimals=0, th=thresholds((None, "blue")), color_mode="value", graph="area"))

    return dashboard("artaban-telemetry", "Artaban · Robot telemetry",
                     "Live telemetry of the Artaban quadruped: ROS 2 topics bridged into Orion-LD (NGSI-LD) by its "
                     "native DDS support, kept by the TROE historian.",
                     P, tags=["telemetry"])


# =========================================================================== 02 HRI


def hri() -> dict:
    g = Grid()
    P: list = [header(g, "HRI condition monitoring")]
    S = "urn:ngsi-ld:HRISession:"

    P.append(row(g, "Session KPIs (latest HRISession computed by aura-monitor)"))
    kpis = [
        ("Command success", "commandSuccessRate", "percent", 1, TH_SUCCESS),
        ("False activations", "falseActivationRate", "percent", 1, TH_FALSE_ACT),
        ("Rejection (5 min)", "windowRejectionRate", "percent", 1, TH_REJECT),
        ("Confirm loops / 10", "confirmationLoopRate", "none", 2, TH_LOOPS),
        ("p95 latency", "p95Latency", "ms", 0, TH_LAT),
        ("Mean confidence", "meanConfidence", "none", 2, TH_CONF),
    ]
    for title, name, unit, dec, th in kpis:
        P.append(panel(g, "stat", title, 4, 4, [target(sql_latest_like(S, name, title))], unit=unit, decimals=dec,
                       th=th, options=stat_opts(graph="none")))
    kpis2 = [
        ("Commands", "commandCount", "none", 0, thresholds((None, "blue"))),
        ("Rejection (session)", "rejectionRate", "percent", 1, TH_REJECT),
        ("Mean latency", "meanLatency", "ms", 0, TH_LAT),
        ("Time-to-safe-stop", "timeToSafeStop", "ms", 0, TH_TTSS),
        ("Operator interventions", "operatorInterventions", "none", 0, thresholds((None, "violet"))),
        ("Calibration error (ECE)", "calibrationError", "none", 3, TH_ECE),
    ]
    for title, name, unit, dec, th in kpis2:
        P.append(panel(g, "stat", title, 4, 3, [target(sql_latest_like(S, name, title))], unit=unit, decimals=dec,
                       th=th, options=stat_opts(graph="none", color_mode="value")))

    P.append(row(g, "Interaction state (HRIModuleStatus:intent · diagnostic_msgs/DiagnosticStatus)"))
    NODE = MODULE + "intent"
    P.append(stat(g, "Engagement", sql_jlatest(NODE, "status", kv("engagement"), "e"), w=4, h=3, mappings=ENGAGE_MAP,
                  th=thresholds((None, "slate"))))
    P.append(stat(g, "Recognition mode", sql_jlatest(NODE, "status", kv("recognition_mode"), "m"), w=5, h=3,
                  mappings=RECOG_MAP, th=thresholds((None, "slate"))))
    P.append(stat(g, "Acoustic condition", sql_jlatest(NODE, "status", kv("acoustic_condition"), "c"), w=5, h=3,
                  mappings=COND_MAP, th=thresholds((None, "slate"))))
    P.append(stat(g, "Acceptance threshold", sql_jlatest(NODE, "status", kv("accept_threshold", "float"), "t"), w=3, h=3,
                  decimals=2, th=thresholds((None, "cyan")), color_mode="value"))
    P.append(stat(g, "Operator", sql_jlatest(NODE, "status", kv("operator_id"), "o"), w=3, h=3,
                  th=thresholds((None, "violet")), color_mode="value"))
    P.append(stat(g, "Last transcript", sql_jlatest(STT, "transcript", j("data", ""), "t"), w=4, h=3,
                  th=thresholds((None, "blue")), color_mode="value", desc="/aura_transcript (aura_stt)"))
    P.append(panel(g, "state-timeline", "Engagement · recognition mode · acoustic condition", 16, 6, [
        target(sql_jseries(NODE, "status", {kv("engagement"): "Engagement"}), "A"),
        target(sql_jseries(NODE, "status", {kv("recognition_mode"): "Recognition"}), "B"),
        target(sql_jseries(NODE, "status", {kv("acoustic_condition"): "Condition"}), "C"),
    ], mappings=ENGAGE_MAP[0:1] + RECOG_MAP + COND_MAP, th=thresholds((None, "slate")),
        custom={"fillOpacity": 80, "lineWidth": 0},
        options={"mergeValues": True, "showValue": "auto", "alignValue": "left", "rowHeight": 0.85,
                 "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}))
    P.append(panel(g, "table", "Module health", 8, 6, [target(
        "SELECT DISTINCT ON (entityid) split_part(entityid, ':', 4) AS \"Module\",\n"
        "  CASE (compound->>'level')::int WHEN 0 THEN 'OK' WHEN 1 THEN 'WARN' WHEN 2 THEN 'ERROR' ELSE 'STALE' END AS \"Health\",\n"
        f"  {kv('rate', 'float')} AS \"Rate\", {kv('rate_unit')} AS \"Unit\", {kv('latency_ms', 'float')} AS \"Latency\"\n"
        f"FROM attributes WHERE entityid LIKE '{MODULE}%' AND {attr('status')}\n"
        "ORDER BY entityid, ts DESC")],
        desc="Latest DiagnosticStatus per AURA module (/aura/<module>/status)",
        custom={"align": "auto", "cellOptions": {"type": "auto"}},
        overrides=[cell_color("Health", text_map({"OK": ("OK", "green"), "WARN": ("WARN", "amber"),
                                                    "ERROR": ("ERROR", "red"), "STALE": ("STALE", "slate")})),
                   cell_props("Rate", decimals=2), cell_props("Latency", unit="ms", decimals=0)],
        options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}))

    P.append(row(g, "Live HRI event stream (HRIEvent:intent · /aura/intent/event)"))
    P.append(panel(g, "table", "Intent events", 16, 12, [target(
        f"SELECT {T} AS \"Time\", compound->>'intent' AS \"Intent\", compound->>'utterance' AS \"Utterance\",\n"
        f"  {DECISION_TEXT} AS \"Decision\", compound->>'outcome' AS \"Outcome\",\n"
        f"  {j('confidence')} AS \"Confidence\", {j('latency_ms')} AS \"Latency\",\n"
        "  compound->>'modality' AS \"Modality\", compound->>'acoustic_condition' AS \"Condition\",\n"
        "  compound->>'operator_id' AS \"Operator\"\n"
        f"{EVENTS}\nORDER BY ts DESC LIMIT 200")],
        desc="Every decision of the confidence-aware interaction policy (aura_interfaces/IntentEvent). Kept by TROE as event history.",
        custom={"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False, "filterable": True},
        overrides=[
            cell_color("Decision", DECISION_MAP),
            cell_color("Outcome", OUTCOME_MAP, "color-text"),
            cell_color("Condition", COND_MAP, "color-text"),
            cell_gauge("Confidence", "percentunit", TH_CONF, 0, 1),
            cell_props("Latency", unit="ms", decimals=0, thresholds=TH_LAT,
                       custom__cellOptions={"type": "color-text"}),
            cell_props("Time", custom__width=90, unit="time:HH:mm:ss"),
            cell_props("Utterance", custom__minWidth=160),
        ],
        options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                 "sortBy": [{"displayName": "Time", "desc": True}]}))
    P.append(panel(g, "piechart", "Decisions", 8, 6, [target(
        "SELECT count(*) FILTER (WHERE (compound->>'decision')::int = 0) AS \"ACCEPT\",\n"
        "  count(*) FILTER (WHERE (compound->>'decision')::int = 1) AS \"CONFIRM\",\n"
        "  count(*) FILTER (WHERE (compound->>'decision')::int = 2) AS \"REJECT\",\n"
        "  count(*) FILTER (WHERE (compound->>'decision')::int = 3) AS \"IGNORE\"\n" + EVENTS)],
        overrides=[color_override("ACCEPT", "green"), color_override("CONFIRM", "amber"),
                   color_override("REJECT", "red"), color_override("IGNORE", "slate")],
        options={"pieType": "donut", "displayLabels": ["percent"],
                 "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                            "values": ["value", "percent"]},
                 "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                 "tooltip": {"mode": "single"}}))
    P.append(panel(g, "piechart", "Modality", 8, 6, [target(
        "SELECT count(*) FILTER (WHERE compound->>'modality'='voice') AS \"voice\",\n"
        "  count(*) FILTER (WHERE compound->>'modality'='gesture') AS \"gesture\",\n"
        "  count(*) FILTER (WHERE compound->>'modality'='fused') AS \"fused\"\n" + EVENTS)],
        overrides=[color_override("voice", "cyan"), color_override("gesture", "amber"),
                   color_override("fused", "violet")],
        options={"pieType": "donut", "displayLabels": ["percent"],
                 "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                            "values": ["value", "percent"]},
                 "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                 "tooltip": {"mode": "single"}}))

    P.append(row(g, "Recognition quality"))
    P.append(timeseries(g, "Noise vs acoustic confidence", [
        target(sql_jseries(AIR, "reading", {j("la_eq"): "Noise LAeq"}), "A", "time_series"),
        target(f"SELECT {T} AS \"time\", {j('confidence')} AS \"Confidence\"\n{EVENTS}\nORDER BY 1",
               "B", "time_series"),
    ], w=12, h=9, fill=10, colors={"Noise LAeq": "amber", "Confidence": "cyan"},
        extra_overrides=[
            cell_props("Noise LAeq", unit="dB"),
            cell_props("Confidence", unit="percentunit", min=0, max=1, custom__axisPlacement="right",
                       custom__drawStyle="points", custom__pointSize=7, custom__fillOpacity=0),
        ], desc="Each dot is one intent event. Confidence drops as LAeq rises above ~75 dB."))
    P.append(panel(g, "xychart", "Confidence vs latency", 12, 9, [target(
        f"SELECT {j('confidence')} AS \"Confidence\", {j('latency_ms')} AS \"Latency (ms)\"\n"
        f"{EVENTS}")],
        custom={"show": "points", "pointSize": {"fixed": 6}, "fillOpacity": 60},
        color={"mode": "fixed", "fixedColor": C["violet"]},
        overrides=[cell_props("Confidence", min=0, max=1), cell_props("Latency (ms)", unit="ms")],
        options={"mapping": "auto", "series": [{}], "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}))

    P.append(row(g, "KPI trends"))
    P.append(timeseries(g, "Success · rejection · false activation", [
        target(sql_series_like(S, "commandSuccessRate", "Success"), "A", "time_series"),
        target(sql_series_like(S, "windowRejectionRate", "Rejection (5 min)"), "B", "time_series"),
        target(sql_series_like(S, "falseActivationRate", "False activation"), "C", "time_series"),
    ], w=12, h=8, unit="percent", minv=0, maxv=100, fill=8, interp="stepAfter",
        colors={"Success": "green", "Rejection (5 min)": "red", "False activation": "orange"}))
    P.append(timeseries(g, "Latency (mean · p95)", [
        target(sql_series_like(S, "meanLatency", "Mean"), "A", "time_series"),
        target(sql_series_like(S, "p95Latency", "p95"), "B", "time_series"),
    ], w=12, h=8, unit="ms", fill=8, interp="stepAfter", colors={"Mean": "cyan", "p95": "violet"},
        th=thresholds((None, "transparent"), (600, "red")), threshold_style="line"))

    return dashboard("aura-hri", "AURA · HRI condition monitoring",
                     "Real-time KPIs of the AURA intent skill on Artaban (ARISE CH5): ROS 2 HRI topics via the "
                     "Orion-LD DDS bridge, KPIs computed by aura-monitor.",
                     P, tags=["hri"])


def sql_series_like(entity_prefix: str, name: str, alias: str) -> str:
    return (
        f'SELECT {T} AS "time", number AS "{alias}"\n'
        f"FROM attributes\n"
        f"WHERE entityid LIKE '{entity_prefix}%' AND {attr(name)} AND {TF}\n"
        f"ORDER BY 1"
    )


# =========================================================================== 03 historian


REPORTS = (
    "WITH r AS (\n"
    "  SELECT DISTINCT ON (entityid, id) entityid, split_part(id, '/', -1) AS attr, number, text, boolean,\n"
    f"         {T} AS t\n"
    "  FROM attributes\n"
    "  WHERE entityid LIKE 'urn:ngsi-ld:MissionReport:%'\n"
    "  ORDER BY entityid, id, ts DESC\n"
    ")\n"
)


def rnum(name: str, alias: str) -> str:
    return f"max(number) FILTER (WHERE attr = '{name}') AS \"{alias}\""


def rtext(name: str, alias: str) -> str:
    return f"max(text) FILTER (WHERE attr = '{name}') AS \"{alias}\""


def historian() -> dict:
    g = Grid()
    P: list = [header(g, "Historian & analytics")]

    P.append(row(g, "Fleet of missions (MissionReport entities)"))
    summary = (
        REPORTS +
        "SELECT count(*) AS \"Missions\", avg(sr) AS \"Avg success\", avg(fa) AS \"Avg false activation\",\n"
        "  avg(lat) AS \"Avg latency\", avg(loops) AS \"Loops / 10 cmds\", sum(alerts) AS \"Alerts\"\n"
        "FROM (SELECT entityid, max(t) AS t,\n"
        f"  {rnum('commandSuccessRate', 'sr')}, {rnum('falseActivationRate', 'fa')},\n"
        f"  {rnum('meanLatency', 'lat')}, {rnum('confirmationLoopRate', 'loops')}, {rnum('alertsCount', 'alerts')}\n"
        "  FROM r GROUP BY entityid) m\nWHERE $__timeFilter(t)"
    )
    cells = [("Missions", "none", 0, thresholds((None, "blue"))),
             ("Avg success", "percent", 1, TH_SUCCESS),
             ("Avg false activation", "percent", 1, TH_FALSE_ACT),
             ("Avg latency", "ms", 0, TH_LAT),
             ("Loops / 10 cmds", "none", 2, TH_LOOPS),
             ("Alerts", "none", 0, thresholds((None, "slate")))]
    for name, unit, dec, th in cells:
        P.append(panel(g, "stat", name, 4, 4, [target(summary)], unit=unit, decimals=dec, th=th,
                       options=stat_opts(graph="none", color_mode="value", fields=f"/^{name}$/")))

    per_mission = (
        REPORTS +
        "SELECT split_part(entityid, ':', 4) AS \"Mission\",\n"
        f"  {rnum('commandSuccessRate', 'Success %')}, {rnum('rejectionRate', 'Rejection %')},\n"
        f"  {rnum('falseActivationRate', 'False activation %')}, {rnum('confirmationLoopRate', 'Loops / 10')},\n"
        f"  {rnum('meanLatency', 'Mean latency')}, {rnum('p95Latency', 'p95 latency')},\n"
        f"  {rnum('timeToSafeStop', 'Time-to-safe-stop')}, max(t) AS t\n"
        "FROM r WHERE entityid IN (SELECT entityid FROM r GROUP BY entityid HAVING max(t) >= $__timeFrom())\n"
        "GROUP BY entityid ORDER BY t"
    )

    def mission_bars(title: str, fields: list[str], unit: str, colors: dict, w: int = 12, **kw) -> dict:
        excl = {"t": True}
        all_fields = ["Success %", "Rejection %", "False activation %", "Loops / 10", "Mean latency",
                      "p95 latency", "Time-to-safe-stop"]
        for f in all_fields:
            if f not in fields:
                excl[f] = True
        return panel(g, "barchart", title, w, 8, [target(per_mission)], unit=unit,
                     overrides=[color_override(n, c) for n, c in colors.items()],
                     custom={"fillOpacity": 85, "gradientMode": "hue", "lineWidth": 0},
                     color={"mode": "palette-classic"},
                     transformations=[{"id": "organize", "options": {"excludeByName": excl}}],
                     options={"xField": "Mission", "orientation": "vertical", "barWidth": 0.8,
                              "groupWidth": 0.75, "showValue": "never", "stacking": "none",
                              "xTickLabelRotation": -45, "xTickLabelSpacing": 0,
                              "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                              "tooltip": {"mode": "multi"}}, **kw)

    P.append(mission_bars("Command success rate per mission", ["Success %"], "percent",
                          {"Success %": "green"}, minv=50, maxv=100, th=TH_SUCCESS,
                          desc="Trend across sprints is the evidence package required by ARISE (PoC → TRL 6–7)"))
    P.append(mission_bars("Rejection · false activation per mission", ["Rejection %", "False activation %"], "percent",
                          {"Rejection %": "red", "False activation %": "orange"}, minv=0))
    P.append(mission_bars("Confirmation loops per 10 commands", ["Loops / 10"], "none",
                          {"Loops / 10": "amber"}, minv=0))
    P.append(mission_bars("Latency · time-to-safe-stop per mission", ["Mean latency", "p95 latency", "Time-to-safe-stop"],
                          "ms", {"Mean latency": "cyan", "p95 latency": "violet", "Time-to-safe-stop": "pink"}, minv=0))

    P.append(row(g, "Acoustic performance (IntentEvent history in range)"))
    P.append(panel(g, "table", "Performance by acoustic condition", 12, 8, [target(
        "SELECT compound->>'acoustic_condition' AS \"Condition\", count(*) AS \"Commands\",\n"
        f"  100 * avg({SUCCESS}) AS \"Success\",\n"
        "  avg((compound->>'confidence')::float) AS \"Mean confidence\",\n"
        f"  10 * avg({CONFIRMED}) AS \"Loops / 10\",\n"
        "  avg((compound->>'latency_ms')::float) AS \"Mean latency\"\n"
        f"{EVENTS} AND {DECIDED}\nGROUP BY 1 ORDER BY 3 DESC")],
        desc="Noise + mask triggers fallback to gesture + thermal cues; watch the confirmation-loop rate.",
        custom={"align": "auto", "cellOptions": {"type": "auto"}},
        overrides=[cell_color("Condition", COND_MAP, "color-text"),
                   cell_gauge("Success", "percent", TH_SUCCESS),
                   cell_props("Mean confidence", decimals=2, thresholds=TH_CONF,
                              custom__cellOptions={"type": "color-text"}),
                   cell_props("Loops / 10", decimals=2, thresholds=TH_LOOPS,
                              custom__cellOptions={"type": "color-text"}),
                   cell_props("Mean latency", unit="ms", decimals=0)],
        options={"showHeader": True, "cellHeight": "md", "footer": {"show": False}}))
    P.append(panel(g, "barchart", "Calibration · reliability diagram", 12, 8, [target(
        "SELECT to_char(width_bucket((compound->>'confidence')::float, 0, 1, 10) / 10.0 - 0.05, 'FM0.00') AS \"Bin\",\n"
        "  avg((compound->>'confidence')::float) AS \"Mean confidence\",\n"
        "  avg(CASE WHEN compound->>'outcome' = 'correct' THEN 1.0 ELSE 0.0 END) AS \"Observed accuracy\"\n"
        f"{EVENTS} AND {DECIDED}\nGROUP BY 1 ORDER BY 1")],
        desc="Confidence calibration quality: bars match when confidence predicts errors well (gap = ECE contribution).",
        unit="percentunit", minv=0, maxv=1,
        overrides=[color_override("Mean confidence", "slate"), color_override("Observed accuracy", "cyan")],
        custom={"fillOpacity": 80, "lineWidth": 0, "gradientMode": "none"},
        color={"mode": "palette-classic"},
        options={"xField": "Bin", "orientation": "vertical", "barWidth": 0.85, "groupWidth": 0.8,
                 "showValue": "never", "stacking": "none",
                 "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "multi"}}))
    P.append(panel(g, "barchart", "Success rate per intent", 12, 8, [target(
        "SELECT compound->>'intent' AS \"Intent\", 100 * avg(" + SUCCESS + ") AS \"Success %\",\n"
        f"  100 * avg({CONFIRMED}) AS \"Confirmed %\"\n"
        f"{EVENTS} AND {DECIDED}\nGROUP BY 1 ORDER BY 2 DESC")],
        unit="percent", minv=0, maxv=100,
        overrides=[color_override("Success %", "green"), color_override("Confirmed %", "amber")],
        custom={"fillOpacity": 85, "lineWidth": 0, "gradientMode": "hue"}, color={"mode": "palette-classic"},
        options={"xField": "Intent", "orientation": "horizontal", "barWidth": 0.8, "groupWidth": 0.75,
                 "showValue": "auto", "stacking": "none",
                 "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "multi"}}))
    P.append(panel(g, "table", "Per-operator profile (inclusiveness: who struggles with what)", 12, 8, [target(
        "SELECT compound->>'operator_id' AS \"Operator\", count(*) AS \"Commands\",\n"
        f"  100 * avg({SUCCESS}) AS \"Success\",\n"
        "  100 * avg(CASE WHEN compound->>'outcome' = 'false_activation' THEN 1.0 ELSE 0.0 END) AS \"False act.\",\n"
        "  avg((compound->>'confidence')::float) AS \"Mean confidence\",\n"
        "  avg((compound->>'latency_ms')::float) AS \"Mean latency\",\n"
        "  mode() WITHIN GROUP (ORDER BY compound->>'modality') AS \"Preferred modality\"\n"
        f"{EVENTS} AND {DECIDED}\nGROUP BY 1 ORDER BY 1")],
        custom={"align": "auto", "cellOptions": {"type": "auto"}},
        overrides=[cell_gauge("Success", "percent", TH_SUCCESS),
                   cell_props("False act.", unit="percent", decimals=1, thresholds=TH_FALSE_ACT,
                              custom__cellOptions={"type": "color-text"}),
                   cell_props("Mean confidence", decimals=2),
                   cell_props("Mean latency", unit="ms", decimals=0)],
        options={"showHeader": True, "cellHeight": "md", "footer": {"show": False}}))

    P.append(row(g, "NGSI-LD temporal API (Mintaka)"))
    P.append(panel(g, "timeseries", "Command success via Mintaka temporal API", 24, 8, [{
        "datasource": INF,
        "refId": "A",
        "type": "json",
        "source": "url",
        "parser": "backend",
        "format": "timeseries",
        "url": ("http://mintaka:8080/temporal/entities/urn:ngsi-ld:HRISessionKPI:$robot"
                "?attrs=commandSuccessRate&timerel=between&timeAt=${__from:date:iso}&endTimeAt=${__to:date:iso}"
                "&lastN=500"),
        "url_options": {"method": "GET", "headers": [{"key": "Accept", "value": "application/json"}]},
        "root_selector": "commandSuccessRate",
        "columns": [
            {"selector": "observedAt", "text": "time", "type": "timestamp"},
            {"selector": "value", "text": "Command success (Mintaka)", "type": "number"},
        ],
    }],
        desc="HRISessionKPI.commandSuccessRate (rolling 15 min) fetched through the standard NGSI-LD temporal API "
             "(GET /temporal/entities/{id}) served by FIWARE Mintaka — the interoperable path for external IT/OT systems. "
             "Entities created by Orion-LD 1.10's DDS bridge have no TROE entity record, so Mintaka cannot serve them yet; "
             "the SQL panels read their history directly.",
        unit="percent", minv=0, maxv=100, ds=INF,
        custom=ts_custom(fill=20, interp="smooth"),
        color={"mode": "fixed", "fixedColor": C["teal"]},
        options={"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "single"}}))

    return dashboard("aura-historian", "AURA · Historian & analytics",
                     "Cross-mission KPIs (MissionReport), acoustic-condition matrix, calibration and per-operator analytics "
                     "from the TROE historian and the NGSI-LD temporal API.",
                     P, time_from="now-30d", refresh="30s", tags=["historian"])


# =========================================================================== 04 alerts


ALERTS = (
    "WITH a AS (\n"
    "  SELECT DISTINCT ON (entityid, id) entityid, split_part(id, '/', -1) AS attr, number, text, ts\n"
    "  FROM attributes\n"
    "  WHERE entityid LIKE 'urn:ngsi-ld:Alert:%'\n"
    "  ORDER BY entityid, id, ts DESC\n"
    "), f AS (\n"
    "  SELECT entityid, min(ts) AS issued, max(ts) AS updated FROM attributes\n"
    "  WHERE entityid LIKE 'urn:ngsi-ld:Alert:%' GROUP BY entityid\n"
    "), alerts AS (\n"
    "  SELECT a.entityid, f.issued, f.updated,\n"
    "    max(text) FILTER (WHERE attr = 'severity') AS severity,\n"
    "    max(text) FILTER (WHERE attr = 'status') AS status,\n"
    "    max(text) FILTER (WHERE attr = 'category') AS category,\n"
    "    max(text) FILTER (WHERE attr = 'subCategory') AS rule,\n"
    "    max(text) FILTER (WHERE attr = 'description') AS description,\n"
    "    max(text) FILTER (WHERE attr = 'missionId') AS mission,\n"
    "    max(number) FILTER (WHERE attr = 'value') AS value,\n"
    "    max(number) FILTER (WHERE attr = 'threshold') AS threshold\n"
    "  FROM a JOIN f USING (entityid) GROUP BY a.entityid, f.issued, f.updated\n"
    ")\n"
)


def alerts() -> dict:
    g = Grid()
    P: list = [header(g, "Alerts & mission reports")]

    P.append(row(g, "Alert overview (Alert entities, FIWARE smart data model)"))
    counts = (ALERTS +
              "SELECT count(*) FILTER (WHERE status <> 'resolved') AS \"Open\",\n"
              "  count(*) FILTER (WHERE status <> 'resolved' AND severity = 'critical') AS \"Critical\",\n"
              "  count(*) FILTER (WHERE status <> 'resolved' AND severity = 'high') AS \"High\",\n"
              "  count(*) FILTER (WHERE status <> 'resolved' AND severity = 'medium') AS \"Medium\",\n"
              "  count(*) FILTER (WHERE status <> 'resolved' AND severity IN ('low','informational')) AS \"Low / info\",\n"
              "  count(*) FILTER (WHERE $__timeFilter(issued)) AS \"Raised in range\"\nFROM alerts")
    for name, color in [("Open", "blue"), ("Critical", "red"), ("High", "orange"), ("Medium", "amber"),
                        ("Low / info", "slate"), ("Raised in range", "violet")]:
        th = thresholds((None, "green"), (1, color)) if name not in ("Raised in range",) else thresholds((None, color))
        P.append(panel(g, "stat", name, 4, 4, [target(counts)], decimals=0, th=th,
                       options=stat_opts(graph="none", fields=f"/^{name}$/")))

    P.append(panel(g, "table", "Alerts", 16, 12, [target(
        ALERTS +
        "SELECT issued AS \"Issued\", severity AS \"Severity\", status AS \"Status\", category AS \"Category\",\n"
        "  rule AS \"Rule\", description AS \"Description\", value AS \"Value\", threshold AS \"Threshold\",\n"
        "  mission AS \"Mission\", updated AS \"Updated\"\n"
        "FROM alerts WHERE $__timeFilter(issued) OR status <> 'resolved'\n"
        "ORDER BY (status = 'resolved'), issued DESC LIMIT 200")],
        desc="Raised and resolved by aura-monitor (hysteresis rules on HRI, robot, environment and middleware signals).",
        custom={"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True},
        overrides=[cell_color("Severity", SEVERITY_MAP), cell_color("Status", STATUS_MAP, "color-text"),
                   cell_props("Issued", custom__width=150), cell_props("Updated", custom__width=150),
                   cell_props("Description", custom__minWidth=260),
                   cell_props("Value", decimals=2), cell_props("Threshold", decimals=2)],
        options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}))
    P.append({
        "id": g.next_id(), "type": "alertlist", "title": "Grafana alert rules (unified alerting)",
        "description": "Grafana-managed rules provisioned from grafana/provisioning/alerting/rules.yaml",
        "gridPos": g.place(8, 12),
        "options": {"viewMode": "list", "groupMode": "default", "groupBy": [], "maxItems": 20,
                    "sortOrder": 1, "dashboardAlerts": False, "alertName": "", "alertInstanceLabelFilter": "",
                    "folder": None,
                    "stateFilter": {"firing": True, "pending": True, "noData": False, "normal": True,
                                    "error": True, "recovering": True},
                    "showInstances": False},
    })
    P.append(panel(g, "state-timeline", "Alert timeline by rule", 24, 7, [target(
        "WITH s AS (\n"
        "  SELECT entityid, ts, text AS status FROM attributes\n"
        "  WHERE entityid LIKE 'urn:ngsi-ld:Alert:%' AND id LIKE '%/status' AND $__timeFilter(ts)\n"
        "), r AS (\n"
        "  SELECT DISTINCT ON (entityid) entityid, text AS rule FROM attributes\n"
        "  WHERE entityid LIKE 'urn:ngsi-ld:Alert:%' AND id LIKE '%/subCategory' ORDER BY entityid, ts DESC\n"
        ")\n"
        "SELECT s.ts AS \"time\", r.rule AS metric, s.status AS value\n"
        "FROM s JOIN r USING (entityid) ORDER BY 1", fmt="table")],
        mappings=STATUS_MAP, th=thresholds((None, "slate")),
        transformations=[{"id": "prepareTimeSeries", "options": {"format": "multi"}}],
        custom={"fillOpacity": 85, "lineWidth": 0},
        options={"mergeValues": True, "showValue": "auto", "alignValue": "left", "rowHeight": 0.8,
                 "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "single"}}))

    P.append(row(g, "Mission reports (MissionReport entities)"))
    P.append(panel(g, "table", "Mission reports", 24, 12, [target(
        REPORTS +
        "SELECT split_part(entityid, ':', 4) AS \"Mission\", max(t) AS \"Ended\",\n"
        f"  {rtext('verdict', 'Verdict')}, {rtext('operator', 'Operator')}, {rtext('condition', 'Condition')},\n"
        f"  {rnum('durationMin', 'Duration')}, {rnum('commandCount', 'Commands')},\n"
        f"  {rnum('commandSuccessRate', 'Success')}, {rnum('falseActivationRate', 'False act.')},\n"
        f"  {rnum('rejectionRate', 'Rejection')}, {rnum('confirmationLoopRate', 'Loops / 10')},\n"
        f"  {rnum('meanLatency', 'Latency')}, {rnum('timeToSafeStop', 'Safe-stop')},\n"
        f"  {rnum('alertsCount', 'Alerts')}, {rnum('distance', 'Distance')}, {rnum('batteryUsed', 'Battery used')}\n"
        "FROM r WHERE entityid IN (SELECT entityid FROM r GROUP BY entityid HAVING max(t) >= $__timeFrom())\n"
        "GROUP BY entityid ORDER BY 2 DESC")],
        desc="Automated per-mission evidence package. Click a mission to open its printable report (aura-monitor).",
        custom={"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True},
        overrides=[
            cell_color("Verdict", VERDICT_MAP),
            cell_color("Condition", COND_MAP, "color-text"),
            cell_props("Mission", links=[{"title": "Open mission report", "targetBlank": True,
                                          "url": REPORTS_URL + "/reports/${__value.raw}"}],
                       custom__width=90),
            cell_props("Ended", custom__width=150),
            cell_props("Duration", unit="m", decimals=0),
            cell_gauge("Success", "percent", TH_SUCCESS),
            cell_props("False act.", unit="percent", decimals=1, thresholds=TH_FALSE_ACT,
                       custom__cellOptions={"type": "color-text"}),
            cell_props("Rejection", unit="percent", decimals=1, thresholds=TH_REJECT,
                       custom__cellOptions={"type": "color-text"}),
            cell_props("Loops / 10", decimals=2, thresholds=TH_LOOPS, custom__cellOptions={"type": "color-text"}),
            cell_props("Latency", unit="ms", decimals=0, thresholds=TH_LAT, custom__cellOptions={"type": "color-text"}),
            cell_props("Safe-stop", unit="ms", decimals=0),
            cell_props("Distance", unit="lengthm", decimals=0),
            cell_props("Battery used", unit="percent", decimals=0),
        ],
        options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                 "sortBy": [{"displayName": "Ended", "desc": True}]}))

    return dashboard("aura-alerts", "AURA · Alerts & reports",
                     "Automated events, alerts and mission reports (ARISE Powered-by-FIWARE condition monitoring).",
                     P, time_from="now-7d", refresh="10s", tags=["alerts"])


# =========================================================================== alert rules

RULES_OUT = Path(__file__).resolve().parent / "provisioning" / "alerting" / "rules.yaml"
RECENT = "ts > (now() AT TIME ZONE 'UTC') - interval '2 minutes'"


def latest_sql(entity: str, name: str, expr: str = "number") -> str:
    return (f"SELECT {expr} AS value FROM attributes\n"
            f"WHERE entityid = '{entity}' AND {attr(name)} AND {RECENT}\nORDER BY ts DESC LIMIT 1")


def latest_session_sql(name: str) -> str:
    return (f"SELECT number AS value FROM attributes\n"
            f"WHERE entityid LIKE 'urn:ngsi-ld:HRISession:%' AND {attr(name)} AND {RECENT}\nORDER BY ts DESC LIMIT 1")


def latest_index_sql(entity: str, name: str, limits: dict[str, float]) -> str:
    """greatest(field / limit) over fields of the latest compound value: > 1 means a limit is exceeded."""
    parts = ", ".join(f"coalesce({j(f)}, 0) / {lim}" for f, lim in limits.items())
    return latest_sql(entity, name, f"greatest({parts})")


def rule(uid: str, title: str, sql: str, op: str, value: float, severity: str, category: str,
         summary: str, dashboard_uid: str, pending: str = "30s") -> dict:
    return {
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            {"refId": "A", "relativeTimeRange": {"from": 600, "to": 0}, "datasourceUid": PG["uid"],
             "model": {"refId": "A", "datasource": PG, "editorMode": "code", "format": "table",
                       "rawQuery": True, "rawSql": sql, "intervalMs": 1000, "maxDataPoints": 43200}},
            {"refId": "C", "relativeTimeRange": {"from": 0, "to": 0}, "datasourceUid": "__expr__",
             "model": {"refId": "C", "type": "threshold", "expression": "A",
                       "datasource": {"type": "__expr__", "uid": "__expr__"},
                       "conditions": [{"evaluator": {"type": op, "params": [value]}}]}},
        ],
        "noDataState": "OK",
        "execErrState": "Error",
        "for": pending,
        "annotations": {"summary": summary, "dashboard": f"http://localhost:3000/d/{dashboard_uid}",
                        "console": CONSOLE},
        "labels": {"severity": severity, "category": category, "rule": uid.removeprefix("aura-"),
                   "project": "aura"},
        "isPaused": False,
    }


def alert_rules() -> dict:
    robot = "urn:ngsi-ld:Robot:artaban-01"
    air = "urn:ngsi-ld:AirQualityObserved:artaban-01"
    battery = f"100 * {j('percentage')}"   # sensor_msgs/BatteryState.percentage is 0..1
    robot_rules = [
        rule("aura-batteryLow", "Battery low", latest_sql(robot, "battery", battery), "lt", 25, "medium", "robot",
             "Artaban battery below 25 % — plan return to dock.", "artaban-telemetry"),
        rule("aura-batteryCritical", "Battery critical", latest_sql(robot, "battery", battery), "lt", 12, "critical",
             "robot", "Artaban battery below 12 % — abort mission and return now.", "artaban-telemetry", "10s"),
        rule("aura-jointOverTemp", "Joint over-temperature",
             latest_sql(robot, "status", j("max_joint_temperature")), "gt", 70,
             "high", "robot", "An Artaban actuator is above 70 °C.", "artaban-telemetry"),
        rule("aura-eStopEngaged", "E-stop engaged",
             latest_sql(robot, "status", f"CASE WHEN {j('e_stop', 'boolean')} THEN 1 ELSE 0 END"), "gt", 0.5,
             "critical", "robot", "Emergency stop is engaged on Artaban.", "artaban-telemetry", "0s"),
        rule("aura-commsDegraded", "Comms degraded",
             latest_index_sql(robot, "status", {"packet_loss": 5, "link_latency_ms": 250}), "gt", 1, "medium",
             "middleware", "Robot link degraded (packet loss > 5 % or latency > 250 ms).", "artaban-telemetry"),
    ]
    env_rules = [
        rule("aura-gasDetected", "Gas detected", latest_index_sql(air, "reading", {"h2s_ppm": 10, "ch4_lel": 10}),
             "gt", 1, "critical", "environment", "H₂S > 10 ppm or CH₄ > 10 %LEL at the robot position.",
             "artaban-telemetry", "0s"),
        rule("aura-noiseHazard", "Noise hazard", latest_sql(air, "reading", j("la_eq")), "gt", 85, "low",
             "environment", "Ambient noise above 85 dB(A) — hearing protection and degraded acoustic recognition.",
             "artaban-telemetry", "1m"),
    ]
    hri_rules = [
        rule("aura-rejectionRateSpike", "Rejection rate spike", latest_session_sql("windowRejectionRate"), "gt", 10,
             "high", "hri", "5-minute rejection rate above 10 % — consider gesture fallback.", "aura-hri"),
        rule("aura-falseActivationHigh", "False activations high", latest_session_sql("falseActivationRate"), "gt",
             5, "high", "hri", "False activation rate above 5 %.", "aura-hri"),
        rule("aura-latencyAbnormal", "Command latency abnormal", latest_session_sql("p95Latency"), "gt", 600,
             "medium", "hri", "p95 end-to-end command latency above 600 ms.", "aura-hri"),
        rule("aura-confirmationLoopsElevated", "Confirmation loops elevated",
             latest_session_sql("confirmationLoopRate"), "gt", 2.0, "medium", "hri",
             "More than 2 confirmation loops per 10 commands.", "aura-hri", "1m"),
    ]
    group = lambda name, rules: {"orgId": 1, "name": name, "folder": "AURA · ARISE", "interval": "30s",
                                 "rules": rules}
    return {"apiVersion": 1, "groups": [group("Artaban robot", robot_rules),
                                        group("Hazardous environment", env_rules),
                                        group("AURA HRI", hri_rules)]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in [("01-artaban-telemetry", telemetry), ("02-hri-condition-monitoring", hri),
                        ("03-historian-analytics", historian), ("04-alerts-reports", alerts)]:
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path.relative_to(OUT.parent.parent)}")
    # JSON is valid YAML; generated so thresholds stay in sync with the dashboards.
    RULES_OUT.write_text("# Generated by fiware/grafana/build_dashboards.py — do not edit by hand.\n"
                         + json.dumps(alert_rules(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {RULES_OUT.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
