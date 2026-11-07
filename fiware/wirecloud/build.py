#!/usr/bin/env python3
"""Package the AURA WireCloud components (stdlib only).

    python3 wirecloud/build.py [out_dir]     → out_dir (default wirecloud/dist)/*.wgt

Runs on Python 3.6+ (the wirecloud-init job builds inside the WireCloud image).

Each widget = widgets/<slug>/js/main.js + generated config.xml/index.html + the shared AURA library
(React 18 UMD, AURA Telemetry design system, aura-wc.js). The mashup embeds every widget and the
NGSI-LD source operator and wires the operator's `entities` output to every widget's `entities` input.
"""

import os
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent
DIST = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
VENDOR, VERSION = "Sensifai", "1.2.3"
NS = "http://wirecloud.conwet.fi.upm.es/ns/macdescription/1"
AUTHOR, EMAIL = "Sensifai", "javad@sensifai.com"

SHARED = {  # path in the package → source
    "lib/react.production.min.js": ROOT / "shared/lib/react.production.min.js",
    "lib/react-dom.production.min.js": ROOT / "shared/lib/react-dom.production.min.js",
    "lib/aura-wc.js": ROOT / "shared/aura-wc.js",
    "lib/aura-wc.css": ROOT / "shared/aura-wc.css",
    "ds/tokens.css": ROOT / "shared/ds/tokens.css",
    "ds/bundle.css": ROOT / "shared/ds/bundle.css",
    "ds/bundle.js": ROOT / "shared/ds/bundle.js",
}

COMMON_PREFS = [
    ("ngsi_server", "text", "Orion-LD URL", "NGSI-LD broker as reached from WireCloud's proxy", "http://orion:1026"),
    ("mintaka_server", "text", "Mintaka URL", "NGSI-LD temporal API (history backfill)", "http://mintaka:8080"),
]
THEME_PREF = ("theme", "list", "Theme", "AURA Telemetry theme", "dark")

# slug, title, description, (width, height) in the 20-column / 12px default grid, extra prefs
WIDGETS = [
    ("kpi-tiles", "HRI KPIs", "StatTiles over HRISessionKPI (rolling 15 min) with Mintaka sparklines.", (20, 12), []),
    ("pipeline-health", "Module health", "PipelineMap over HRIModuleStatus (stt, intent, gesture, speaker).", (8, 24), []),
    ("confidence-trend", "Intent confidence", "TimeSeriesChart of HRIEvent confidence with the intent node's thresholds.", (12, 24), []),
    ("live-log", "Live interaction log", "LogTable over the five HRIEvent entities, Mintaka backfill + live.", (20, 34), []),
    ("intent-breakdown", "Intents this shift", "BarChart of HRISessionKPI.intentCounts.", (7, 24), []),
    ("module-rates", "Event rate by module", "BarChart of HRISessionKPI.moduleEventRates.", (6, 24), []),
    ("rejection-reasons", "Rejection reasons", "BarChart of HRISessionKPI.rejectionReasons.", (7, 24), []),
    ("robot-vitals", "Artaban vitals", "StatTiles over Robot and NoiseLevelObserved.", (20, 12), []),
    ("robot-state", "Artaban state", "Mission, waypoint, mode, gait and safety from the Robot entity.", (8, 26), []),
    ("battery-trend", "Battery", "TimeSeriesChart of Robot.batteryLevel.", (12, 26), []),
    ("joint-temperatures", "Joint temperatures", "BarChart of Robot.jointTemperatures (12 joints).", (10, 30), []),
    ("environment", "Site environment", "AirQualityObserved and NoiseLevelObserved against hazard limits.", (10, 30), []),
    ("alerts-panel", "Active alerts", "Alert entities raised by aura-monitor; acknowledge / resolve in Orion-LD.", (11, 38), []),
    ("mission-reports", "Mission reports", "MissionReport entities, newest first.", (9, 38),
     [("report_base_url", "text", "Report pages URL", "Where aura-monitor serves printable mission reports", "http://localhost:8090")]),
]

# Mashup tabs: (tab name, title, [(slug, x, y)]) — sizes come from WIDGETS
TABS = [
    ("hri", "HRI", [("kpi-tiles", 0, 0), ("pipeline-health", 0, 12), ("confidence-trend", 8, 12),
                    ("live-log", 0, 36), ("intent-breakdown", 0, 70), ("module-rates", 7, 70), ("rejection-reasons", 13, 70)]),
    ("artaban", "Artaban telemetry", [("robot-vitals", 0, 0), ("robot-state", 0, 12), ("battery-trend", 8, 12),
                                      ("joint-temperatures", 0, 38), ("environment", 10, 38)]),
    ("ops", "Alerts & reports", [("alerts-panel", 0, 0), ("mission-reports", 11, 0)]),
]
OPERATOR = ("aura-ngsild-source", ROOT / "operators/ngsild-source")
OPERATOR_PREFS = ("ngsi_server", "ngsi_proxy_public", "ngsi_proxy_internal", "entity_types")

# Dashboards built from the same widgets: (mashup name, title, simulator?). They differ only in preference values:
# the Simulator reads its own broker (orion-sim / mintaka-sim, fed on DDS domain 1), the Aura dashboard the real
# robot's (orion / mintaka, DDS domain 0). ARISE_PUBLIC_URL (e.g. https://arise.sensifai.com) sets the browser-facing
# addresses; without it the defaults of the local compose file apply.
PUBLIC_URL = os.environ.get("ARISE_PUBLIC_URL", "").rstrip("/")
DASHBOARDS = [("aura-dashboard", "AURA · Artaban HRI condition monitoring", False),
              ("aura-simulator", "AURA · Simulator (test and debug)", True)]


def dashboard_prefs(simulator):
    values = {}
    if PUBLIC_URL:
        values["ngsi_proxy_public"] = PUBLIC_URL
        values["report_base_url"] = PUBLIC_URL + ("/monitor/sim" if simulator else "/monitor/aura")
    if simulator:
        values["ngsi_server"] = "http://orion-sim:1026"
        values["mintaka_server"] = "http://mintaka-sim:8080"
    return values


def preference_values(names, values):
    return "".join(f'\n        <preferencevalue name="{n}" value={quoteattr(values[n])}/>' for n in names if n in values)


def pref_xml(name, typ, label, desc, default):
    if typ == "list":
        return (f'    <preference name="{name}" type="list" label={quoteattr(label)} description={quoteattr(desc)} default="{default}">\n'
                '      <option label="Control room (dark)" value="dark"/>\n      <option label="Daylight" value="light"/>\n'
                '    </preference>')
    return f'    <preference name="{name}" type="{typ}" label={quoteattr(label)} description={quoteattr(desc)} default={quoteattr(default)}/>'


def widget_config(slug, title, desc, size, extra):
    prefs = "\n".join(pref_xml(*p) for p in COMMON_PREFS + extra + [THEME_PREF])
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<widget xmlns="{NS}" vendor="{VENDOR}" name="aura-{slug}" version="{VERSION}">
  <details>
    <title>{escape("AURA · " + title)}</title>
    <authors>{AUTHOR}</authors>
    <email>{EMAIL}</email>
    <description>{escape(desc)}</description>
    <license>Apache-2.0</license>
  </details>
  <preferences>
{prefs}
  </preferences>
  <wiring>
    <inputendpoint name="entities" type="text" label="NGSI-LD entities" description="Normalized NGSI-LD entities from the AURA NGSI-LD source operator" friendcode="ngsild-entities"/>
  </wiring>
  <contents src="index.html" contenttype="text/html" charset="utf-8" useplatformstyle="false"/>
  <rendering width="{size[0]}" height="{size[1]}"/>
</widget>
'''


def widget_index(title):
    return f'''<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="ds/tokens.css">
  <link rel="stylesheet" href="ds/bundle.css">
  <link rel="stylesheet" href="lib/aura-wc.css">
</head>
<body>
  <div id="root"></div>
  <script src="lib/react.production.min.js"></script>
  <script src="lib/react-dom.production.min.js"></script>
  <script src="ds/bundle.js"></script>
  <script src="lib/aura-wc.js"></script>
  <script src="js/main.js"></script>
</body>
</html>
'''


def pack(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in files.items():
            z.writestr(name, src.read_bytes() if isinstance(src, Path) else src)
    return path


def wgt_name(name):
    return f"{VENDOR}_{name}_{VERSION}.wgt"


def build_widgets():
    sizes = {}
    for slug, title, desc, size, extra in WIDGETS:
        files = {
            "config.xml": widget_config(slug, title, desc, size, extra).encode(),
            "index.html": widget_index("AURA · " + title).encode(),
            "js/main.js": ROOT / f"widgets/{slug}/js/main.js",
            **SHARED,
        }
        pack(DIST / wgt_name(f"aura-{slug}"), files)
        sizes[slug] = (title, size)
    return sizes


def build_operator():
    name, src = OPERATOR
    files = {str(p.relative_to(src)): p for p in src.rglob("*") if p.is_file()}
    return pack(DIST / wgt_name(name), files)


def build_mashup(sizes, mashup_name, mashup_title, values):
    widget_prefs = {slug: [p[0] for p in COMMON_PREFS + extra + [THEME_PREF]] for slug, _t, _d, _s, extra in WIDGETS}
    embedded = "\n".join(f'    <resource vendor="{VENDOR}" name="aura-{s}" version="{VERSION}" src="macs/{wgt_name("aura-" + s)}"/>'
                         for s, *_ in WIDGETS)
    embedded += f'\n    <resource vendor="{VENDOR}" name="{OPERATOR[0]}" version="{VERSION}" src="macs/{wgt_name(OPERATOR[0])}"/>'
    tabs, conns, wid = [], [], 0
    for i, (name, title, placed) in enumerate(TABS):
        res = []
        for z, (slug, x, y) in enumerate(placed):
            wid += 1
            wtitle, (w, hgt) = sizes[slug]
            res.append(f'''      <resource id="{wid}" vendor="{VENDOR}" name="aura-{slug}" version="{VERSION}" title={quoteattr(wtitle)}>{preference_values(widget_prefs[slug], values)}
        <position x="{x}" y="{y}" z="{z}"/>
        <rendering width="{w}" height="{hgt}" layout="0" titlevisible="false"/>
      </resource>''')
            conns.append(f'''      <connection>
        <source type="operator" id="0" endpoint="entities"/>
        <target type="widget" id="{wid}" endpoint="entities"/>
      </connection>''')
        tabs.append(f'    <tab id="{i}" name="{name}" title={quoteattr(title)}>\n' + "\n".join(res) + "\n    </tab>")
    config = f'''<?xml version="1.0" encoding="UTF-8"?>
<mashup xmlns="{NS}" vendor="{VENDOR}" name="{mashup_name}" version="{VERSION}">
  <details>
    <title>{escape(mashup_title)}</title>
    <authors>{AUTHOR}</authors>
    <email>{EMAIL}</email>
    <description>Powered-by-FIWARE condition monitoring for the Artaban robot and the AURA HRI modules (ARISE): Orion-LD → ngsi-proxy → AURA widgets, history from Mintaka.</description>
    <license>Apache-2.0</license>
  </details>
  <embedded>
{embedded}
  </embedded>
  <structure>
{chr(10).join(tabs)}
    <wiring version="2.0">
      <operator id="0" vendor="{VENDOR}" name="{OPERATOR[0]}" version="{VERSION}">{preference_values(OPERATOR_PREFS, values)}
      </operator>
{chr(10).join(conns)}
    </wiring>
  </structure>
</mashup>
'''
    files = {"config.xml": config.encode()}
    mashups = {wgt_name(m) for m, _t, _s in DASHBOARDS}
    for p in sorted(DIST.glob(f"{VENDOR}_aura-*.wgt")):
        if p.name not in mashups:
            files[f"macs/{p.name}"] = p
    return pack(DIST / wgt_name(mashup_name), files)


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.glob("*.wgt"):
        old.unlink()
    sizes = build_widgets()
    build_operator()
    mashups = [build_mashup(sizes, name, title, dashboard_prefs(sim)) for name, title, sim in DASHBOARDS]
    for p in sorted(DIST.glob("*.wgt")):
        print(f"{p.stat().st_size / 1024:7.1f} KiB  {p.name}")
    print("mashups:", ", ".join(m.name for m in mashups))


if __name__ == "__main__":
    main()
