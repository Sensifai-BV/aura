from glob import glob

from setuptools import find_packages, setup

setup(
    name="aura_tts",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/aura_tts"]),
        ("share/aura_tts", ["package.xml"]),
        ("share/aura_tts/config", glob("config/*.yaml")),
        ("share/aura_tts/launch", glob("launch/*.launch.py")),
        # Supertonic 3 int8 (MIT code, OpenRAIL-M weights), chosen in docs/design/tts-model-selection.md
        ("share/aura_tts/model/supertonic-3-int8", glob("model/supertonic-3-int8/*")),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest"]},
    zip_safe=True,
    maintainer="AURA contributors",
    maintainer_email="info@sensifai.com",
    description="Speaker module: sherpa-onnx text-to-speech (Supertonic 3) with interruptible playback",
    license="Proprietary",
    entry_points={"console_scripts": ["tts = aura_tts.node:main"]},
)
