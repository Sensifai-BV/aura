# Connecting the robot to Orion-LD over DDS

Robot and AURA module data reaches the ARISE middleware **only as ROS 2 / DDS topics**. Orion-LD runs with its native
DDS bridge (`-wip dds`, ARISE D2.1 "Phase I / legacy mode"): it listens to the DDS domain, maps every allow-listed topic
onto one NGSI-LD attribute (the whole ROS message becomes the value) and serves it over HTTP. Everything else on the
server (Mintaka, Grafana, aura-monitor, WireCloud) talks HTTP to Orion-LD.

```
ROBOT (Artaban + AURA modules)                      SERVER (ARISE middleware)
  ROS 2 nodes --DDS, UDP, domain 0----------------> Orion-LD (-wip dds) --TROE--> TimescaleDB --> Mintaka / Grafana
                                                       |-- subscriptions --> aura-monitor, ngsi-proxy --> WireCloud
```

The topic mapping is `orion/config-dds.json` (`dds.ddsmodule.dds.allowlist` plus `dds.ngsild.topics`). Only listed
topics are bridged. Per ARISE D2.1 the legacy mode needs no change to the running DDS system; the mapping file supplies
topic to entity id, entity type and attribute name.

## What runs where

**Server** (see [PRODUCTION.md](PRODUCTION.md) for the Coolify deployment, `docker-compose.yml` for local use):

```bash
cd fiware
docker compose up -d --build        # Orion-LD, Mongo, TimescaleDB, Mintaka, Grafana, aura-monitor, WireCloud, ngsi-proxy
docker compose up wirecloud-init    # one-off: WireCloud widgets and dashboard
```

Orion-LD needs `fiware/orion-ld:1.10.0` (amd64), `-wip dds`, the mapping mounted at `/root/.orionld`, and **UDP reachable
from the robot**. Fast DDS uses UDP 7400 + 250 x domain for discovery and 7410 + 250 x domain (+ 2 per participant)
upwards for unicast; for domain 0 publish UDP 7400 and 7410-7419.

**Robot:**

- ROS 2 with **Fast DDS** (`rmw_fastrtps_cpp`), the same **domain** as the broker (0);
- this repo's `aura_interfaces` package built (the custom messages must exist on the robot);
- the AURA modules you run: `aura_stt`, `aura_intent`, `aura_tts`, `aura_gesture`.

```bash
source /opt/ros/<distro>/setup.bash
colcon build --packages-select aura_interfaces aura_stt aura_intent aura_tts aura_gesture
source install/setup.bash

export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_STATIC_PEERS=<address of the machine running Orion-LD>   # unicast discovery

ros2 launch aura_stt stt.launch.py
ros2 launch aura_intent intent.launch.py
ros2 launch aura_tts tts.launch.py
ros2 launch aura_gesture gesture.launch.py source:=/dev/video0
```

`ROS_STATIC_PEERS` is what makes discovery work across networks; a Fast DDS XML profile was ignored by the ROS
middleware in the tests. **Start the server first, let it settle, then start the robot nodes one at a time**: Orion-LD
creates its DDS readers when it first discovers a publisher, and under heavy CPU load (several models loading at once)
that step can fail silently and is not retried, leaving the entity `"uninitialized"`. The test runner works around this
by publishing an honest `"starting"` module status before the models load (`tools/integration/run_integration.py`).

## Topics

**Robot to Orion-LD** (bridged, QoS RELIABLE / VOLATILE, depth 1):

| ROS 2 topic | Type | Produced by | NGSI-LD entity · attribute |
| --- | --- | --- | --- |
| `/battery_state` | `sensor_msgs/BatteryState` | robot | `Robot:artaban-01` · `battery` |
| `/odom` | `nav_msgs/Odometry` | robot | `Robot:artaban-01` · `odometry` |
| `/imu/data` | `sensor_msgs/Imu` | robot | `Robot:artaban-01` · `imu` |
| `/artaban/status` | `aura_interfaces/ArtabanStatus` | robot | `Robot:artaban-01` · `status` |
| `/artaban/environment` | `aura_interfaces/EnvironmentReading` | inspection payload | `AirQualityObserved:artaban-01` · `reading` |
| `/aura_transcript`, `/aura_partial_transcript` | `std_msgs/String` | `aura_stt` | `HRIEvent:stt` · `transcript`, `partialTranscript` |
| `/aura/gesture/intent` | `aura_interfaces/GestureIntent` | `aura_gesture` | `HRIEvent:gesture` · `gestureIntent` |
| `/aura/intent/event` | `aura_interfaces/IntentEvent` | `aura_intent` | `HRIEvent:intent` · `intentEvent` |
| `/aura/speaker/say` | `std_msgs/String` | `aura_intent` | `HRIEvent:speaker` · `say` |
| `/aura/{stt,intent,gesture,speaker}/status` | `aura_interfaces/ModuleStatus` | each module | `HRIModuleStatus:<module>` · `status` |

**Orion-LD to robot:** the mapping is bidirectional by design (ARISE D2.1 "write mode": an NGSI-LD update is pushed onto
DDS) and Orion-LD creates writers on the mapped topics. The dashboards only read; sending commands back to the robot
over DDS has **not been tested** here.

**Stays on the robot** (not bridged): `/aura/camera/image_raw/compressed`, `/aura/gesture/hypothesis`,
`/aura/gesture/detections`, `/aura/speaker/speaking`. Images never leave the robot.

Module status is the flat `aura_interfaces/ModuleStatus` (`keys[]` / `values[]` string arrays): Orion-LD cannot read a
stock `diagnostic_msgs/DiagnosticStatus` (a list of structs) from a ROS 2 node. See [DATA_MODEL.md](DATA_MODEL.md).

## Checking it

```bash
# On the robot: is the broker discovered? Look for a "_CREATED_BY_BARE_DDS_APP_" subscriber (Orion-LD).
ros2 topic info /aura/intent/event -v --no-daemon

# On the server: did the data land?
curl -s 'http://SERVER:1026/ngsi-ld/v1/entities/urn:ngsi-ld:HRIModuleStatus:stt?options=keyValues'
curl -s 'http://SERVER:1026/ngsi-ld/v1/entities?type=HRIEvent&options=keyValues'
```

`"status":"uninitialized"` means Orion-LD created the entity but never received a sample: check the peer address, that
UDP is not blocked, and the start order above. Use `--no-daemon` for ROS 2 CLI checks: the daemon caches the network
settings it was started with.

## Limits

- **No authentication or encryption on DDS** in this setup. Restrict the DDS ports to the robot's address at the firewall,
  or run the link through a VPN.
- **Across NAT or the internet** DDS discovery needs the peers to be reachable on the addresses they advertise
  (a robot behind NAT advertises its private address). A VPN between robot and server, or a Fast DDS Discovery Server /
  DDS Router, is the usual remedy.
- On a single host (a laptop with a VM, or a robot and server on one LAN) `ROS_STATIC_PEERS` is all that is needed.
