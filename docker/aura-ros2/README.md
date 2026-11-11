# AURA modules on ROS 2 Jazzy (Docker)

`aura_stt`, `aura_intent`, `aura_tts` and `aura_gesture` built with colcon on `ros:jazzy-ros-base`: the distribution
the Artaban computer runs (Jazzy, Fast DDS). CPU build; the ML packages live in `/opt/aura_venv`
(`--system-site-packages`, so rclpy, OpenCV and numpy come from apt; numpy stays below 2 for apt's OpenCV).

```bash
docker build -f docker/aura-ros2/Dockerfile -t aura-ros2:jazzy .      # about 10 min (llama.cpp is compiled)

# unit tests + full-stack integration suite (all nodes, synthetic operator, the robot's voice fed back to the mic)
docker run --rm -v "$PWD:/aura" -v "$PWD/model:/model:ro" aura-ros2:jazzy \
    bash /aura/docker/aura-ros2/test.sh docker1 --only 1,2,3,4,5,6,7,8,9,10,11,12,15,17
```

`test.sh <name> [run_integration.py arguments]` writes `runs/<name>/report.json` and one log per node; it uses DDS
domain 42 on localhost only, so nothing leaves the container. The STT model (`/model`, not in git) is mounted; the
intent, speech-output and gesture models are installed into the package shares at build time.

Run a single node, e.g. with the robot's microphone and speaker passed through:

```bash
docker run --rm -it --network host --device /dev/snd -v "$PWD/model:/model:ro" aura-ros2:jazzy \
    ros2 launch aura_stt stt.launch.py input_device:=ReSpeaker
```

GPU builds (CUDA torch, `onnxruntime-gpu` for CUDA 12, llama.cpp with `-DGGML_CUDA=on`) are not part of this image.
