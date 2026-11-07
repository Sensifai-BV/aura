from glob import glob

from setuptools import find_packages, setup

setup(
    name="aura_intent",
    version="0.1.0",
    packages=find_packages(exclude=["test", "training"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/aura_intent"]),
        ("share/aura_intent", ["package.xml"]),
        ("share/aura_intent/config", glob("config/*.yaml")),
        ("share/aura_intent/launch", glob("launch/*.launch.py")),
        ("share/aura_intent/model", glob("model/*.gguf")),
    ],
    install_requires=["setuptools"],
    extras_require={"test": ["pytest"]},
    zip_safe=True,
    maintainer="AURA contributors",
    maintainer_email="info@sensifai.com",
    description="Operator intent from speech and gestures with confidence gating and spoken confirmation",
    license="Proprietary",
    entry_points={"console_scripts": [
        "intent = aura_intent.node:main",
        "parse = aura_intent.cli:main",
    ]},
)
