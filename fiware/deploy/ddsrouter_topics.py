"""DDS types of the topics bridged between the robot and Orion-LD (shared by the router entrypoint and the type announcer).

DDS topic name -> DDS type name. The ROS 2 message class is derived from the type name.
"""
TYPES = {
    "rt/battery_state": "sensor_msgs::msg::dds_::BatteryState_",
    "rt/odom": "nav_msgs::msg::dds_::Odometry_",
    "rt/imu/data": "sensor_msgs::msg::dds_::Imu_",
    "rt/artaban/status": "aura_interfaces::msg::dds_::ArtabanStatus_",
    "rt/artaban/environment": "aura_interfaces::msg::dds_::EnvironmentReading_",
    "rt/aura_partial_transcript": "std_msgs::msg::dds_::String_",
    "rt/aura_transcript": "std_msgs::msg::dds_::String_",
    "rt/aura/gesture/intent": "aura_interfaces::msg::dds_::GestureIntent_",
    "rt/aura/intent/event": "aura_interfaces::msg::dds_::IntentEvent_",
    "rt/aura/speaker/say": "std_msgs::msg::dds_::String_",
    "rt/aura/stt/status": "aura_interfaces::msg::dds_::ModuleStatus_",
    "rt/aura/intent/status": "aura_interfaces::msg::dds_::ModuleStatus_",
    "rt/aura/gesture/status": "aura_interfaces::msg::dds_::ModuleStatus_",
    "rt/aura/speaker/status": "aura_interfaces::msg::dds_::ModuleStatus_",
}
