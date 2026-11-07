"""Mission report: NGSI-LD MissionReport entity + printable HTML evidence page."""

from __future__ import annotations

from html import escape

from .kpi import Mission, verdict
from .ngsi import now_iso, prop, rel

# (attribute, label, unit, target text, ok(value) -> bool)
KPI_TABLE = [
    ("commandSuccessRate", "Command success rate", "%", "≥ 88 %", lambda v: v >= 88),
    ("falseActivationRate", "False activation rate", "%", "< 3 %", lambda v: v < 3),
    ("rejectionRate", "Rejection rate", "%", "< 10 %", lambda v: v < 10),
    ("confirmationLoopRate", "Confirmation loops / 10 cmds", "", "≤ 2.0", lambda v: v <= 2.0),
    ("meanLatency", "Mean command latency", "ms", "< 400 ms", lambda v: v < 400),
    ("p95Latency", "p95 command latency", "ms", "< 600 ms", lambda v: v < 600),
    ("meanConfidence", "Mean acoustic confidence", "", "≥ 0.70", lambda v: v >= 0.70),
    ("timeToSafeStop", "Mean time-to-safe-stop", "ms", "< 1000 ms", lambda v: 0 < v < 1000),
    ("recoveryTime", "Recovery after misunderstanding", "s", "< 8 s", lambda v: v < 8),
    ("operatorInterventions", "Operator interventions", "", "—", lambda v: True),
    ("calibrationError", "Calibration error (ECE)", "", "< 0.08", lambda v: v < 0.08),
]


def build_entity(m: Mission, ended_at: str | None = None, final: bool = True) -> dict:
    k = m.kpis()
    ts = ended_at or now_iso()
    started = m.started_at
    try:
        from datetime import datetime
        dur = (datetime.fromisoformat(ts.replace("Z", "+00:00")) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() / 60
    except ValueError:
        dur = 0.0
    attrs = {key: prop(val, ts) for key, val in k.items()}
    attrs.update({
        "missionId": prop(m.mission_id, ts),
        "dateCreated": prop(now_iso(), ts),
        "startedAt": prop(started, ts),
        "endedAt": prop(ts, ts),
        "durationMin": prop(round(dur, 1), ts),
        "operator": prop(m.operator or "-", ts),
        "condition": prop(m.dominant_condition, ts),
        "alertsCount": prop(m.alerts, ts),
        "distance": prop(round(m.distance, 1), ts),
        "batteryUsed": prop(round((m.start_battery or 0) - (m.min_battery or 0), 1), ts),
        "inspectionPoints": prop(7, ts),
        "verdict": prop(verdict(k) if k["commandCount"] else "watch", ts),
        "reportUrl": prop(f"/reports/{m.mission_id}", ts),
        "synthetic": prop(False, ts),
        "status": prop("final" if final else "interim", ts),
        "robot": rel("urn:ngsi-ld:Robot:artaban-01"),
    })
    return {"id": f"urn:ngsi-ld:MissionReport:{m.mission_id}", "type": "MissionReport", **attrs}


def _fmt(v, unit: str) -> str:
    if isinstance(v, float):
        v = f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 10 else f"{v:.1f}"
    return f"{v} {unit}".strip()


def render_html(report: dict, alerts: list[dict], events: list[dict], baseline: dict | None) -> str:
    mid = escape(str(report.get("missionId", "?")))
    verdict_ = str(report.get("verdict", "watch"))
    rows = []
    for key, label, unit, target, ok in KPI_TABLE:
        v = report.get(key)
        if v is None:
            continue
        good = ok(float(v)) if isinstance(v, (int, float)) else True
        base = baseline.get(key) if baseline else None
        delta = ""
        if isinstance(base, (int, float)) and isinstance(v, (int, float)):
            d = v - base
            delta = f"{'+' if d >= 0 else ''}{_fmt(round(d, 3), unit)}"
        rows.append(
            f"<tr><td>{escape(label)}</td><td class='num'>{escape(_fmt(v, unit))}</td>"
            f"<td class='muted'>{escape(target)}</td><td class='num muted'>{escape(delta)}</td>"
            f"<td><span class='pill {'ok' if good else 'bad'}'>{'met' if good else 'missed'}</span></td></tr>"
        )
    alert_rows = "".join(
        f"<tr><td><span class='pill sev-{escape(str(a.get('severity')))}'>{escape(str(a.get('severity', '')).upper())}</span></td>"
        f"<td>{escape(str(a.get('subCategory', '')))}</td><td>{escape(str(a.get('description', '')))}</td>"
        f"<td class='mono'>{escape(str(a.get('dateIssued', ''))[11:19])}</td><td>{escape(str(a.get('status', '')))}</td></tr>"
        for a in alerts
    ) or "<tr><td colspan='5' class='muted'>No alerts raised during this mission.</td></tr>"

    by_intent: dict[str, list[int]] = {}
    for e in events:
        if e.get("decision") == "IGNORE":
            continue
        s = by_intent.setdefault(e.get("intent", "?"), [0, 0])
        s[0] += 1
        s[1] += e.get("outcome") in ("correct", "recovered")
    intent_rows = "".join(
        f"<tr><td>{escape(i)}</td><td class='num'>{n}</td><td class='num'>{100 * ok / n:.0f} %</td></tr>"
        for i, (n, ok) in sorted(by_intent.items(), key=lambda x: -x[1][0])
    ) or "<tr><td colspan='3' class='muted'>Event-level data not available (historical summary).</td></tr>"

    meta = [
        ("Operator", report.get("operator")), ("Dominant condition", report.get("condition")),
        ("Started", str(report.get("startedAt", ""))[:19].replace("T", " ")),
        ("Ended", str(report.get("endedAt", ""))[:19].replace("T", " ")),
        ("Duration", f"{report.get('durationMin', '-')} min"), ("Distance", f"{report.get('distance', '-')} m"),
        ("Battery used", f"{report.get('batteryUsed', '-')} %"), ("Commands", report.get("commandCount")),
    ]
    meta_html = "".join(f"<div><span>{escape(k)}</span><b>{escape(str(v))}</b></div>" for k, v in meta)
    synthetic = "<p class='note'>Seeded historical summary (synthetic baseline data).</p>" if report.get("synthetic") else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AURA mission report {mid}</title>
<style>
:root{{--bg:#fff;--fg:#141a22;--muted:#5d6876;--line:#e3e7ec;--ok:#1f8a4c;--bad:#c2372f;--warn:#b26b00;--acc:#2563eb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 Inter,system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:32px 24px}}
header{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid var(--fg);padding-bottom:16px}}
h1{{font-size:22px;margin:0}}h2{{font-size:15px;margin:28px 0 8px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}
.sub{{color:var(--muted);font-size:12px}}.verdict{{font-size:13px;font-weight:700;padding:6px 14px;border-radius:999px;text-transform:uppercase;letter-spacing:.08em}}
.v-pass{{background:#e5f5eb;color:var(--ok)}}.v-watch{{background:#fff3df;color:var(--warn)}}.v-fail{{background:#fde7e5;color:var(--bad)}}
.meta{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}}.meta div{{border:1px solid var(--line);border-radius:8px;padding:8px 10px}}
.meta span{{display:block;font-size:11px;color:var(--muted)}}table{{width:100%;border-collapse:collapse}}
td,th{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-size:11px;color:var(--muted);font-weight:600}}
.num,.mono{{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:13px}}.muted{{color:var(--muted)}}
.pill{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px}}.ok{{background:#e5f5eb;color:var(--ok)}}.bad{{background:#fde7e5;color:var(--bad)}}
.sev-critical,.sev-high{{background:#fde7e5;color:var(--bad)}}.sev-medium{{background:#fff3df;color:var(--warn)}}.sev-low,.sev-informational{{background:#e8effd;color:var(--acc)}}
.note{{color:var(--warn);font-size:12px}}footer{{margin-top:36px;color:var(--muted);font-size:11px;border-top:1px solid var(--line);padding-top:10px}}
@media (max-width:640px){{.meta{{grid-template-columns:repeat(2,1fr)}}}}@media print{{main{{padding:0}}}}
</style></head><body><main>
<header><div><div class="sub">AURA · Artaban · Powered by FIWARE (ARISE)</div><h1>Mission report {mid}</h1>
<div class="sub">Generated {escape(str(report.get('dateCreated', ''))[:19].replace('T', ' '))} UTC · source entity urn:ngsi-ld:MissionReport:{mid}</div></div>
<span class="verdict v-{escape(verdict_)}">{escape(verdict_)}</span></header>
{synthetic}<section class="meta">{meta_html}</section>
<h2>HRI KPIs</h2><table><tr><th>KPI</th><th>Value</th><th>Target</th><th>vs prev. 5 missions</th><th></th></tr>{''.join(rows)}</table>
<h2>Alerts</h2><table><tr><th>Severity</th><th>Rule</th><th>Description</th><th>Time</th><th>Status</th></tr>{alert_rows}</table>
<h2>Intent breakdown</h2><table><tr><th>Intent</th><th>Commands</th><th>Success</th></tr>{intent_rows}</table>
<footer>KPI definitions follow the AURA evaluation protocol (WP2/T2.2). Data path: ROS 2 skill → Orion-LD (NGSI-LD) → TROE/TimescaleDB → aura-monitor.</footer>
</main></body></html>"""
