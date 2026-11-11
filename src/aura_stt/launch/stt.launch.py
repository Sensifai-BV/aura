from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_node(context):
    parameters = [LaunchConfiguration("params_file")]
    latency = LaunchConfiguration("latency_ms").perform(context)
    if latency:
        parameters.append({"latency_ms": int(latency)})
    for name in ("input_device", "device"):
        value = LaunchConfiguration(name).perform(context)
        if value:
            parameters.append({name: value})
    return [Node(package="aura_stt", executable="stt", name="aura_stt",
                 output="screen", parameters=parameters)]


def generate_launch_description():
    config = str(Path(get_package_share_directory("aura_stt")) / "config" / "stt.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=config),
        DeclareLaunchArgument("latency_ms", default_value="",
                              description="Override YAML latency: 80, 160, 560 or 1120 ms"),
        DeclareLaunchArgument("input_device", default_value="",
                              description="Override YAML microphone: index or name"),
        DeclareLaunchArgument("device", default_value="",
                              description="Override YAML ASR device: auto, cpu, mps, cuda or cuda:<index>"),
        OpaqueFunction(function=launch_node),
    ])
