"""Live A/B: one camera, two gesture nodes on the same frames, separate topics."""

from pathlib import Path

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_nodes(context):
    params = LaunchConfiguration("params_file")
    # A/B nodes have their own names, so lift the shared aura_gesture block explicitly
    with open(params.perform(context)) as f:
        shared = yaml.safe_load(f)["aura_gesture"]["ros__parameters"]
    nodes = []
    source = LaunchConfiguration("source").perform(context)
    if LaunchConfiguration("camera").perform(context).lower() == "true":
        nodes.append(Node(package="aura_gesture", executable="camera", name="aura_camera", output="screen",
                          parameters=[params, {"source": source} if source else {}]))
    for arm in ("a", "b"):
        model = LaunchConfiguration(f"model_{arm}").perform(context)
        nodes.append(Node(
            package="aura_gesture", executable="gesture", name=f"aura_gesture_{arm}", output="screen",
            parameters=[{
                **shared,
                "model_path": model, "model_id": Path(model).stem,
                "hypothesis_topic": f"/aura/gesture_{arm}/hypothesis",
                "intent_topic": f"/aura/gesture_{arm}/intent",
                "detections_topic": f"/aura/gesture_{arm}/detections",
            }]))
    return nodes


def generate_launch_description():
    config = str(Path(get_package_share_directory("aura_gesture")) / "config" / "gesture.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=config),
        DeclareLaunchArgument("camera", default_value="true"),
        DeclareLaunchArgument("source", default_value=""),
        DeclareLaunchArgument("model_a", default_value="model/gesture/YOLOv10n_gestures.onnx"),
        # not bundled (116 MB): export it to the project's model/gesture/ for A/B runs
        DeclareLaunchArgument("model_b", default_value="model/gesture/YOLOv10x_gestures.onnx"),
        OpaqueFunction(function=launch_nodes),
    ])
