#!/bin/bash
# ROS 2 Jazzy, the AURA workspace and the ML venv in every command.
set -e
source /opt/ros/jazzy/setup.bash
source /ws/install/setup.bash
exec "$@"
