# Artaban platform status at 1 Hz on /artaban/status: mission, locomotion, safety and the
# health signals not covered by standard messages (battery, odometry and IMU use sensor_msgs / nav_msgs).

std_msgs/Header header

string mission_id
string mission_state      # idle, navigating, inspecting, paused, returning, charging, safe-stop
float32 mission_progress  # 0..100 %
string current_waypoint   # e.g. "WP-03 Compressor bay"
string operation_mode     # supervised, teleop, autonomous, safe-stop, docked
string gait               # stand, walk, trot, stairs, sit
string safety_state       # nominal, warning, protective-stop
bool e_stop
float32 odometry_m        # distance walked this mission

# Motor temperatures, same order as joint_names (FL_hip, FL_thigh, FL_calf, FR_..., RL_..., RR_...)
string[] joint_names
float32[] joint_temperatures   # degC
float32 max_joint_temperature

# Edge computer and link
float32 cpu_load          # %
float32 gpu_load          # %
float32 memory_usage      # %
float32 soc_temperature   # degC
float32 wifi_rssi         # dBm
float32 link_latency_ms
float32 packet_loss       # %
