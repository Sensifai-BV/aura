#!/bin/bash
# Send mock Artaban + AURA data to the Simulator dashboard (DDS domain 1) of the ARISE middleware.
#
#   tools/sim_link/run.sh [seconds]              # mock_publish.py for 600 s (default)
#   tools/sim_link/run.sh link                   # only the link: your own ROS 2 publishers join as peers
#
# A DDS Router client (TLS, outbound TCP only, works behind NAT) connects to ddsrouter-sim on the server; publishers
# on the Docker network "aura-sim-link" reach it with ROS_STATIC_PEERS=aura-sim-link. Nothing reaches the real robot
# plane (domain 0). Environment: SERVER (95.217.135.234), PORT (11801), TLS_CA_B64 (default: the CA next to this file),
# NETWORK (Docker network of the client and publishers; another one to reach a local test server by name), CHECK=0
# skips the TCP check from this host, DOMAIN (1; must equal the server router's DDS domain).
set -euo pipefail
cd "$(dirname "$0")/../.."
SERVER=${SERVER:-95.217.135.234}
PORT=${PORT:-11801}
CA=${TLS_CA_B64:-$(base64 < tools/sim_link/aura-ddsrouter-ca.crt | tr -d '\n')}
IMAGE=${IMAGE:-aura-ddsrouter}
MODE=${1:-600}
NETWORK=${NETWORK:-aura-sim-link}
DOMAIN=${DOMAIN:-1}   # both ends of the link must use the same domain: 1 = Simulator plane on the server
MAPPING=/etc/aura/config-dds-sim.json; [ "$DOMAIN" = 1 ] || MAPPING=/etc/aura/config-dds.json

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -f fiware/deploy/ddsrouter.Dockerfile -t "$IMAGE" .
[ "${CHECK:-1}" = 0 ] || nc -z -w 6 "$SERVER" "$PORT" || { echo "cannot reach $SERVER:$PORT (TCP)"; exit 1; }
docker network create "$NETWORK" >/dev/null 2>&1 || true
docker rm -f aura-sim-link >/dev/null 2>&1 || true
docker run -d --name aura-sim-link --network "$NETWORK" \
  -e ROLE=client -e DDS_DOMAIN="$DOMAIN" -e DDS_CONFIG="$MAPPING" \
  -e SERVER_ADDRESS="$SERVER" -e WAN_PORT="$PORT" -e TLS_CA_B64="$CA" "$IMAGE" >/dev/null
sleep 5
docker logs aura-sim-link 2>&1 | grep -E "ddsrouter role|running|rror" | head -5

if [ "$MODE" = "link" ]; then
  echo "link up: run publishers with --network $NETWORK -e ROS_DOMAIN_ID=$DOMAIN -e ROS_STATIC_PEERS=aura-sim-link"
  echo "stop it with: docker rm -f aura-sim-link"
  exit 0
fi
trap 'docker rm -f aura-sim-link aura-sim-mock >/dev/null 2>&1 || true' EXIT
docker run --rm --name aura-sim-mock --network "$NETWORK" -e ROS_DOMAIN_ID="$DOMAIN" -e ROS_STATIC_PEERS=aura-sim-link \
  -v "$PWD/tools/sim_link:/sim:ro" --entrypoint bash "$IMAGE" -c \
  "source /opt/vulcanexus/jazzy/setup.bash && source /ws/install/setup.bash && python3 /sim/mock_publish.py --seconds $MODE"
