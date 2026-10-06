# AURA — Adaptive User-intent Recognition & Augmented interaction for industrial robots

AURA is a research and development project that makes industrial inspection robots easier and safer for non-expert operators to supervise in hazardous or contaminated environments such as oil and gas facilities, waste treatment plants, and post-disaster sites. Instead of requiring specialised training or fragile single-channel control, AURA lets an operator direct a robot naturally — by speaking to it and by using a small set of clear hand gestures — while the robot communicates back what it has understood and what it is about to do. The system recognises not only the command itself but also whether the operator is genuinely addressing the robot and how confident it is in what it heard or saw, so control stays reliable even when noise, protective equipment, or difficult site conditions would otherwise break communication down.

## Microphone speech-to-text (ROS2)

`src/aura_stt` is an `ament_python` package. It captures microphone input
at a supported hardware rate, downmixes to mono, and continuously resamples to
16 kHz for the local fine-tuned NVIDIA Nemotron RNNT model on the edge device's
CPU. The module supports streaming inference and NeMo-exported encoder adapters.
The fine-tuned model files are in `model/`, and loading uses local files only.

| Topic | Type | Payload |
| --- | --- | --- |
| `/aura_partial_transcript` | `std_msgs/msg/String` | Cumulative hypothesis for the current phrase; replace the previous partial |
| `/aura_transcript` | `std_msgs/msg/String` | One completed phrase, after a pause or the duration limit |

Build in a sourced ROS2 environment (for example ROS2 Jazzy on Ubuntu 24.04).
Use the **same Python interpreter as `rclpy` and `colcon`** for the ML packages.
Run the following commands from the project root.

```bash
source /opt/ros/jazzy/setup.bash
# PortAudio is required by sounddevice; Ubuntu:
sudo apt-get install libportaudio2 python3-colcon-common-extensions python3-venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r src/aura_stt/requirements.txt
python -m pip install -r src/aura_stt/requirements-test.txt
python -c 'import rclpy, torch, transformers, sounddevice'
python -m colcon build --symlink-install --packages-select aura_stt
source install/setup.bash
```

Run with the fine-tuned model in the project's `model/` directory:

```bash
ros2 run aura_stt stt --ros-args \
  -p model_dir:=model \
  -p device:=cpu
```

Or load all settings from the installed configuration, with `device: cpu`
set in the YAML file:

```bash
ros2 launch aura_stt stt.launch.py
ros2 launch aura_stt stt.launch.py latency_ms:=160
# Supply a configuration with paths for the ROS2 host:
ros2 launch aura_stt stt.launch.py params_file:=/absolute/path/to/stt.yaml
```

In another sourced terminal, inspect completed speech:

```bash
ros2 topic echo /aura_transcript
ros2 topic echo /aura_partial_transcript
```

The default `model_dir` is `model`, resolved relative to the working directory.
Run from the project root or set `model_dir` to the deployed model's path.
`model_dir` also accepts `nemotron-welding-real` or
`nemotron-welding-librispeech` adapter directories; preserve their relative
`base_model` directory from `adapter_config.json`, or update that pointer.
Raw `.nemo`/Lightning checkpoints and ONNX exports need conversion to the
supported Transformers format before use here.
For `ros2 run`, `AURA_STT_MODEL_DIR` supplies the default model path; explicit
ROS parameters take precedence.

The parameters in `src/aura_stt/config/stt.yaml` control both topic names,
`device` (set to `cpu` for edge deployment), `input_device` (empty for automatic
microphone discovery, or a quoted device index/name), and `latency_ms`
(80, 160, 560, or 1120).
List microphone devices using
`python -m sounddevice` in the runtime environment. For example:

```bash
ros2 run aura_stt stt --ros-args \
  -p model_dir:=/path/to/model -p input_device:="'1'" \
  -p device:=cpu -p latency_ms:=560
```

`aura_stt/config.py` provides the immutable `SttConfig` configuration module,
including defaults, latency-to-lookahead mapping and startup validation.
Configuration precedence is: built-in/environment defaults, YAML parameters,
then explicit launch/ROS overrides. The launch `latency_ms` argument is optional;
when omitted, the YAML value applies. ROS parameters are read-only after startup
because changing latency or the microphone requires rebuilding the live session.
`num_threads` controls PyTorch CPU threads (default 4). `debug_audio:=true`
logs the capture duration and peak amplitude every five seconds. Persistent
digital silence produces a diagnostic warning even when debug logging is off.

Every input device reported by PortAudio is eligible for capture, including USB,
onboard, Bluetooth, audio interfaces and system/virtual inputs. Automatic
selection prefers hardware inputs and, when enabled, USB inputs; other inputs
remain available as fallbacks. An explicit `input_device` selects an index or
an unambiguous name match. One input is selected for STT, and its capture
channels are averaged to mono. DSP runs on a worker thread.

`input_sample_rate: 0` probes supported rates, preferring 48 kHz; a positive
value requires that rate. `capture_channels: 0` tries stereo, mono, then the
full native channel count; a positive value selects that channel count. `capture_block_ms`
sets the microphone callback period (default 100 ms), independently of the
model's `latency_ms`. `input_latency: 0.0` uses the device's high-latency setting;
a positive value requests that many seconds. `prefer_usb` and `include_internal`
control automatic discovery. A stateful resampler preserves continuity across
capture blocks and feeds the STT pipeline in 20 ms blocks at 16 kHz.

For repeatable dataset debugging, set `audio_file` to a 16 kHz mono PCM16 WAV.
It plays into the same audio queue in 20 ms blocks at real time. Empty
`audio_file` selects the microphone. Replay finishes the last phrase at EOF and
leaves the node running so subscribers can inspect the result.

```bash
ros2 run aura_stt stt --ros-args -p model_dir:=/path/to/model \
  -p audio_file:=/path/to/dataset.wav -p device:=cpu -p latency_ms:=560
```

Speech starts when a 20 ms audio block exceeds `speech_threshold_db` (default
−40 dBFS). `pre_roll_s` preserves preceding audio; `silence_s` ends a phrase
after 0.8 seconds of quiet. `max_utterance_s` bounds a phrase at 20 seconds.
This energy gate needs tuning for room noise and microphone gain; it does not
distinguish speech from loud machinery. Increase the threshold to reject noise,
or lower it to capture quieter speech. Continuous speech reaching the duration
limit is split at that boundary. Finals require endpoint silence and decoding
of the padded tail, so they arrive later than streaming partials.

Capture, resampling, endpointing, and inference run outside the ROS executor;
only the executor publishes messages. Audio and utterance backlogs are bounded.
The raw microphone queue keeps the newest two seconds if its worker falls
behind. Capture xruns and dropped blocks are
reported in diagnostics. Exhausted downstream STT queues or inference errors
stop the node with an error. Ctrl+C closes capture and unblocks readers;
an incomplete phrase at shutdown may be discarded.
Grant microphone access to the application running the node when required.

Validate audio buffering and endpoints without ROS2:

```bash
PYTHONPATH=src/aura_stt python -m unittest discover -s src/aura_stt/test -p 'test_*.py' -v
```

Validate actual streaming weights with a 16 kHz mono PCM16 speech recording:

```bash
PYTHONPATH=src/aura_stt python src/aura_stt/test/smoke_model.py \
  --model-dir /path/to/model --wav /path/to/speech.wav --device cpu
```

For an installed ROS2 test, `test/ros_e2e.py` launches the executable, receives
partials and finals through DDS, verifies the model/latency parameters, and
checks clean shutdown. Supply `--params-file` to exercise the installed launch
file and YAML. `--expected-finals` verifies phrase boundaries, and optional
`--reference-text` calculates word error rate for that sample.

```bash
python src/aura_stt/test/ros_e2e.py --model-dir /path/to/model \
  --wav /path/to/dataset.wav --latency-ms 560 --report validation/replay.json
```

ROS2 installations using Ubuntu's Python environment may expose an older
`coverage` package that conflicts with recent `numba` imported by the feature
extractor. `requirements-test.txt` installs a compatible version inside the
virtual environment. The setup commands above install PyTorch from its CPU
wheel index for deployment on the edge device.
