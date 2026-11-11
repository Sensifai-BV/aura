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
    if LaunchConfiguration("debug").perform(context).lower() == "true":
        parameters.append({"debug": True})
    for name in ("input_device", "device", "metrics_dir", "echo_mode"):
        value = LaunchConfiguration(name).perform(context)
        if value:
            parameters.append({name: value})
    tail = LaunchConfiguration("echo_tail_s").perform(context)
    if tail:
        parameters.append({"echo_tail_s": float(tail)})
    if LaunchConfiguration("record_audio").perform(context).lower() == "true":
        parameters.append({"record_audio": True})
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
        DeclareLaunchArgument("debug", default_value="false",
                              description="Write metrics CSV (one row per utterance)"),
        DeclareLaunchArgument("metrics_dir", default_value="", description="Override YAML metrics_dir"),
        DeclareLaunchArgument("device", default_value="",
                              description="Override YAML ASR device: auto, cpu, mps, cuda or cuda:<index>"),
        DeclareLaunchArgument("echo_mode", default_value="",
                              description="Override YAML echo_mode: filter (hear the operator while the robot talks) or mute"),
        DeclareLaunchArgument("echo_tail_s", default_value="",
                              description="Override YAML echo_tail_s (seconds after robot speech still treated as echo)"),
        DeclareLaunchArgument("record_audio", default_value="false",
                              description="Save every utterance as a WAV in <metrics_dir>/audio"),
        OpaqueFunction(function=launch_node),
    ])
