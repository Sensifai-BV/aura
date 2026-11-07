"""Edge-computer telemetry on /artaban/status, measured on the machine that runs the AURA modules.

Only values that are actually measured are filled in; everything else keeps the message default
(empty or zero) and is not shown as a reading:

  cpu_load          % of CPU time busy over the last period            (/proc/stat)
  memory_usage      % of memory in use                                  (/proc/meminfo)
  soc_temperature   hottest thermal zone, degC                          (/sys/class/thermal; 0 if none)
  link_latency_ms   TCP connect time to the broker (--probe host:port)
  packet_loss       % of failed probe connections over the last minute
  mission_id        operating session id, set at start (M<date>-<time>)

    python edge_telemetry.py --probe 192.168.97.8:1026
"""

import argparse
import collections
import glob
import socket
import time
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node

from aura_interfaces.msg import ArtabanStatus


def cpu_times():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    idle = v[3] + v[4]
    return idle, sum(v)


def memory_percent():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.split()[0])
    return 100.0 * (1 - info["MemAvailable"] / info["MemTotal"])


def thermal_c():
    temps = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            temps.append(int(open(path).read()) / 1000.0)
        except (OSError, ValueError):
            pass
    return max(temps) if temps else 0.0


class EdgeTelemetry(Node):
    def __init__(self, probe, period, mission):
        super().__init__("aura_edge_telemetry")
        self.pub = self.create_publisher(ArtabanStatus, "/artaban/status", 10)
        self.probe = (probe.rsplit(":", 1)[0], int(probe.rsplit(":", 1)[1])) if probe else None
        self.mission = mission or datetime.now(timezone.utc).strftime("M%Y%m%d-%H%M")
        self.results = collections.deque(maxlen=60)  # (ok, ms)
        self.last_cpu = cpu_times()
        self.create_timer(period, self.tick)
        self.get_logger().info(f"edge telemetry on /artaban/status, session {self.mission}, probe {probe}")

    def measure_link(self):
        if not self.probe:
            return
        t0 = time.monotonic()
        try:
            with socket.create_connection(self.probe, timeout=1.0):
                self.results.append((True, (time.monotonic() - t0) * 1000))
        except OSError:
            self.results.append((False, 0.0))

    def tick(self):
        self.measure_link()
        idle, total = cpu_times()
        d_idle, d_total = idle - self.last_cpu[0], total - self.last_cpu[1]
        self.last_cpu = (idle, total)
        msg = ArtabanStatus()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.mission_id = self.mission
        msg.cpu_load = float(100.0 * (1 - d_idle / d_total)) if d_total else 0.0
        msg.memory_usage = float(memory_percent())
        msg.soc_temperature = float(thermal_c())
        ok = [ms for good, ms in self.results if good]
        if self.results:
            msg.link_latency_ms = float(sum(ok) / len(ok)) if ok else 0.0
            msg.packet_loss = float(100.0 * (len(self.results) - len(ok)) / len(self.results))
        self.pub.publish(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default="", help="host:port of the broker for link latency / loss")
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--mission-id", default="")
    a = ap.parse_args()
    rclpy.init()
    node = EdgeTelemetry(a.probe, a.period, a.mission_id)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
