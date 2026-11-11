"""Analytics over Orion-LD TROE tables (TimescaleDB)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from . import ros
from .intent_sql import INTENT_ID, merged_intent_events
from .kpi import calibration_bins

log = logging.getLogger(__name__)

CTX = "https://uri.etsi.org/ngsi-ld/default-context/"
# INTENT_ID (urn:ngsi-ld:HRIEvent:intent): attribute intentEvent = aura_interfaces/IntentEvent

# Convenience views for ad-hoc SQL / Grafana explore (dashboards do not depend on them).
VIEWS = f"""
CREATE INDEX IF NOT EXISTS attributes_entity_attr_ts ON attributes (entityid, id, ts DESC);
CREATE OR REPLACE VIEW aura_attributes AS
  SELECT entityid, split_part(id, '/', -1) AS attr, coalesce(observedat, ts) AS time,
         valuetype, number, text, boolean, compound, unitcode
  FROM attributes;
DROP VIEW IF EXISTS aura_intent_events;
CREATE VIEW aura_intent_events AS
  -- one row per decision: the adapter's re-publications (same event_id and stamp) are merged in
  SELECT coalesce(observedat, ts) AS time, ts,
         compound->>'mission_id' AS mission_id, compound->>'event_id' AS event_id, compound->>'intent' AS intent,
         (ARRAY['ACCEPT','CONFIRM','REJECT','IGNORE'])[(compound->>'decision')::int + 1] AS decision,
         compound->>'outcome' AS outcome, (compound->>'correct')::boolean AS correct,
         (compound->>'confidence')::float AS confidence, (compound->>'latency_ms')::float AS latency_ms,
         compound->>'modality' AS modality, compound->>'acoustic_condition' AS condition,
         compound->>'operator_id' AS operator_id, (compound->>'noise_db')::float AS noise_db,
         (compound->>'time_to_safe_stop_ms')::float AS time_to_safe_stop_ms, compound
  FROM ({merged_intent_events(f"id = '{CTX}intentEvent'")}) merged;
"""


class Historian:
    def __init__(self, conninfo: str):
        self.pool = AsyncConnectionPool(conninfo, min_size=1, max_size=5, open=False,
                                        kwargs={"row_factory": dict_row, "autocommit": True})

    async def open(self) -> None:
        await self.pool.open(wait=False)

    async def ping(self) -> bool:
        async with self.pool.connection() as conn:
            await conn.execute("select 1")
        return True

    async def ensure_views(self) -> bool:
        try:
            async with self.pool.connection() as conn:
                exists = await (await conn.execute("select to_regclass('public.attributes') as t")).fetchone()
                if not exists or not exists["t"]:
                    return False
                await conn.execute(VIEWS)
            return True
        except Exception as exc:  # noqa: BLE001 - keep monitor running without the historian
            log.warning("could not create views: %s", exc)
            return False

    async def register_dds_entities(self) -> int:
        """Workaround for Orion-LD 1.10: its DDS bridge writes attribute history to TROE but no `entities` row,
        so Mintaka answers 404 for DDS-fed entities. Add the missing rows (type from the NGSI-LD id)."""
        sql = f"""
            INSERT INTO entities (instanceid, ts, opmode, id, type)
            SELECT 'urn:ngsi-ld:attribute:instance:dds-' || md5(a.entityid), min(a.ts), 'Create', a.entityid,
                   '{CTX}' || split_part(a.entityid, ':', 3)
            FROM attributes a
            WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = a.entityid)
            GROUP BY a.entityid
            ON CONFLICT DO NOTHING"""
        try:
            async with self.pool.connection() as conn:
                cur = await conn.execute(sql)
                return cur.rowcount or 0
        except Exception as exc:  # noqa: BLE001 - tables may not exist yet
            log.warning("could not register DDS entities in TROE: %s", exc)
            return 0

    async def mission_events(self, mission_id: str) -> list[dict]:
        sql = ("select extract(epoch from coalesce(observedat, ts)) as t, compound from attributes "
               "where entityid = %s and id = %s and compound->>'mission_id' = %s order by ts")
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, (INTENT_ID, CTX + "intentEvent", mission_id))).fetchall()
        out = []
        for r in rows:
            ev = ros.intent_event(r["compound"])
            if ev:
                ev["_rx"] = float(r["t"])
                out.append(ev)
        return ros.merge_feedback(out)

    async def recent_events(self, limit: int) -> list[dict]:
        sql = ("select coalesce(observedat, ts) as t, compound from attributes "
               "where entityid = %s and id = %s and valuetype = 'Compound' order by ts desc limit %s")
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, (INTENT_ID, CTX + "intentEvent", limit))).fetchall()
        out = []
        for r in rows[::-1]:  # oldest first, so feedback merges into the original event
            ev = ros.intent_event(r["compound"])
            if ev:
                out.append({**ev, "time": r["t"].replace(tzinfo=timezone.utc).isoformat(),
                            "_t": r["t"].replace(tzinfo=timezone.utc).timestamp()})
        return [{k: v for k, v in e.items() if k != "_t"} for e in ros.merge_feedback(out, "_t")][::-1]

    async def history(self, entity_id: str, attrs: list[str], minutes: int) -> dict:
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
        sql = ("select coalesce(observedat, ts) as t, split_part(id, '/', -1) as attr, number, text, boolean, compound "
               "from attributes where entityid = %s and id = any(%s) and coalesce(observedat, ts) > %s order by 1")
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, (entity_id, [CTX + a for a in attrs], since))).fetchall()
        out: dict = {a: [] for a in attrs}
        for r in rows:
            v = r["number"] if r["number"] is not None else r["boolean"] if r["boolean"] is not None else r["text"] if r["text"] is not None else r["compound"]
            out.setdefault(r["attr"], []).append([r["t"].replace(tzinfo=timezone.utc).isoformat(), v])
        return out

    async def analytics(self) -> dict:
        base = (f"from aura_intent_events where decision <> 'IGNORE'")
        q = {
            "conditions": f"""select condition, count(*) as commands,
                round(100.0 * avg((outcome in ('correct','recovered'))::int), 1) as "successRate",
                round(avg(confidence)::numeric, 3) as "meanConfidence",
                round(10.0 * avg((decision = 'CONFIRM')::int), 2) as "loopRate",
                round(100.0 * avg((decision = 'REJECT')::int), 1) as "rejectionRate" {base} group by 1 order by 1""",
            "intents": f"""select intent, count(*) as count,
                round(100.0 * avg((outcome in ('correct','recovered'))::int), 1) as "successRate",
                round(avg(confidence)::numeric, 3) as "meanConfidence" {base} group by 1 order by 2 desc""",
            "modalities": f"""select modality, count(*) as count,
                round(100.0 * avg((outcome in ('correct','recovered'))::int), 1) as "successRate",
                round(avg(latency_ms)::numeric) as "meanLatency" {base} group by 1 order by 2 desc""",
            "operators": f"""select operator_id as "operatorId", count(*) as commands,
                round(100.0 * avg((outcome in ('correct','recovered'))::int), 1) as "successRate",
                round(avg(latency_ms)::numeric) as "meanLatency",
                round(100.0 * avg((outcome = 'false_activation')::int), 2) as "falseActivationRate" {base} group by 1 order by 1""",
            "hotspots": f"""select intent, condition, count(*) as commands,
                round(100.0 * avg((outcome not in ('correct','recovered'))::int), 1) as "failureRate"
                {base} group by 1, 2 having count(*) >= 5 order by 4 desc limit 8""",
        }
        out: dict = {}
        async with self.pool.connection() as conn:
            for key, sql in q.items():
                rows = await (await conn.execute(sql)).fetchall()
                out[key] = [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items()} for r in rows]
            cal = await (await conn.execute(f"select confidence, correct {base}")).fetchall()
        out["calibration"] = calibration_bins([{"confidence": r["confidence"], "correct": r["correct"]} for r in cal])
        out["totalCommands"] = len(cal)
        return out

    async def row_count(self) -> int:
        async with self.pool.connection() as conn:
            r = await (await conn.execute("select count(*) as n from attributes")).fetchone()
        return int(r["n"])
