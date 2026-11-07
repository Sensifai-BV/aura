# eProsima DDS Router (Vulcanexus Jazzy) as the WAN link between a robot and Orion-LD.
# One image, two roles (ROLE=server on the broker side, ROLE=client on the robot): both bridge a local DDS domain
# to a TCP link, so a robot behind NAT only needs an outbound connection to the server's public TCP port.
# The topic allow-list is generated from the Orion-LD mapping, so the two cannot drift apart.
FROM eprosima/vulcanexus:jazzy-core-4.5.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends vulcanexus-jazzy-ddsrouter openssl \
 && rm -rf /var/lib/apt/lists/*
COPY orion/config-dds.json /etc/aura/config-dds.json
COPY orion/config-dds-sim.json /etc/aura/config-dds-sim.json
COPY deploy/ddsrouter-entrypoint.py /usr/local/bin/ddsrouter-entrypoint.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/usr/local/bin/ddsrouter-entrypoint.py"]
