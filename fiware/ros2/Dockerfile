# ROS 2 (Vulcanexus) publisher for the Artaban / AURA topics, built from the repo root:
#   docker build -f fiware/ros2/Dockerfile .
# It stands in for the robot and the AURA modules until they publish over DDS themselves.
FROM eprosima/vulcanexus:jazzy-core-4.5.0

SHELL ["/bin/bash", "-c"]
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential python3-colcon-common-extensions \
      ros-jazzy-vision-msgs ros-jazzy-diagnostic-msgs ros-jazzy-nav-msgs ros-jazzy-sensor-msgs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /ws
COPY src/aura_interfaces src/aura_interfaces
RUN source /opt/vulcanexus/jazzy/setup.bash && colcon build --packages-select aura_interfaces

COPY fiware/simulator/artaban_sim /app/artaban_sim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
CMD ["bash", "-c", "source /opt/vulcanexus/jazzy/setup.bash && source /ws/install/setup.bash && python3 -m artaban_sim"]
