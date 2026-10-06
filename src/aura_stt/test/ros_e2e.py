"""Validate the installed ROS2 executable using real-time recorded speech.

Run in a sourced ROS2 workspace using its ML Python environment. This subscribes
over DDS, checks latency configuration, and verifies a clean SIGINT shutdown.
"""

import argparse
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import time
import wave

import rclpy
from rcl_interfaces.srv import GetParameters, SetParameters
from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType
from std_msgs.msg import String


def word_error_rate(reference, hypothesis):
    normalize = lambda text: re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()
    reference, hypothesis = normalize(reference), normalize(hypothesis)
    previous = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, other in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (word != other)))
        previous = current
    return previous[-1] / max(1, len(reference))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--latency-ms", type=int, default=560)
    parser.add_argument("--report", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--params-file", help="Also verify installed launch and YAML loading")
    parser.add_argument("--expected-finals", type=int, default=1)
    parser.add_argument("--reference-text", help="Optional ground-truth transcript for sample WER")
    args = parser.parse_args()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = report_path.with_suffix(".log")
    with wave.open(args.wav) as source:
        duration = source.getnframes() / source.getframerate()
    rclpy.init()
    node = rclpy.create_node("aura_stt_validation")
    messages = {"partial": [], "final": []}
    start = time.monotonic()
    for kind, topic in (("partial", "/aura_partial_transcript"), ("final", "/aura_transcript")):
        node.create_subscription(String, topic, lambda msg, kind=kind: messages[kind].append({
            "seconds": round(time.monotonic() - start, 3), "text": msg.data}), 100)
    if args.params_file:
        command = ["ros2", "launch", "aura_stt", "stt.launch.py",
                   f"params_file:={args.params_file}", f"latency_ms:={args.latency_ms}"]
    else:
        command = ["ros2", "run", "aura_stt", "stt", "--ros-args",
                   "-p", f"model_dir:={args.model_dir}", "-p", f"audio_file:={args.wav}",
                   "-p", "device:=cpu", "-p", f"latency_ms:={args.latency_ms}"]
    proc = None
    ready_at = None
    try:
        with log_path.open("w") as log:
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            deadline = start + args.timeout
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
                if proc.poll() is not None:
                    raise RuntimeError(f"STT exited early ({proc.returncode}); see {log_path}")
                if ready_at is None and "Audio ready:" in log_path.read_text():
                    ready_at = time.monotonic()
                if ready_at and time.monotonic() >= ready_at + duration + 3 and len(messages["final"]) >= args.expected_finals:
                    break
            assert messages["partial"] and messages["final"], "No partial/final messages arrived over DDS"
            assert len(messages["final"]) == args.expected_finals, (
                f"Expected {args.expected_finals} final phrases, received {len(messages['final'])}")
            get_client = node.create_client(GetParameters, "/aura_stt/get_parameters")
            assert get_client.wait_for_service(timeout_sec=5), "Parameter service missing"
            future = get_client.call_async(GetParameters.Request(names=["latency_ms", "model_dir"]))
            rclpy.spin_until_future_complete(node, future, timeout_sec=5)
            values = future.result().values
            assert values[0].integer_value == args.latency_ms, "Configured latency was not applied"
            assert values[1].string_value == args.model_dir, "Configured fine-tuned model was not applied"
            set_client = node.create_client(SetParameters, "/aura_stt/set_parameters")
            assert set_client.wait_for_service(timeout_sec=5)
            future = set_client.call_async(SetParameters.Request(parameters=[Parameter(
                name="latency_ms", value=ParameterValue(type=ParameterType.PARAMETER_INTEGER, integer_value=80))]))
            rclpy.spin_until_future_complete(node, future, timeout_sec=5)
            assert not future.result().results[0].successful, "Startup latency unexpectedly mutable"
            os.killpg(proc.pid, signal.SIGINT)
            shutdown_code = proc.wait(timeout=15)
            assert shutdown_code == 0, f"Unclean shutdown ({shutdown_code})"
        logs = log_path.read_text()
        assert "Traceback" not in logs and "did not finish" not in logs, logs
        report = {"latency_ms": args.latency_ms, "model_dir": args.model_dir,
                  "audio_seconds": duration, "source": "real-time WAV replay",
                  "partial_count": len(messages["partial"]), "final_count": len(messages["final"]),
                  "messages": messages, "configuration_verified": True,
                  "shutdown_exit_code": shutdown_code, "launch": bool(args.params_file)}
        if ready_at:
            report["first_partial_after_ready_s"] = round(messages["partial"][0]["seconds"] - (ready_at - start), 3)
            report["last_final_after_ready_s"] = round(messages["final"][-1]["seconds"] - (ready_at - start), 3)
        if args.reference_text:
            report["reference_text"] = args.reference_text
            report["sample_wer"] = word_error_rate(
                args.reference_text, " ".join(message["text"] for message in messages["final"]))
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        summary = {key: value for key, value in report.items() if key != "messages"}
        summary["finals"] = [message["text"] for message in messages["final"]]
        summary["report"] = str(report_path)
        print(json.dumps(summary, indent=2))
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
