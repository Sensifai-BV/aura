# Connecting the robot to Orion-LD over DDS

Robot and AURA module data reaches the ARISE middleware **only as ROS 2 / DDS topics**. Orion-LD runs with its native
DDS bridge (`-wip dds`, ARISE D2.1 "Phase I / legacy mode"): it listens to the DDS domain, maps every allow-listed topic
onto one NGSI-LD attribute (the whole ROS message becomes the value) and serves it over HTTP. Everything else on the
server (Mintaka, Grafana, aura-monitor, WireCloud) talks HTTP to Orion-LD.

```
ROBOT (Artaban + AURA modules)                      SERVER (ARISE middleware)
  ROS 2 nodes --DDS, domain 0--> DDS Router ==TLS/TCP==> DDS Router --DDS--> Orion-LD (-wip dds) --TROE--> TimescaleDB
              (client, on the robot)  :11800   (server, ddsrouter)             |-- subscriptions --> aura-monitor, ngsi-proxy
                                                                               --> WireCloud
```

On one LAN or one machine the robot can also talk DDS to Orion-LD directly (section "What runs where"); over the internet
use the DDS Router link described in "Robot link over the internet".

The topic mapping is `orion/config-dds.json` (`dds.ddsmodule.dds.allowlist` plus `dds.ngsild.topics`). Only listed
topics are bridged. Per ARISE D2.1 the legacy mode needs no change to the running DDS system; the mapping file supplies
topic to entity id, entity type and attribute name.

## What runs where

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

## Robot link over the internet (DDS Router over TCP)

Plain DDS discovery does not survive NAT (a robot advertises its private address), so the production server accepts a
TLS-encrypted TCP connection instead. Both ends run `eProsima DDS Router`, built by `fiware/deploy/ddsrouter.Dockerfile`:

**Client** (on the robot or any machine on the robot's LAN): connects out to the server, bridges the local domain.
  The allow-list is generated from `orion/config-dds.json`: only the 14 mapped topics cross the link.

Run the client with Docker on the robot's computer (host network, so it sees the ROS 2 nodes; build the image from the repo
root once with `docker build -f fiware/deploy/ddsrouter.Dockerfile -t aura-ddsrouter .`):

```bash
docker run -d --restart unless-stopped --name aura-ddsrouter --network host \
  -e ROLE=client -e DDS_DOMAIN=0 \
  -e SERVER_ADDRESS=aura-dds.sensifai.com \
  -e TLS_CA_B64='<the TLS_CA_B64=... line printed in the server log, value only>' \
  aura-ddsrouter
```

The robot's ROS 2 nodes then run as usual (`ROS_DOMAIN_ID=0`, `rmw_fastrtps_cpp`), with **no** `ROS_STATIC_PEERS` for the
server: the client router discovers them on the robot's network (multicast). If the ROS 2 nodes run on the same computer,
host networking is enough; if multicast does not work there, set `ROS_STATIC_PEERS=127.0.0.1` for the nodes.

Environment of the image (`deploy/ddsrouter-entrypoint.py` documents all of them): `ROLE` (`server`|`client`),
`DDS_DOMAIN`, `WAN_PORT` (11800), `SERVER_ADDRESS` (client), `TLS_CA_B64` (client; without it the client does not
verify the server), `LOCAL_PEERS` (unicast peers for the local side, used by the server for Orion-LD).

What was tested (locally, Docker; client and server on separate networks, Orion-LD 1.10.0): battery, odometry-style and
AURA `ModuleStatus` messages published on the client's network reached Orion-LD as entity attributes within about 30 s;
the link survives a client restart; with TLS 1.3 and the generated CA. **Not yet tested:** a real robot behind NAT across
the internet, and the production deployment itself.

**The server must announce its public address** (`PUBLIC_ADDRESS`, production default `95.217.135.234`). Behind Docker's
port mapping a server listening on `0.0.0.0` announces its private container address; the robot then holds the TCP
connection, keeps retrying the private address, and no data reaches Orion-LD. This was seen with the real robot and
reproduced locally (client reaching the server only through a port mapping); with `PUBLIC_ADDRESS` set, data arrived
within 30 s.

**Rate limit:** the router forwards at most 2 samples per second on `rt/odom`, `rt/imu/data` and `rt/battery_state`
(`RATE_LIMIT_HZ`, `RATE_LIMIT_TOPICS`). With the real robot's full-rate odometry and IMU, Orion-LD wrote every sample to
Mongo and TimescaleDB, the database ran out of connections and Orion-LD crash-looped. Event topics are never limited.

Limits: the TLS proves the server and encrypts the data, but a router client is not authenticated, so restrict TCP 11800
to the robot's address at the firewall. The client resolves `SERVER_ADDRESS` once at start; restart it if the address
changes. Start the server first and let it settle; if Orion-LD was restarted while the router was up, restart `ddsrouter`
too so Orion-LD sees the announced types again.

## Simulator plane (test publishers)

`ddsrouter-sim` is a second DDS Router server on TCP 11801 that bridges to `orion-sim` (DDS domain 1, Simulator
dashboard). It reuses the robot link's certificate, so the client needs the same `TLS_CA_B64`. Connect a client with
`DDS_DOMAIN=1`, `DDS_CONFIG=/etc/aura/config-dds-sim.json`, `WAN_PORT=11801`; `tools/sim_link/run.sh` does this and
publishes mock data (`tools/sim_link/mock_publish.py`, mission `M-SIM-<UTC time>`), including the robot adapter's
feedback on intent events (time to rest, false activation). Use it for integration tests instead of the robot plane,
so a test never mixes with a live robot session.

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

- **Direct DDS has no authentication or encryption.** Use the DDS Router link (TLS) over the internet; keep direct DDS to
  one LAN or a VPN.
- **Across NAT or the internet** plain DDS discovery needs the peers to be reachable on the addresses they advertise
  (a robot behind NAT advertises its private address): that is why the router link exists.
- On a single host (a laptop with a VM, or a robot and server on one LAN) `ROS_STATIC_PEERS` is all that is needed.
