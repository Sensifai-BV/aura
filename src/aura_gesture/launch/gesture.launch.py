from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_nodes(context):
    params = LaunchConfiguration("params_file")
    nodes = []
    if LaunchConfiguration("camera").perform(context).lower() == "true":
        overrides = {}
        source = LaunchConfiguration("source").perform(context)
        if source:
            overrides["source"] = source
        nodes.append(Node(package="aura_gesture", executable="camera", name="aura_camera",
                          output="screen", parameters=[params, overrides]))
    overrides = {}
    for name in ("model_path", "model_id", "providers"):
        value = LaunchConfiguration(name).perform(context)
        if value:
            overrides[name] = value
    if LaunchConfiguration("debug").perform(context).lower() == "true":
        overrides["publish_debug_image"] = True
    max_age = LaunchConfiguration("max_frame_age_s").perform(context)
    if max_age:
        overrides["max_frame_age_s"] = float(max_age)
    nodes.append(Node(package="aura_gesture", executable="gesture", name="aura_gesture",
                      output="screen", parameters=[params, overrides]))
    return nodes


def generate_launch_description():
    config = str(Path(get_package_share_directory("aura_gesture")) / "config" / "gesture.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=config),
        DeclareLaunchArgument("camera", default_value="true", description="Also start the camera node"),
        DeclareLaunchArgument("source", default_value="", description="Override camera source"),
        DeclareLaunchArgument("model_path", default_value=""),
        DeclareLaunchArgument("model_id", default_value=""),
        DeclareLaunchArgument("providers", default_value="",
                              description="Override YAML ONNX Runtime providers, e.g. CUDAExecutionProvider,CPUExecutionProvider"),
        DeclareLaunchArgument("debug", default_value="false",
                              description="Publish the annotated debug image stream"),
        DeclareLaunchArgument("max_frame_age_s", default_value="",
                              description="0 disables the stale-frame guard (bag replay)"),
        OpaqueFunction(function=launch_nodes),
    ])
