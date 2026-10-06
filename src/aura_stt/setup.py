from glob import glob

from setuptools import find_packages, setup

setup(
    name="aura_stt",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/aura_stt"]),
        ("share/aura_stt", ["package.xml"]),
        ("share/aura_stt/config", glob("config/*.yaml")),
        ("share/aura_stt/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest", "coverage>=7.6"]},
    zip_safe=True,
    maintainer="AURA contributors",
    maintainer_email="info@sensifai.com",
    description="Microphone STT with local fine-tuned Nemotron streaming weights",
    license="Proprietary",
    entry_points={"console_scripts": ["stt = aura_stt.node:main"]},
)
