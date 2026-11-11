#!/bin/bash
# Inside the aura-ros2 image, with the repository mounted at /aura and the STT model at /model:
#   1. unit tests of the four packages (the copies built into the image)
#   2. the full-stack integration suite: all nodes started with ros2 run, a synthetic operator over
#      websockets (voice, camera), the robot's own voice fed back into the microphone
# Usage: test.sh [run name] [extra run_integration.py arguments]
set -u -o pipefail
out=${1:-docker}
shift || true
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-42} ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
unset ROS_STATIC_PEERS
status=0
for p in stt intent tts gesture; do
  echo "== unit tests aura_$p"
  (cd /ws/src/aura_$p && PYTHONPATH=. python -m pytest -q test -p no:cacheprovider \
      --ignore=test/ros_e2e.py --ignore=test/smoke_model.py 2>&1 | tail -4) || status=1
done
echo "== integration suite (ROS 2 $ROS_DISTRO)"
cd /aura
python tools/integration/run_integration.py --model-dir /model --out "runs/$out" "$@" || status=1
exit $status
