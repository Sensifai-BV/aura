# eProsima DDS Router (Vulcanexus Jazzy) as the WAN link between a robot and Orion-LD. Build from the repository root:
#   docker build -f fiware/deploy/ddsrouter.Dockerfile .
# One image, two roles (ROLE=server on the broker side, ROLE=client on the robot): both bridge a local DDS domain
# to a TCP link, so a robot behind NAT only needs an outbound connection to the server's public TCP port.
# The topic allow-list is generated from the Orion-LD mapping, so the two cannot drift apart. The server role also runs
# a small ROS 2 node that announces the message types (aura_interfaces is built here for that).
FROM eprosima/vulcanexus:jazzy-core-4.5.0
SHELL ["/bin/bash", "-c"]
RUN apt-get update \
 && apt-get install -y --no-install-recommends vulcanexus-jazzy-ddsrouter openssl build-essential \
      python3-colcon-common-extensions ros-jazzy-vision-msgs ros-jazzy-diagnostic-msgs ros-jazzy-nav-msgs ros-jazzy-sensor-msgs \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /ws
COPY src/aura_interfaces src/aura_interfaces
RUN source /opt/vulcanexus/jazzy/setup.bash && colcon build --packages-select aura_interfaces
COPY fiware/orion/config-dds.json /etc/aura/config-dds.json
COPY fiware/orion/config-dds-sim.json /etc/aura/config-dds-sim.json
COPY fiware/deploy/ddsrouter-entrypoint.py fiware/deploy/ddsrouter_topics.py fiware/deploy/ddsrouter-announcer.py /usr/local/bin/
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/usr/local/bin/ddsrouter-entrypoint.py"]
