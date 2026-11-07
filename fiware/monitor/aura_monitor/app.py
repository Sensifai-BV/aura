"""aura-monitor: the AURA analytics plane on top of the ARISE all-in-one middleware.

Subscribes to Orion-LD, derives HRI KPIs per mission (HRISession), raises Alert entities,
produces MissionReports, and serves the AURA web console (REST + SSE).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from . import reports, ros
from .alerts import AlertEngine
from .db import Historian
from .kpi import Mission, compute
from .ngsi import Orion, now_iso, prop, rel, simplify

log = logging.getLogger("aura_monitor")

ORION_URL = os.environ.get("ORION_URL", "http://localhost:1026")
MINTAKA_URL = os.environ.get("MINTAKA_URL", "http://localhost:8080").rstrip("/")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
NOTIFY_URL = os.environ.get("MONITOR_NOTIFY_URL", "http://localhost:8090/ngsi-ld/notify")
PG = "host={PGHOST} port={PGPORT} user={PGUSER} password={PGPASSWORD} dbname={PGDATABASE}".format(
    **{k: os.environ.get(k, d) for k, d in
       dict(PGHOST="localhost", PGPORT="5432", PGUSER="orion", PGPASSWORD="orion", PGDATABASE="orion").items()})

SUBSCRIPTION_ID = "urn:ngsi-ld:Subscription:aura-monitor"
# Robot / AirQualityObserved / HRIEvent / HRIModuleStatus come from ROS 2 through Orion-LD's DDS bridge;
# HRISessionKPI and Alert are followed for operator actions taken in WireCloud.
WATCHED = ["Robot", "AirQualityObserved", "HRIEvent", "HRISessionKPI", "Alert"]
KPI_ID = "urn:ngsi-ld:HRISessionKPI:artaban-01"
KPI_WINDOW_S = 15 * 60
SHIFT_S = 8 * 3600
ROBOT_ID = "urn:ngsi-ld:Robot:artaban-01"
AIR_ID = "urn:ngsi-ld:AirQualityObserved:artaban-01"
INTENT_ID = "urn:ngsi-ld:HRIEvent:intent"
ENTITY_TYPES = ["Robot", "AirQualityObserved", "HRIEvent", "HRIModuleStatus",
                "HRISessionKPI", "HRISession", "MissionReport", "Alert"]
KPI_KEYS = ["commandSuccessRate", "falseActivationRate", "rejectionRate", "confirmationLoopRate", "meanLatency",
            "p95Latency", "meanConfidence", "timeToSafeStop", "recoveryTime", "operatorInterventions", "calibrationError"]


class Monitor:
    def __init__(self) -> None:
        self.orion = Orion(ORION_URL)
        self.db = Historian(PG)
        self.http = httpx.AsyncClient(timeout=5.0)
        self.state: dict = {"robot": {}, "air": {}, "session": {}}
        self.events: deque = deque(maxlen=300)
        self.mission: Mission | None = None
        self.last_event_id: str | None = None
        self.last_seen: dict[str, float] = {}
        self.clients: set[asyncio.Queue] = set()
        self.last_report_request: str | None = None
        self.shift: deque = deque(maxlen=6000)        # intent events of the last shift, each with _rx
        self.module_rx: dict[str, deque] = {}       # HRIEvent module -> notification times
        self.alerts = AlertEngine(self.orion, self._on_alert)
        self.ready = False
        self.tasks: list[asyncio.Task] = []

    # -- SSE ------------------------------------------------------------------
    def broadcast(self, msg: dict) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def _on_alert(self, alert: dict) -> None:
        if alert.get("status") == "active" and self.mission and alert.get("missionId") == self.mission.mission_id:
            self.mission.alerts += 1
        self.broadcast({"type": "alert", "data": alert})

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        await self.db.open()
        self.tasks.append(asyncio.create_task(self._bootstrap()))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()

    async def _bootstrap(self) -> None:
        while True:
            try:
                await self.orion.version()
                break
            except Exception:  # noqa: BLE001
                log.info("waiting for Orion-LD …")
                await asyncio.sleep(3)
        for _ in range(60):
            if await self.db.ensure_views():
                break
            await asyncio.sleep(3)
        robot, air = await self.orion.get(ROBOT_ID), await self.orion.get(AIR_ID)
        self.state["robot"] = ros.robot(simplify(robot)) if robot else {}
        self.state["air"] = ros.environment(simplify(air)) if air else {}
        await self.alerts.load()
        kpi = await self.orion.get(KPI_ID)
        self.last_report_request = (simplify(kpi).get("reportRequestedAt") if kpi else None) or ""
        mid = self.state["robot"].get("missionId")
        if mid:
            await self._open_mission(mid, rebuild=True)
        recent = (await self.db.recent_events(5000))[::-1]
        for e in recent:
            e["_rx"] = datetime.fromisoformat(e["time"]).timestamp()
            if time.time() - e["_rx"] <= SHIFT_S:
                self.shift.append(e)
        for e in recent[-60:]:
            self.events.appendleft(e)
        intent = await self.orion.get(INTENT_ID)
        last = ros.intent_event(simplify(intent).get("intentEvent")) if intent else None
        self.last_event_id = last["eventId"] if last else None
        while True:
            try:
                await self.orion.ensure_subscription(SUBSCRIPTION_ID, WATCHED, NOTIFY_URL)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("subscription: %s", exc)
                await asyncio.sleep(3)
        self.ready = True
        log.info("aura-monitor ready (mission %s)", mid)
        self.tasks.append(asyncio.create_task(self._session_loop()))

    async def _open_mission(self, mission_id: str, rebuild: bool = False) -> None:
        self.mission = Mission(mission_id, now_iso())
        self.alerts.raised_in_mission = 0
        if rebuild:
            try:
                events = await self.db.mission_events(mission_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not rebuild mission %s: %s", mission_id, exc)
                events = []
            for ev in events:
                self.mission.add(ev)
            if events:
                self.mission.started_at = datetime.fromtimestamp(events[0]["_rx"], timezone.utc).isoformat().replace("+00:00", "Z")
            existing = await self.orion.get(f"urn:ngsi-ld:HRISession:{mission_id}")
            if existing:
                self.mission.started_at = simplify(existing).get("startedAt") or self.mission.started_at
        self.mission.observe_robot(self.state["robot"])
        log.info("tracking mission %s (%d events)", mission_id, len(self.mission.events))

    async def _close_mission(self) -> None:
        m = self.mission
        if not m:
            return
        await self._publish_session(status="completed")
        entity = reports.build_entity(m, final=True)
        await self.orion.upsert([entity])
        report = simplify(entity)
        self.broadcast({"type": "report", "data": report})
        log.info("mission %s closed → report verdict %s", m.mission_id, report.get("verdict"))

    async def _publish_session(self, status: str = "running") -> None:
        m = self.mission
        if not m:
            return
        ts = now_iso()
        k = m.kpis()
        attrs = {key: prop(v, ts) for key, v in k.items()}
        attrs.update({
            "missionId": prop(m.mission_id, ts), "status": prop(status, ts), "startedAt": prop(m.started_at, ts),
            "condition": prop(m.dominant_condition, ts),
            "robot": rel("urn:ngsi-ld:Robot:artaban-01"),
        })
        if m.operator:
            attrs["operator"] = rel(f"urn:ngsi-ld:Operator:{m.operator}")
        entity = {"id": f"urn:ngsi-ld:HRISession:{m.mission_id}", "type": "HRISession", **attrs}
        await self.orion.upsert([entity])
        self.state["session"] = simplify(entity)
        self.broadcast({"type": "entity", "entityType": "HRISession", "id": entity["id"], "data": self.state["session"]})
        await self.alerts.evaluate("session", self.state["session"], m.mission_id)

    async def _session_loop(self) -> None:
        tick = 0
        while True:
            try:
                if tick % 60 == 0:  # every 5 min
                    n = await self.db.register_dds_entities()
                    if n:
                        log.info("registered %d DDS-fed entities in TROE for Mintaka", n)
                tick += 1
                await self._publish_session()
                await self._publish_kpi()
            except Exception as exc:  # noqa: BLE001
                log.warning("session publish failed: %s", exc)
            await asyncio.sleep(5)

    async def _publish_kpi(self) -> None:
        """HRISessionKPI: the design's KPI-tile entity, over a rolling 15 min window (+ shift breakdowns)."""
        now = time.time()
        while self.shift and now - self.shift[0]["_rx"] > SHIFT_S:
            self.shift.popleft()
        window = [e for e in self.shift if now - e["_rx"] <= KPI_WINDOW_S]
        k = compute(window, now)
        fa = sum(1 for e in window if e.get("outcome") == "false_activation")
        intents: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for e in self.shift:
            if e.get("decision") != "IGNORE":
                intents[e.get("intent", "unknown")] = intents.get(e.get("intent", "unknown"), 0) + 1
            reason = rejection_reason(e)
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        rates = {}
        for m, q in self.module_rx.items():
            while q and now - q[0] > 300:
                q.popleft()
            rates[m] = round(len(q) / 5.0, 2)
        ts = now_iso()
        values = {
            "avgConfidence": k["meanConfidence"], "commandSuccessRate": k["commandSuccessRate"],
            "falseActivationRate": k["falseActivationRate"], "falseActivationsPerHour": round(fa * 3600 / KPI_WINDOW_S, 1),
            "meanTimeToSafeStop": k["timeToSafeStop"], "e2eLatencyMs": k["meanLatency"], "p95LatencyMs": k["p95Latency"],
            "confirmationLoopRate": k["confirmationLoopRate"], "operatorInterventions": k["operatorInterventions"],
            "rejectionRate": k["rejectionRate"], "commandCount": k["commandCount"],
            "eventsPerMin": round(len(window) / (KPI_WINDOW_S / 60), 2), "windowMinutes": KPI_WINDOW_S // 60,
            "missionId": self.mission.mission_id if self.mission else "-",
            "intentCounts": intents or {"none": 0}, "rejectionReasons": reasons or {"none": 0},
            "moduleEventRates": rates or {"none": 0},
        }
        if not k["commandCount"]:
            # No commands in the window: publish counts only, so trends never dip to a fake 0.
            values = {a: v for a, v in values.items() if a in ("commandCount", "eventsPerMin", "windowMinutes", "missionId",
                                                               "intentCounts", "rejectionReasons", "moduleEventRates")}
        await self.orion.upsert([{"id": KPI_ID, "type": "HRISessionKPI", **{a: prop(v, ts) for a, v in values.items()}}])

    async def generate_report(self) -> dict | None:
        if not self.mission:
            return None
        entity = reports.build_entity(self.mission, final=False)
        await self.orion.upsert([entity])
        report = simplify(entity)
        self.broadcast({"type": "report", "data": report})
        log.info("interim report for %s", self.mission.mission_id)
        return report

    # -- notifications ----------------------------------------------------------
    async def on_notification(self, body: dict) -> None:
        for raw in body.get("data", []):
            etype = raw.get("type", "").rsplit("/", 1)[-1]
            if etype == "HRIEvent":
                self.module_rx.setdefault(raw["id"].rsplit(":", 1)[-1], deque()).append(time.time())
            elif etype == "Alert":
                # Operators acknowledge / resolve alerts in WireCloud by PATCHing the Alert in Orion-LD.
                self.alerts.sync(simplify(raw))
            elif etype == "HRISessionKPI":
                requested = simplify(raw).get("reportRequestedAt")
                if requested and self.last_report_request is not None and requested != self.last_report_request:
                    self.last_report_request = requested
                    await self.generate_report()
            mid = self.mission.mission_id if self.mission else None
            if etype == "Robot":
                data = ros.robot(simplify(raw))
                self.last_seen[etype] = time.time()
                self.state["robot"] = {**self.state["robot"], **{k: v for k, v in data.items() if v is not None}}
                data = self.state["robot"]
                new_mid = data.get("missionId")
                if new_mid and new_mid != mid:
                    if self.mission:
                        await self._close_mission()
                    await self._open_mission(new_mid)
                    mid = new_mid
                if self.mission:
                    self.mission.observe_robot(data)
                await self.alerts.evaluate("robot", data, mid)
            elif etype == "AirQualityObserved":
                data = self.state["air"] = ros.environment(simplify(raw))
                await self.alerts.evaluate("air", data, mid)
            elif etype == "HRIEvent" and raw.get("id") == INTENT_ID:
                ent = simplify(raw)
                ev = ros.intent_event(ent.get("intentEvent"))
                if ev and ev["eventId"] != self.last_event_id:
                    self.last_event_id = ev["eventId"]
                    ev["time"] = ent.get("_observedAt") or now_iso()
                    if self.mission and ev.get("missionId") == self.mission.mission_id:
                        self.mission.add(dict(ev))
                    self.events.appendleft(ev)
                    self.shift.append({**ev, "_rx": time.time()})
                    self.broadcast({"type": "event", "data": ev})
                continue
            else:
                continue
            self.broadcast({"type": "entity", "entityType": etype, "id": data["id"], "data": data})

    # -- queries ------------------------------------------------------------------
    async def history(self, entity: str, attrs: list[str], minutes: int) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            r = await self.http.get(f"{MINTAKA_URL}/temporal/entities/{entity}", params={
                "attrs": ",".join(attrs), "timerel": "after", "timeAt": since, "options": "temporalValues"})
            if r.status_code in (200, 206):
                body = r.json()
                out = {}
                for a in attrs:
                    vals = (body.get(a) or {}).get("values", []) if isinstance(body.get(a), dict) else []
                    out[a] = sorted([[t, v] for v, t in vals])
                if any(out.values()):
                    return {"source": "mintaka", **_downsample(out)}
        except Exception as exc:  # noqa: BLE001
            log.debug("mintaka history failed: %s", exc)
        return {"source": "timescale", **_downsample(await self.db.history(entity, attrs, minutes))}

    async def health(self) -> dict:
        comps = []

        async def check(name: str, role: str, coro):
            t0 = time.perf_counter()
            try:
                detail = await coro
                comps.append({"name": name, "role": role, "status": "up", "detail": detail,
                              "latencyMs": round((time.perf_counter() - t0) * 1000, 1)})
            except Exception as exc:  # noqa: BLE001
                comps.append({"name": name, "role": role, "status": "down", "detail": str(exc)[:160], "latencyMs": None})

        async def orion():
            v = await self.orion.version()
            return f"Orion-LD {v.get('orionld version')}"

        async def mongo():
            n = await self.orion.count("Robot")
            return f"{n} Robot entity · Orion storage reachable"

        async def timescale():
            return f"{await self.db.row_count():,} TROE attribute rows"

        async def mintaka():
            r = await self.http.get(f"{MINTAKA_URL}/temporal/entities/urn:ngsi-ld:Robot:artaban-01",
                                    params={"attrs": "batteryLevel", "lastN": 1})
            r.raise_for_status()
            return "NGSI-LD temporal API"

        async def grafana():
            r = await self.http.get(f"{GRAFANA_URL}/api/health")
            r.raise_for_status()
            return f"Grafana {r.json().get('version', '')}"

        async def source():
            age = time.time() - self.last_seen.get("Robot", 0)
            if age > 10:
                raise RuntimeError(f"no robot telemetry for {age:.0f}s")
            return f"telemetry {age:.1f}s ago"

        await asyncio.gather(
            check("Artaban / ROS 2 skill", "source", source()),
            check("Orion-LD", "context broker", orion()),
            check("MongoDB", "context storage", mongo()),
            check("TimescaleDB (TROE)", "historian", timescale()),
            check("Mintaka", "temporal API", mintaka()),
            check("Grafana", "dashboards & alerting", grafana()),
        )
        comps.append({"name": "aura-monitor", "role": "analytics plane", "status": "up" if self.ready else "starting",
                      "detail": f"{len(self.clients)} live clients", "latencyMs": 0})
        counts = dict(zip(ENTITY_TYPES, await asyncio.gather(*(self.orion.count(t) for t in ENTITY_TYPES))))
        return {"components": comps, "subscriptions": len(await self.orion.subscriptions()), "entities": counts,
                "mission": self.mission.mission_id if self.mission else None}


def rejection_reason(e: dict) -> str | None:
    """Why a command did not go through as spoken (design: 'rejection reasons' bar chart)."""
    if e.get("decision") == "IGNORE":
        return "not_addressed"
    if e.get("decision") == "REJECT":
        cond = e.get("condition") or "clean"
        return "low_confidence" if cond == "clean" else "low_confidence_" + cond.replace("+", "_")
    if e.get("outcome") == "misrecognized":
        return "operator_cancelled"
    if e.get("outcome") == "false_activation":
        return "false_activation"
    return None


def _downsample(series: dict, max_points: int = 400) -> dict:
    out = {}
    for k, pts in series.items():
        step = max(1, len(pts) // max_points)
        out[k] = pts[::step]
    return out


monitor = Monitor()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="aura-monitor", lifespan=lifespan)


@app.post("/ngsi-ld/notify", status_code=204)
async def notify(request: Request):
    await monitor.on_notification(await request.json())


@app.get("/api/state")
async def api_state():
    return {**monitor.state,
            "mission": monitor.mission.mission_id if monitor.mission else None, "ready": monitor.ready}


@app.get("/api/stream")
async def api_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    monitor.clients.add(q)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(msg, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            monitor.clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/events")
async def api_events(limit: int = 50):
    events = list(monitor.events)[:limit]
    if len(events) < limit:
        try:
            events = await monitor.db.recent_events(limit)
        except Exception:  # noqa: BLE001
            pass
    return events


@app.get("/api/history")
async def api_history(entity: str, attrs: str, minutes: int = 15):
    return await monitor.history(entity, [a for a in attrs.split(",") if a], max(1, min(minutes, 24 * 60)))


@app.get("/api/sessions")
async def api_sessions():
    reps = [simplify(e) for e in await monitor.orion.query("MissionReport", limit=1000)]
    reps.sort(key=lambda r: str(r.get("endedAt") or r.get("_observedAt") or ""))
    return {"reports": reps, "running": monitor.state.get("session")}


@app.get("/api/analytics")
async def api_analytics():
    return await monitor.db.analytics()


@app.get("/api/alerts")
async def api_alerts(status: str = "active"):
    alerts = [simplify(e) for e in await monitor.orion.query("Alert", limit=1000)]
    if status != "all":
        alerts = [a for a in alerts if a.get("status") in ("active", "acknowledged")]
    alerts.sort(key=lambda a: str(a.get("dateIssued", "")), reverse=True)
    return alerts


@app.post("/api/alerts/{alert_id}/{action}")
async def api_alert_action(alert_id: str, action: str):
    status = {"ack": "acknowledged", "resolve": "resolved"}.get(action)
    if not status:
        raise HTTPException(404)
    alert = await monitor.alerts.set_status(alert_id, status)
    if not alert:
        raise HTTPException(404, "alert not found")
    return alert


@app.post("/api/reports")
async def api_generate_report():
    report = await monitor.generate_report()
    if not report:
        raise HTTPException(409, "no running mission")
    return report


@app.get("/reports/{mission_id}", response_class=HTMLResponse)
async def report_page(mission_id: str):
    e = await monitor.orion.get(f"urn:ngsi-ld:MissionReport:{mission_id}")
    if not e:
        if monitor.mission and monitor.mission.mission_id == mission_id:
            e = reports.build_entity(monitor.mission, final=False)
        else:
            raise HTTPException(404, "report not found")
    report = simplify(e)
    alerts = [simplify(a) for a in await monitor.orion.query("Alert", q=f'missionId=="{mission_id}"')]
    alerts.sort(key=lambda a: str(a.get("dateIssued", "")))
    try:
        events = await monitor.db.mission_events(mission_id)
    except Exception:  # noqa: BLE001
        events = []
    prev = [simplify(r) for r in await monitor.orion.query("MissionReport", limit=1000)]
    prev = sorted((r for r in prev if str(r.get("missionId", "")) < mission_id), key=lambda r: r.get("missionId"))[-5:]
    baseline = {k: sum(r.get(k, 0) for r in prev) / len(prev) for k in KPI_KEYS} if prev else None
    return reports.render_html(report, alerts, events, baseline)


@app.get("/api/health")
async def api_health():
    return await monitor.health()


@app.get("/api/entities")
async def api_entities(type: str | None = None, limit: int = 50):  # noqa: A002 - NGSI-LD vocabulary
    return await monitor.orion.query(type, limit=min(limit, 200))


@app.post("/api/grafana-webhook", status_code=204)
async def grafana_webhook(request: Request):
    body = await request.json()
    log.info("grafana alert: %s %s", body.get("status"), body.get("title"))
    monitor.broadcast({"type": "grafana", "data": {"status": body.get("status"), "title": body.get("title"),
                                                   "message": body.get("message")}})


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), access_log=False)


if __name__ == "__main__":
    main()
