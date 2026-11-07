from glob import glob

from setuptools import find_packages, setup

setup(
    name="aura_gesture",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/aura_gesture"]),
        ("share/aura_gesture", ["package.xml"]),
        ("share/aura_gesture/config", glob("config/*.yaml")),
        ("share/aura_gesture/launch", glob("launch/*.launch.py")),
        ("share/aura_gesture/model/gesture", glob("model/*.onnx") + glob("model/gesture/*.onnx")),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest"]},
    zip_safe=True,
    maintainer="AURA contributors",
    maintainer_email="info@sensifai.com",
    description="Camera hand-gesture recognition with confidence-aware intent acceptance",
    license="Proprietary",
    entry_points={"console_scripts": [
        "gesture = aura_gesture.node:main",
        "camera = aura_gesture.camera_node:main",
        "debug = aura_gesture.debug:main",
    ]},
)
