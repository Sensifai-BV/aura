from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_node(context):
    overrides = {}
    for name in ("model_path", "confirm_mode", "operator_id"):
        value = LaunchConfiguration(name).perform(context)
        if value:
            overrides[name] = value
    return [Node(package="aura_intent", executable="intent", name="aura_intent", output="screen",
                 parameters=[LaunchConfiguration("params_file"), overrides])]


def generate_launch_description():
    config = str(Path(get_package_share_directory("aura_intent")) / "config" / "intent.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=config),
        DeclareLaunchArgument("model_path", default_value=""),
        DeclareLaunchArgument("confirm_mode", default_value="",
                              description="always, uncertain or never (stop/pause/cancel never wait)"),
        DeclareLaunchArgument("operator_id", default_value=""),
        OpaqueFunction(function=launch_node),
    ])
