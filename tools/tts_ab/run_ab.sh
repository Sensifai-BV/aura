#!/usr/bin/env bash
# Full TTS A/B: synthesis latency (4 and 2 threads), ASR scoring, ROS 2 probe in the arm64 VM, report.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
for t in 4 2; do
  for m in piper kokoro supertonic; do $PY synth.py --model "$m" --threads "$t" --runs 3; done
done
$PY asr_eval.py results/piper_t4 results/kokoro_t4 results/supertonic_t4 | tee results/asr.log
for m in piper kokoro supertonic; do
  orb -m ros2 bash -lc "source /opt/ros/lyrical/setup.bash && cd '$PWD' && ~/tts_venv/bin/python ros2_probe.py --model $m --threads 4"
done
$PY report.py
