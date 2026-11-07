# AURA × ARISE — Powered-by-FIWARE condition monitoring (local stack)

Local run of the ARISE all-in-one middleware for the **Artaban** quadruped with the AURA
acoustic-intent HRI skill: real-time dashboards, historian + analytics, and automated
alerts + mission reports, as required by the ARISE Open Call (proposal WP5/WP6).

Robot and module data enters FIWARE only as **ROS 2 / DDS** topics, bridged by Orion-LD's native DDS support
(ARISE D2.1, Phase I): every mapped topic becomes one NGSI-LD attribute. Until Artaban and the real AURA modules
publish on the DDS domain, the `artaban-ros2` container publishes the same topics and message types.

```
ROS 2 (DDS domain 0) ──▶ Orion-LD -wip dds ──TROE──▶ TimescaleDB ──▶ Mintaka ──▶ Grafana
 /battery_state /odom        │   ▲
 /artaban/status …           │   └──── HRISession · HRISessionKPI · Alert · MissionReport ── aura-monitor
 /aura/intent/event …        ▼
                 subscription ──▶ ngsi-proxy ──▶ WireCloud: AURA NGSI-LD source operator ──wiring──▶ AURA widgets
```

| Service | URL | Role |
| --- | --- | --- |
| **WireCloud** | http://localhost:8000/admin/aura (admin / admin) | FIWARE dashboard GE with the AURA workspace: AURA Telemetry widgets + NGSI-LD source operator |
| ngsi-proxy | http://localhost:3001/eventsources | FIWARE ngsi-proxy: relays Orion-LD notifications to the widgets |
| Orion-LD | http://localhost:1026/ngsi-ld/v1/entities?type=Robot&options=keyValues | NGSI-LD context broker with native DDS (FIWARE / ARISE) |
| Mintaka | http://localhost:8080/temporal/entities/urn:ngsi-ld:Robot:artaban-01?attrs=battery&lastN=5&timeproperty=modifiedAt | NGSI-LD temporal API |
| Grafana | http://localhost:3000 (admin / admin, anonymous viewer on) | ARISE-standard dashboards + Grafana alert rules |
| aura-monitor | http://localhost:8090/api/health | analytics plane (KPIs, alerts, mission reports written to Orion-LD) + printable reports |
| TimescaleDB | `localhost:5432` db `orion` (orion / orion) | TROE historian, views `aura_attributes`, `aura_intent_events` |

## Run

Requires Docker (Orion-LD, WireCloud and ngsi-proxy are amd64-only; on Apple silicon they run under emulation).

```bash
cd fiware
docker compose up -d --build
```

This is the local development stack (one broker, default logins). The **production** deployment on Coolify has two
brokers, two dashboards (Aura and Simulator), generated secrets and resource limits: see
[docs/PRODUCTION.md](docs/PRODUCTION.md). How a robot connects over DDS: [docs/DDS_INTEGRATION.md](docs/DDS_INTEGRATION.md).

A mission takes about 10 minutes, followed by charging at the dock. Each finished mission produces a
`MissionReport` entity and an HTML report at `http://localhost:8090/reports/<missionId>`.

### ROS 2 topics

The topic → attribute contract is in `docs/DATA_MODEL.md` and `orion/config-dds.json`; AURA message types are in
`src/aura_interfaces/msg/` (`IntentEvent`, `ArtabanStatus`, `EnvironmentReading`, `GestureIntent`). Inspect what the
broker holds:

```bash
curl -s 'localhost:1026/ngsi-ld/v1/entities/urn:ngsi-ld:HRIEvent:intent?options=keyValues'
```

### Real robot / modules

Publish the same topics on DDS domain 0 reachable by the Orion-LD container (for a robot on the LAN, run
Orion-LD with host networking or a Fast DDS discovery server), then stop the stand-in publisher:

```bash
docker compose stop artaban-ros2
```

Test conditions for the stand-in publisher (`noise_spike`, `mask_on`, `gas_leak`, `motor_overheat`,
`comms_degraded`, `low_battery`, `estop`, `none`):

```bash
docker compose exec artaban-ros2 bash -c 'source /opt/vulcanexus/jazzy/setup.bash && source /ws/install/setup.bash && ros2 topic pub --once /aura_sim/scenario std_msgs/msg/String "{data: gas_leak}"'
```

### Reset

```bash
docker compose down -v   # drops all data volumes (broker, historian, WireCloud); init jobs rebuild WireCloud
```

## WireCloud dashboard

The dashboard runs in **WireCloud** (FIWARE's dashboard GE). Every AURA widget is its own WireCloud widget
package (`.wgt`) built on the **AURA Telemetry** design system; the `wirecloud-init` job builds the packages,
installs them in the catalogue and creates the `admin/aura` workspace from the `Sensifai/aura-dashboard` mashup.

Data path inside WireCloud:

- **Live:** the *AURA NGSI-LD source* operator opens an EventSource on ngsi-proxy, creates a callback (via WireCloud's
  proxy, so the URL is reachable from Orion-LD) and an Orion-LD subscription for the AURA entity types (expires after
  2 h, renewed hourly, deleted on unload). Each notification goes out on its `entities` output, wired to every widget.
- **State / history:** each widget queries Orion-LD (`/ngsi-ld/v1`) and Mintaka (`/temporal`) through WireCloud's proxy
  (`MashupPlatform.http`), and falls back to polling when it is not wired.
- **Actions:** widgets write NGSI-LD: Acknowledge / Resolve PATCH the `Alert` status, and "Generate report" sets
  `HRISessionKPI.reportRequestedAt` (aura-monitor is subscribed).

| Widget (`Sensifai/aura-<name>`) | Design component | NGSI-LD source | Tab |
| --- | --- | --- | --- |
| `kpi-tiles` | StatTile ×6 | `HRISessionKPI` (+ Mintaka sparklines) | HRI |
| `pipeline-health` | PipelineMap | `HRIModuleStatus:*.status` (`/aura/*/status`) | HRI |
| `confidence-trend` | TimeSeriesChart | `HRIEvent:intent.intentEvent`, `HRIEvent:gesture.gestureIntent` | HRI |
| `live-log` | LogTable | `HRIEvent` transcript · gestureIntent · intentEvent · say (Mintaka backfill + live) | HRI |
| `intent-breakdown` · `module-rates` · `rejection-reasons` | BarChart | `HRISessionKPI` compounds | HRI |
| `robot-vitals` | StatTile ×6 | `Robot` battery · odometry · status, `AirQualityObserved.reading` | Artaban telemetry |
| `robot-state` | Panel + badges | `Robot.status`, `Robot.odometry` | Artaban telemetry |
| `battery-trend` | TimeSeriesChart | `Robot.battery` (`/battery_state`) | Artaban telemetry |
| `joint-temperatures` | BarChart | `Robot.status` joint names × temperatures | Artaban telemetry |
| `environment` | bars | `AirQualityObserved.reading` (`/artaban/environment`) | Artaban telemetry |
| `alerts-panel` | Panel + badges | `Alert` | Alerts & reports |
| `mission-reports` | table | `MissionReport` | Alerts & reports |

Every widget has the preferences *Orion-LD URL*, *Mintaka URL* and *Theme* (Control room / Daylight).

**Developing a widget:** edit `wirecloud/widgets/<name>/js/main.js` (shared code: `wirecloud/shared/aura-wc.js`,
design system: `wirecloud/shared/ds/`), bump `VERSION` in `wirecloud/build.py`, then run
`docker compose run --rm wirecloud-init`. WireCloud's `--redeploy` does not persist with its SQLite backend, so a new
version is the way to ship changes; upgrade the widgets in the workspace from WireCloud's UI. To add a widget, add it
to `WIDGETS` and `TABS` in `build.py`.

## Layout

| Path | Content |
| --- | --- |
| `docs/DATA_MODEL.md` | NGSI-LD entities, KPI definitions, alert rules, monitor API (the contract) |
| `simulator/artaban_sim/` | stand-in ROS 2 publisher for the Artaban / AURA topics (`ros_node.py`) |
| `ros2/Dockerfile` | Vulcanexus image with `aura_interfaces` built, runs the publisher (`artaban-ros2`) |
| `monitor/` | `aura-monitor` FastAPI service: KPIs (`HRISession`, `HRISessionKPI`), `Alert`s, `MissionReport`s, report pages |
| `wirecloud/` | WireCloud dashboard: `widgets/` (one folder per widget), `operators/ngsild-source`, `shared/` (AURA Telemetry design system, React 18, `aura-wc.js`), `build.py` (→ `.wgt`), `init.sh`, `nginx/` |
| `grafana/` | provisioning (datasources, alert rules, contact point) and dashboards; regenerate with `python3 grafana/build_dashboards.py` |
| `orion/config-dds.json` | ROS 2 topic → NGSI-LD attribute mapping (allowlist) |
| `orion/config-dds-sim.json` | same mapping on DDS domain 1 (the Simulator broker in production) |
| `docker-compose.prod.yml` | production stack for Coolify: two brokers, two dashboards, secrets, resource limits, [docs/PRODUCTION.md](docs/PRODUCTION.md) |
| `wirecloud/init.prod.sh`, `wirecloud/nginx/nginx.prod.conf` | production WireCloud provisioning (own admin account, Aura + Simulator dashboards) and routing |
| `docs/DDS_INTEGRATION.md` | how a robot connects to Orion-LD over DDS: topics, ports, how to run, how to check |

Research notes and the ARISE PoC reference used to build this live in `artifacts/arise-research/`; that directory is git-ignored.
