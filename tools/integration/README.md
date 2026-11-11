# AURA full-stack integration tests (ROS 2 VM)

Runs the real `aura_stt`, `aura_intent`, `aura_tts`, `aura_camera` and `aura_gesture` nodes and drives them through
websockets, so no physical camera, microphone or speaker is needed inside the OrbStack VM (it has a VirtIO sound card
but no PulseAudio server and no camera device).

```
operator mic    --ws /mic-------> ws_bridge --FIFO (real-time WAV)--> aura_stt --> aura_intent --> aura_tts
operator camera --ws /camera----> ws_bridge --MJPEG :18090---------> aura_camera --> aura_gesture --^
robot speaker  <--ws /speaker_out-- ws_bridge <--ws /speaker--------- aura_tts (output_device: ws://...)
```

| File | Role |
| --- | --- |
| `ws_bridge.py` | runs in the VM; presents the websocket streams as the device interfaces the nodes already support. 100 Hz high-pass on the mic stream (`--highpass`), optional recording of what STT receives (`--record`), dropout counter on `/stats` |
| `run_integration.py` | starts the nodes and the bridge, plays the operator and the robot platform, scores 16 scenarios; `--live` waits for real host devices instead |
| `mac_clients.py` | host side: streams the webcam (`--fps`), microphone and plays the robot's voice (mutes the mic while it speaks) |
| `guided_commands.py`, `score_guided.py` | 22 fine-tuned commands for a spoken session, and the scorer |
| `record_guided.py`, `score_offline.py` | record each phrase straight from the mic and decode it offline (separates the microphone from the live pipeline) |
| `level_meter.py` | host microphone meter: speech should reach about -25 to -15 dBFS with most energy in 150 Hz to 4 kHz |
| `edge_telemetry.py` | publishes real edge-computer metrics (CPU, memory, link) on `/artaban/status` when no robot platform does |

## Scenarios (synthetic operator)

```bash
orbctl run -m ros2 bash -lc 'source /opt/ros/lyrical/setup.bash; source ~/aura_venv/bin/activate; \
  source ~/aura_ws/install/setup.bash; cd <repo>; python tools/integration/run_integration.py --out runs/it1'
```

`--only 2,3` runs selected scenarios; `--gesture-images DIR` enables the gesture scenarios (`DIR/<gesture>/*.jpg`);
`--confirm-mode never` makes every command execute directly (one decision per phrase); `--mission-id M-...` pins the
mission id of `aura_intent`. Scenario 16 is the 22 fine-tuned commands (wake word "Artaban" on half of them):
`run_integration.py --only 16 --confirm-mode never --mission-id M-AURA-LIVE-01` (last run: 19 of 22 correct, median
1.5 s from end of speech to decision; the misses were STT: "halt", "Artaban, wait", "Artaban, open the valve").

The robot's voice is fed back into the operator microphone (`--echo-gain`, default 0.8; 0 = no echo), as on
Artaban where the speaker is next to the microphone. Scenario 17 checks what failed in the field on 10 Nov: the
robot's own "Stop, executing." must not stop it again, and a "yes" said 0.6 s after the question must confirm it.
`--no-echo-gate` turns off STT's echo gate for a baseline.

Operator speech is synthesised with Supertonic (voice 3; the robot speaks voice 0) and can be mixed with plant noise
at a set SNR. Results: `<out>/report.json` (checks, notes, event timeline) and one log per node.

## Live session with your own devices

```bash
# VM: start the real modules and wait for the host devices (the --no-platform flag: a robot platform provides /artaban/*)
python tools/integration/run_integration.py --out runs/live --live --no-platform --confirm-timeout 25 \
    --speech-threshold-db -34 --mission-id M-AURA-LIVE-01
# Mac (grant Camera and Microphone access to the terminal app); 6 fps keeps gesture at about 120 % CPU
python tools/integration/mac_clients.py --url ws://ros2.orb.local:8765 --fps 6 --preview
```

To send the data on to a FIWARE stack, export `ROS_DOMAIN_ID=0` and `ROS_STATIC_PEERS=<Orion-LD address>` before the
runner (see `fiware/docs/DDS_INTEGRATION.md`); the runner then primes Orion-LD's status readers first.

## Lessons from running it

- STT's energy gate is `-40 dBFS` by default. A laptop microphone with room rumble sat at about -42 dBFS (peaks -37),
  so noise kept opening utterances (each costs about 2 s of decoding) until STT stopped with "Utterance queue full".
  Use `--speech-threshold-db` around -34 and the bridge's high-pass.
- The VM shares CPU with other containers: a load average of 25 to 38 starves STT. Keep the camera at 6 fps.
- If the built-in microphone captures only rumble (voice band under 30 % in `level_meter.py`), no voice test can work;
  check the Sound settings, lid and microphone permission, or use the synthetic operator.
