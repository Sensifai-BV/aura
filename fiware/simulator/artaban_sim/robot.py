"""Artaban quadruped + inspection-site physics (simplified but coherent)."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Site frame origin projected near Trnava (SK); 60 x 40 m plant.
ORIGIN_LAT, ORIGIN_LON = 48.37740, 17.58720
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(ORIGIN_LAT))

DOCK = (4.0, 4.0)


@dataclass(frozen=True)
class Waypoint:
    code: str
    name: str
    x: float
    y: float
    noise_db: float      # typical ambient level at this spot
    noise_source: str


ROUTE = [
    Waypoint("WP-01", "Control room", 10, 32, 58, "ambient"),
    Waypoint("WP-02", "Pump station", 24, 30, 79, "pump"),
    Waypoint("WP-03", "Compressor bay", 44, 32, 86, "compressor"),
    Waypoint("WP-04", "Separator skid", 54, 22, 74, "pump"),
    Waypoint("WP-05", "Tank farm A", 48, 8, 63, "ambient"),
    Waypoint("WP-06", "Tank farm B", 34, 8, 62, "ambient"),
    Waypoint("WP-07", "Flare KO drum", 20, 14, 68, "ambient"),
]

JOINTS = [f"{leg}_{part}" for leg in ("FL", "FR", "RL", "RR") for part in ("hip", "thigh", "calf")]


def to_latlon(x: float, y: float) -> tuple[float, float]:
    return ORIGIN_LAT + y / M_PER_DEG_LAT, ORIGIN_LON + x / M_PER_DEG_LON


def _approach(cur: float, target: float, rate: float) -> float:
    return cur + (target - cur) * rate


@dataclass
class Scenario:
    name: str
    remaining_s: float


@dataclass
class Robot:
    dwell_s: float = 40.0
    x: float = DOCK[0]
    y: float = DOCK[1]
    yaw: float = 0.0
    speed: float = 0.0
    odometry: float = 0.0
    battery: float = 96.0
    charging: bool = False
    state: str = "idle"           # idle navigating inspecting paused returning charging safe-stop
    mode: str = "supervised"
    gait: str = "stand"
    wp_index: int = 0
    dwell_left: float = 0.0
    pause_left: float = 0.0
    estop_left: float = 0.0
    inspections_done: int = 0
    joint_temp: dict = field(default_factory=lambda: {j: 34.0 + random.uniform(-1, 1) for j in JOINTS})
    cpu: float = 35.0
    gpu: float = 30.0
    soc_temp: float = 52.0
    noise_db: float = 55.0
    noise_source: str = "ambient"
    h2s: float = 0.2
    ch4: float = 0.5
    co: float = 1.0
    o2: float = 20.9
    air_temp: float = 17.0
    humidity: float = 58.0
    thermal_max: float = 41.0
    rssi: float = -58.0
    link_latency: float = 28.0
    packet_loss: float = 0.2
    roll: float = 0.0
    pitch: float = 0.0
    scenarios: dict = field(default_factory=dict)  # name -> seconds left

    # -- mission control -------------------------------------------------
    def start_mission(self) -> None:
        self.state, self.mode, self.wp_index, self.odometry = "navigating", "supervised", 0, 0.0
        self.inspections_done, self.charging = 0, False

    @property
    def target(self) -> tuple[float, float, str]:
        if self.state == "returning" or self.wp_index >= len(ROUTE):
            return DOCK[0], DOCK[1], "DOCK Charging station"
        wp = ROUTE[self.wp_index]
        return wp.x, wp.y, f"{wp.code} {wp.name}"

    @property
    def progress(self) -> float:
        if self.state in ("idle", "charging"):
            return 100.0 if self.inspections_done else 0.0
        return round(100.0 * self.inspections_done / (len(ROUTE) + 1), 1)

    def pause(self, seconds: float) -> None:
        if self.state in ("navigating", "returning", "inspecting"):
            self._resume_state = self.state
            self.state, self.pause_left, self.speed, self.gait = "paused", seconds, 0.0, "stand"

    def resume(self) -> None:
        if self.state == "paused":
            self.state, self.pause_left = getattr(self, "_resume_state", "navigating"), 0.0

    def trigger_estop(self, seconds: float = 20.0) -> None:
        self._resume_state = self.state if self.state not in ("paused", "safe-stop") else getattr(self, "_resume_state", "navigating")
        self.state, self.mode, self.speed, self.gait, self.estop_left = "safe-stop", "safe-stop", 0.0, "sit", seconds

    def scenario(self, name: str, seconds: float) -> None:
        self.scenarios[name] = seconds
        if name == "estop":
            self.trigger_estop(seconds)
        if name == "low_battery":
            self.battery = min(self.battery, 14.0)

    def active(self, name: str) -> bool:
        return self.scenarios.get(name, 0) > 0

    # -- step --------------------------------------------------------------
    def step(self, dt: float) -> str | None:
        """Advance physics; returns 'mission_complete' when docked after the route."""
        event = None
        for k in list(self.scenarios):
            self.scenarios[k] -= dt
            if self.scenarios[k] <= 0:
                del self.scenarios[k]

        if self.state == "safe-stop":
            self.estop_left -= dt
            if self.estop_left <= 0:
                self.state, self.mode = getattr(self, "_resume_state", "navigating"), "supervised"
        elif self.state == "paused":
            self.pause_left -= dt
            if self.pause_left <= 0:
                self.resume()
        elif self.state in ("navigating", "returning"):
            tx, ty, _ = self.target
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            cruise = 0.45 if self.active("comms_degraded") else 0.85
            self.speed = _approach(self.speed, min(cruise, dist), 0.5)
            self.gait = "trot" if self.speed > 0.6 else "walk"
            desired = math.atan2(dy, dx)
            err = (desired - self.yaw + math.pi) % (2 * math.pi) - math.pi
            self.yaw += max(-0.6, min(0.6, err)) * dt * 2
            step = min(dist, self.speed * dt)
            self.x += math.cos(desired) * step
            self.y += math.sin(desired) * step
            self.odometry += step
            if dist < 0.4:
                self.speed = 0.0
                if self.state == "returning" or self.wp_index >= len(ROUTE):
                    self.state, self.mode, self.gait, self.charging = "charging", "docked", "sit", True
                    self.inspections_done += 1
                    event = "mission_complete"
                else:
                    self.state, self.gait = "inspecting", "stand"
                    self.dwell_left = self.dwell_s * random.uniform(0.7, 1.3)
        elif self.state == "inspecting":
            self.speed = 0.0
            self.dwell_left -= dt
            if self.dwell_left <= 0:
                self.inspections_done += 1
                self.wp_index += 1
                self.state = "returning" if self.wp_index >= len(ROUTE) else "navigating"
        elif self.state == "charging":
            self.speed = 0.0

        self._update_power(dt)
        self._update_thermal(dt)
        self._update_environment(dt)
        self._update_compute_and_comms(dt)
        return event

    def _update_power(self, dt: float) -> None:
        moving = self.speed > 0.05
        self.power = (310 if moving else 140 if self.state != "charging" else 60) + random.gauss(0, 12) + 120 * self.speed
        if self.charging:
            self.battery = min(100.0, self.battery + 0.35 * dt)
        else:
            self.battery = max(3.0, self.battery - self.power * dt / 9000)
        self.voltage = 44.0 + 0.085 * self.battery + random.gauss(0, 0.05)
        self.current = (-12.0 if self.charging else self.power / self.voltage) + random.gauss(0, 0.2)
        self.battery_temp = _approach(getattr(self, "battery_temp", 27.0), 26 + self.power / 40, 0.02)

    def _update_thermal(self, dt: float) -> None:
        load = 0.9 if self.speed > 0.05 else 0.25 if self.state not in ("charging", "safe-stop") else 0.05
        for j in JOINTS:
            base = 34 + load * (16 if "calf" in j else 12 if "thigh" in j else 8)
            if j == "RR_calf" and self.active("motor_overheat"):
                base = 82
            self.joint_temp[j] = _approach(self.joint_temp[j], base + random.gauss(0, 0.3), 0.03 if base < 70 else 0.05)
        self.roll = random.gauss(0, 1.2 if self.speed > 0 else 0.3)
        self.pitch = random.gauss(-1.0 if self.gait == "trot" else 0, 1.0 if self.speed > 0 else 0.3)

    def _nearest_wp(self) -> Waypoint:
        return min(ROUTE, key=lambda w: math.hypot(w.x - self.x, w.y - self.y))

    def _update_environment(self, dt: float) -> None:
        wp = self._nearest_wp()
        d = math.hypot(wp.x - self.x, wp.y - self.y)
        ambient = 55 + max(0.0, (wp.noise_db - 55) * math.exp(-d / 9))
        source = wp.noise_source if d < 12 else "ambient"
        if self.active("noise_spike"):
            ambient, source = max(ambient, 90), "compressor"
        if random.random() < 0.01:
            source = "radio"
            ambient += 6
        self.noise_db = _approach(self.noise_db, ambient + random.gauss(0, 1.2), 0.35)
        self.noise_peak = self.noise_db + abs(random.gauss(6, 2))
        self.noise_source = source

        gas = self.active("gas_leak")
        near_tanks = self.y < 16 and self.x > 28
        self.h2s = _approach(self.h2s, (18 if gas else 0.4 if near_tanks else 0.1) + abs(random.gauss(0, 0.05)), 0.08)
        self.ch4 = _approach(self.ch4, (16 if gas else 1.2 if near_tanks else 0.3) + abs(random.gauss(0, 0.05)), 0.08)
        self.co = _approach(self.co, (9 if gas else 1.5) + abs(random.gauss(0, 0.1)), 0.05)
        self.o2 = _approach(self.o2, (20.1 if gas else 20.9) + random.gauss(0, 0.02), 0.05)
        self.air_temp = _approach(self.air_temp, 17 + (6 if wp.noise_source == "compressor" and d < 10 else 0), 0.02)
        self.humidity = _approach(self.humidity, 58 + random.gauss(0, 1), 0.05)
        hot = 96 if wp.noise_source == "compressor" and d < 10 else 64 if wp.noise_source == "pump" and d < 10 else 38
        self.thermal_max = _approach(self.thermal_max, hot + random.gauss(0, 1.5), 0.15)

    def _update_compute_and_comms(self, dt: float) -> None:
        busy = self.state in ("inspecting", "navigating", "returning")
        self.cpu = _approach(self.cpu, (58 if busy else 24) + random.gauss(0, 4), 0.3)
        self.gpu = _approach(self.gpu, (66 if self.state == "inspecting" else 44 if busy else 12) + random.gauss(0, 4), 0.3)
        self.mem = 47 + random.gauss(0, 1.5)
        self.soc_temp = _approach(self.soc_temp, 45 + self.gpu * 0.32, 0.05)
        dist_to_dock = math.hypot(self.x - DOCK[0], self.y - DOCK[1])
        degraded = self.active("comms_degraded")
        self.rssi = _approach(self.rssi, -45 - dist_to_dock * 0.45 - (14 if degraded else 0) + random.gauss(0, 1.5), 0.3)
        self.link_latency = max(4.0, _approach(self.link_latency, (320 if degraded else 22 + dist_to_dock * 0.6) + random.gauss(0, 4), 0.3))
        self.packet_loss = max(0.0, _approach(self.packet_loss, (11 if degraded else 0.3) + random.gauss(0, 0.2), 0.3))

    @property
    def safety_state(self) -> str:
        if self.state == "safe-stop":
            return "protective-stop"
        if max(self.joint_temp.values()) > 70 or self.h2s > 10 or self.ch4 > 10 or self.battery < 15:
            return "warning"
        return "nominal"
