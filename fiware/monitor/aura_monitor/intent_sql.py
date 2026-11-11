"""SQL for intent events with the robot adapter's re-publications merged in (one row per decision).

The adapter publishes an IntentEvent again, with the same event_id and header stamp, to add feedback:
outcome "false_activation", time_to_safe_stop_ms, ... Orion-LD's TROE keeps every sample, so counting
raw rows counts such a stop two or three times. This query returns the original sample of each decision
with the latest non-empty feedback fields merged into its ``compound``, the same rule as
``ros.merge_feedback``:

- With a header stamp on both samples, a sample belongs to the decision with the same mission_id,
  event_id and stamp (an aura_intent restart reuses event_ids, never stamps).
- Without one, a sample within FEEDBACK_WINDOW of an earlier one with the same key is a re-publication.

No dependencies: the monitor creates the ``aura_intent_events`` view from it and the Grafana dashboard
builder inlines it, so the dashboards do not depend on the monitor having created the view.
"""

INTENT_ID = "urn:ngsi-ld:HRIEvent:intent"
FEEDBACK_WINDOW = "10 minutes"  # ros.FEEDBACK_WINDOW_S
STAMP_TOLERANCE_S = 0.001


def merged_intent_events(attr_filter: str = "id LIKE '%/intentEvent'") -> str:
    """SELECT returning entityid, id, ts, observedat, valuetype and the merged compound of each decision."""
    def same(x: str, y: str, unstamped: str) -> str:
        """Samples x and y are the same decision; ``unstamped`` decides when a stamp is missing."""
        return (f"{y}.event_id = {x}.event_id AND {y}.mission_id IS NOT DISTINCT FROM {x}.mission_id AND CASE "
                f"WHEN {x}.stamp IS NOT NULL AND {y}.stamp IS NOT NULL "
                f"THEN abs({x}.stamp - {y}.stamp) <= {STAMP_TOLERANCE_S} ELSE {unstamped} END")

    return f"""WITH raw AS (
  SELECT entityid, id, ts, observedat, valuetype, compound::jsonb AS compound,
         compound->>'mission_id' AS mission_id, compound->>'event_id' AS event_id,
         nullif(coalesce((compound#>>'{{header,stamp,sec}}')::numeric, 0)
                + coalesce((compound#>>'{{header,stamp,nanosec}}')::numeric, 0) / 1e9, 0) AS stamp
  FROM attributes
  WHERE entityid = '{INTENT_ID}' AND {attr_filter} AND valuetype = 'Compound'
), orig AS (  -- the first sample of each decision
  SELECT a.* FROM raw a
  WHERE NOT EXISTS (SELECT 1 FROM raw b WHERE b.ts < a.ts
    AND {same("a", "b", f"b.ts >= a.ts - interval '{FEEDBACK_WINDOW}'")})
), member AS (  -- every sample, with the decision it belongs to (the latest matching first sample)
  SELECT a.ts AS orig_ts, a.event_id, a.mission_id, b.ts, b.compound FROM orig a
  JOIN raw b ON b.ts >= a.ts AND {same("a", "b", "TRUE")}
  WHERE NOT EXISTS (SELECT 1 FROM orig c WHERE c.ts > a.ts AND c.ts <= b.ts AND {same("b", "c", "TRUE")})
), feedback AS (  -- latest non-empty value of each feedback field
  SELECT orig_ts, event_id, mission_id,
    (array_agg(compound->>'outcome' ORDER BY ts DESC)
       FILTER (WHERE coalesce(compound->>'outcome', '') <> ''))[1] AS outcome,
    (array_agg((compound->>'correct')::boolean ORDER BY ts DESC)
       FILTER (WHERE (compound->>'correct')::boolean OR coalesce(compound->>'outcome', '') <> ''))[1] AS correct,
    (array_agg((compound->>'time_to_safe_stop_ms')::float ORDER BY ts DESC)
       FILTER (WHERE (compound->>'time_to_safe_stop_ms')::float > 0))[1] AS time_to_safe_stop_ms,
    (array_agg((compound->>'recovery_time_s')::float ORDER BY ts DESC)
       FILTER (WHERE (compound->>'recovery_time_s')::float > 0))[1] AS recovery_time_s
  FROM member GROUP BY orig_ts, event_id, mission_id
)
SELECT o.entityid, o.id, o.ts, o.observedat, o.valuetype,
       o.compound || jsonb_strip_nulls(jsonb_build_object(
         'outcome', f.outcome, 'correct', f.correct,
         'time_to_safe_stop_ms', f.time_to_safe_stop_ms, 'recovery_time_s', f.recovery_time_s)) AS compound
FROM orig o LEFT JOIN feedback f
  ON f.orig_ts = o.ts AND f.event_id = o.event_id AND f.mission_id IS NOT DISTINCT FROM o.mission_id"""
