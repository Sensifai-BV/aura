cmake_minimum_required(VERSION 3.16)
project(aura_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(vision_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/GestureIntent.msg"
  "msg/IntentEvent.msg"
  "msg/ArtabanStatus.msg"
  "msg/EnvironmentReading.msg"
  "msg/ModuleStatus.msg"
  DEPENDENCIES std_msgs vision_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
