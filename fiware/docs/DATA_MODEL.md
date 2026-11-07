# AURA × ARISE — ROS 2 ⇄ NGSI-LD data model and service contracts

Robot and AURA module data reaches FIWARE only over **ROS 2 / DDS**: Orion-LD runs with its native DDS bridge
(`-wip dds`, ARISE D2.1 "Phase I / legacy mode") and maps every topic listed in `orion/config-dds.json` onto one
NGSI-LD attribute. The attribute value is the **whole ROS message** (`std_msgs/*` arrive as `{"data": …}`), and it
carries no `observedAt`: its time is the broker's `modifiedAt` (ask for `options=sysAttrs`, Mintaka
`timeproperty=modifiedAt`). Only the topics in the mapping are bridged (allowlist).

`aura-monitor` (the analytics plane) derives KPIs, alerts and reports and writes them back as plain NGSI-LD
(`observedAt` set). WireCloud widgets, Grafana and `aura-monitor` all read Orion-LD / TimescaleDB (TROE) / Mintaka.

```
ROS 2 nodes (Artaban, AURA modules)  ──DDS──▶  Orion-LD (-wip dds)  ──TROE──▶ TimescaleDB ──▶ Mintaka / Grafana
   today: artaban-ros2 publisher                 │ subscriptions
                                                 ├──▶ aura-monitor ──▶ HRISession · HRISessionKPI · Alert · MissionReport
                                                 └──▶ ngsi-proxy ──▶ WireCloud (AURA NGSI-LD source operator → widgets)
```

## ROS 2 topic contract

| ROS 2 topic | Message type | NGSI-LD entity · attribute | Producer |
| --- | --- | --- | --- |
| `/battery_state` | `sensor_msgs/BatteryState` | `urn:ngsi-ld:Robot:artaban-01` · `battery` | Artaban |
| `/odom` | `nav_msgs/Odometry` | `Robot:artaban-01` · `odometry` | Artaban |
| `/imu/data` | `sensor_msgs/Imu` | `Robot:artaban-01` · `imu` | Artaban |
| `/artaban/status` | `aura_interfaces/ArtabanStatus` | `Robot:artaban-01` · `status` | Artaban |
| `/artaban/environment` | `aura_interfaces/EnvironmentReading` | `urn:ngsi-ld:AirQualityObserved:artaban-01` · `reading` | inspection payload |
| `/aura_partial_transcript` | `std_msgs/String` | `HRIEvent:stt` · `partialTranscript` | `aura_stt` |
| `/aura_transcript` | `std_msgs/String` | `HRIEvent:stt` · `transcript` | `aura_stt` |
| `/aura/gesture/intent` | `aura_interfaces/GestureIntent` | `HRIEvent:gesture` · `gestureIntent` | `aura_gesture` |
| `/aura/intent/event` | `aura_interfaces/IntentEvent` | `HRIEvent:intent` · `intentEvent` | AURA intent node |
| `/aura/speaker/say` | `std_msgs/String` | `HRIEvent:speaker` · `say` | AURA speaker (TTS) |
| `/aura/<module>/status` | `aura_interfaces/ModuleStatus` | `urn:ngsi-ld:HRIModuleStatus:<module>` · `status` | each AURA module |

`<module>` ∈ `stt` `intent` `gesture` `speaker`. Messages are defined in `src/aura_interfaces/msg/`.

- `ModuleStatus`: `level` 0 OK / 1 WARN / 2 ERROR (→ healthy / degraded / down) and the metrics as two parallel string
  arrays, `keys[]` and `values[]` (same length and order): `rate`, `rate_unit` (`Hz` for streams, `/min` for events),
  `latency_ms`; the intent node adds `accept_threshold`, `reject_threshold`, `recognition_mode`, `acoustic_condition`,
  `engagement`, `operator_id`. It is flat on purpose: Orion-LD's DDS bridge cannot read a sequence of structs, so
  `diagnostic_msgs/DiagnosticStatus` (a list of `KeyValue`) from a stock ROS 2 node never reaches the broker.
  WireCloud and the Grafana queries read both shapes.
- `IntentEvent.decision`: 0 ACCEPT, 1 CONFIRM, 2 REJECT, 3 IGNORE. `outcome`: `correct` `recovered` `misrecognized`
  `false_activation` `rejected`; `correct` and `outcome` come from operator feedback (ground truth for calibration).
- `BatteryState.percentage` is 0..1; `power_supply_status` 1 = charging.
- Orion-LD returns `"` inside strings escaped as `\"`; consumers unescape.

Real modules replace the stand-in publisher by publishing the same topics on DDS domain 0; nothing else changes.
The stand-in also listens on `/aura_sim/scenario` (`std_msgs/String`) for test conditions
(`noise_spike` `mask_on` `gas_leak` `motor_overheat` `comms_degraded` `low_battery` `estop` `none`).

## Entities written by aura-monitor (plain NGSI-LD, `observedAt` set)

### `HRISession` — `urn:ngsi-ld:HRISession:<missionId>` (every 5 s)

`missionId` (from `ArtabanStatus.mission_id`, e.g. `M20261029-1903`), `status` (`running` `completed`), `startedAt`,
`robot`/`operator` (Relationship), `condition`, and the mission KPIs (Number): `commandCount`, `commandSuccessRate` %,
`falseActivationRate` %, `rejectionRate` %, `windowRejectionRate` % (last 5 min), `confirmationLoopRate` (per 10
commands), `meanLatency` ms, `p95Latency` ms, `meanConfidence`, `timeToSafeStop` ms, `recoveryTime` s,
`operatorInterventions`, `calibrationError` (ECE, 0–1).

### `HRISessionKPI` — `urn:ngsi-ld:HRISessionKPI:artaban-01` (every 5 s, rolling 15 min window)

`avgConfidence`, `commandSuccessRate`, `falseActivationRate`, `falseActivationsPerHour`, `meanTimeToSafeStop`,
`e2eLatencyMs`, `p95LatencyMs`, `confirmationLoopRate`, `operatorInterventions`, `rejectionRate`, `commandCount`,
`eventsPerMin`, `windowMinutes`, `missionId`, and compounds `intentCounts` (last 8 h), `rejectionReasons` (last 8 h:
`low_confidence[_<condition>]`, `not_addressed`, `operator_cancelled`, `false_activation`), `moduleEventRates`
(HRIEvent updates per minute, last 5 min). With no commands in the window only the counts are published.
`reportRequestedAt` (written by the WireCloud *Mission reports* widget) asks aura-monitor for an interim `MissionReport`.

### `MissionReport` — `urn:ngsi-ld:MissionReport:<missionId>` (at mission end, or on request)

The `HRISession` KPIs plus `dateCreated`, `startedAt`, `endedAt`, `durationMin`, `operator`, `condition`, `alertsCount`,
`distance` m, `batteryUsed` %, `inspectionPoints`, `verdict` (`pass` `watch` `fail`), `status` (`final` `interim`),
`reportUrl` (printable page on aura-monitor).

### `Alert` — `urn:ngsi-ld:Alert:<rule>-<id>` (FIWARE smart data model `Alert`)

`category` (`hri` `robot` `environment`), `subCategory` (rule id), `severity` (`informational` `low` `medium` `high`
`critical`), `description`, `dateIssued`, `validTo` (when resolved), `status` (`active` `acknowledged` `resolved`),
`alertSource` (Relationship), `value`, `threshold`, `missionId`. WireCloud acknowledges / resolves an alert by
PATCHing `status`; aura-monitor follows the change.

### KPI definitions (over the `IntentEvent`s of one mission)

Commands = events with `decision != IGNORE`.
`commandSuccessRate` = (`correct` + `recovered`) / commands · `falseActivationRate` = `false_activation` / commands ·
`rejectionRate` = `REJECT` / commands (`windowRejectionRate`: same over the last 5 min, needs ≥ 8 commands) ·
`confirmationLoopRate` = `CONFIRM` per 10 commands · `meanLatency`/`p95Latency` over commands ·
`timeToSafeStop` = mean `time_to_safe_stop_ms` · `recoveryTime` = mean `recovery_time_s` ·
`operatorInterventions` = accepted stop/pause/cancel + `misrecognized` · `calibrationError` = 10-bin expected
calibration error of `confidence` vs `correct`.

## Alert rules (aura-monitor; Grafana mirrors the robot / environment ones)

| rule | severity | condition |
| --- | --- | --- |
| `rejectionRateSpike` | high | `windowRejectionRate` > 10 % |
| `confirmationLoopsElevated` | medium | `confirmationLoopRate` > 2.0 per 10 cmds |
| `latencyAbnormal` | medium | `p95Latency` > 600 ms |
| `falseActivationHigh` | high | `falseActivationRate` > 5 % |
| `confidenceDrift` | low | `meanConfidence` < 0.70 |
| `batteryLow` / `batteryCritical` | medium / critical | `battery.percentage` < 25 % / < 12 % |
| `jointOverTemp` | high | `status.max_joint_temperature` > 70 °C |
| `gasDetected` | critical | `reading.h2s_ppm` > 10 or `reading.ch4_lel` > 10 |
| `noiseHazard` | low | `reading.la_eq` > 85 dB(A) |
| `commsDegraded` | medium | `status.packet_loss` > 5 % or `status.link_latency_ms` > 250 ms |
| `eStopEngaged` | critical | `status.e_stop` |

Rules resolve automatically (hysteresis) when the condition clears.

## Orion-LD 1.10 DDS bridge notes

- Entities named in the mapping are created at broker start with `"uninitialized"` values until the first sample.
- The bridge writes attribute history to TROE but no `entities` row, so Mintaka would answer 404 for DDS-fed entities;
  aura-monitor adds the missing rows at startup and every 5 min (`Historian.register_dds_entities`).
- Fast DDS logs a shared-memory port warning between containers and falls back to UDP; it is harmless.

## aura-monitor REST API (port 8090)

| method | path | returns |
| --- | --- | --- |
| GET | `/reports/{missionId}` | printable HTML mission report |
| GET | `/api/sessions` | `{reports: MissionReport[], running: HRISession}` |
| GET | `/api/analytics` | condition matrix, calibration bins, per-intent / modality / operator stats, hotspots (TROE SQL) |
| GET | `/api/alerts?status=active\|all` · POST `/api/alerts/{id}/ack\|resolve` | Alerts |
| POST | `/api/reports` | interim report for the running mission |
| GET | `/api/health` | component health, subscription and entity counts |
| GET | `/api/state` · `/api/events` · `/api/history` · `/api/entities` · `/api/stream` | diagnostics |
