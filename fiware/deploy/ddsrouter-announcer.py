"""Announce the bridged topics' message types on the local DDS domain, publishing nothing.

Orion-LD needs a DDS writer (with its type description) before it creates the reader for a topic, and DDS Router only
creates a writer for data coming from the robot. This process breaks that circle: it declares one publisher per topic,
so Orion-LD learns the schema and subscribes, then the router's data (same type name) is decoded. No sample is ever
published here, so every entity stays "uninitialized" until the real robot sends data.
"""
import importlib
import os
import sys

import rclpy
from rclpy.node import Node

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ddsrouter_topics import TYPES  # noqa: E402

rclpy.init()
node = Node("aura_type_announcer")
keep = []
for dds_topic, dds_type in TYPES.items():
    package, _, _, name = dds_type.rstrip("_").split("::")
    cls = getattr(importlib.import_module(f"{package}.msg"), name)
    keep.append(node.create_publisher(cls, "/" + dds_topic.removeprefix("rt/"), 10))
print(f"announcing {len(keep)} topic types", flush=True)
rclpy.spin(node)
