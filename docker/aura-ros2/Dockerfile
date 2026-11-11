# AURA modules (aura_stt, aura_intent, aura_tts, aura_gesture) on ROS 2 Jazzy, CPU build.
# The same distribution as the robot's computer (Jazzy, Fast DDS); used to build and test the
# packages there before they are handed over. Build from the repository root:
#
#   docker build -f docker/aura-ros2/Dockerfile -t aura-ros2:jazzy .
#
# The STT model (/model, 2.8 GB, not in git) is mounted at run time; the intent, speech output and
# gesture models are in src/ and are installed into the package shares by colcon.
# See docker/aura-ros2/README.md for the test commands.
FROM ros:jazzy-ros-base

SHELL ["/bin/bash", "-c"]
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3-pip python3-venv python3-colcon-common-extensions python3-numpy python3-opencv python3-pytest \
      ros-jazzy-vision-msgs ros-jazzy-diagnostic-msgs ros-jazzy-sensor-msgs \
      libportaudio2 libsndfile1 build-essential cmake git \
 && rm -rf /var/lib/apt/lists/*

# ML packages in a venv that also sees the ROS Python packages (rclpy, cv2, numpy from apt).
# numpy stays below 2: apt's OpenCV is built against numpy 1.26.
RUN python3 -m venv --system-site-packages /opt/aura_venv
ENV PATH=/opt/aura_venv/bin:$PATH VIRTUAL_ENV=/opt/aura_venv
RUN echo "numpy<2" > /opt/aura_venv/constraints.txt \
 && pip install --no-cache-dir -c /opt/aura_venv/constraints.txt \
      torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -c /opt/aura_venv/constraints.txt \
      "transformers>=5.13,<6" "safetensors>=0.4" "sounddevice>=0.5" "soxr>=0.5" "librosa>=0.11" \
      "sherpa-onnx>=1.13.8" "onnxruntime>=1.20" websockets pytest "coverage>=7.6"
# llama.cpp is compiled for a generic CPU of the target architecture, not the build host.
RUN CMAKE_ARGS="-DGGML_NATIVE=OFF" pip install --no-cache-dir -c /opt/aura_venv/constraints.txt \
      "llama-cpp-python>=0.3.16"

WORKDIR /ws
COPY src src
# colcon from the venv Python, so the node entry points run with the ML packages.
RUN source /opt/ros/jazzy/setup.bash \
 && python -m colcon build --packages-select aura_interfaces aura_stt aura_intent aura_tts aura_gesture

COPY docker/aura-ros2/entrypoint.sh /entrypoint.sh
ENV PYTHONUNBUFFERED=1 AURA_STT_MODEL_DIR=/model
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
