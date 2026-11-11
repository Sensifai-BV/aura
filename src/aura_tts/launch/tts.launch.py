from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_node(context):
    overrides = {}
    for name in ("model_dir", "output_device"):
        value = LaunchConfiguration(name).perform(context)
        if value:
            overrides[name] = value
    if LaunchConfiguration("debug").perform(context).lower() == "true":
        overrides["debug"] = True
    metrics_dir = LaunchConfiguration("metrics_dir").perform(context)
    if metrics_dir:
        overrides["metrics_dir"] = metrics_dir
    voice = LaunchConfiguration("voice").perform(context)
    if voice:
        overrides["voice"] = int(voice)
    return [Node(package="aura_tts", executable="tts", name="aura_tts", output="screen",
                 parameters=[LaunchConfiguration("params_file"), overrides])]


def generate_launch_description():
    config = str(Path(get_package_share_directory("aura_tts")) / "config" / "tts.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=config),
        DeclareLaunchArgument("model_dir", default_value=""),
        DeclareLaunchArgument("output_device", default_value="",
                              description='Output device index or name; "none" for headless'),
        DeclareLaunchArgument("voice", default_value=""),
        DeclareLaunchArgument("debug", default_value="false", description="Write metrics CSV (one row per prompt)"),
        DeclareLaunchArgument("metrics_dir", default_value="", description="Override YAML metrics_dir"),
        OpaqueFunction(function=launch_node),
    ])
