"""Parse transcripts with the intent model, without ROS.

    ros2 run aura_intent parse "robot go to the compressor bay"
    python -m aura_intent.cli --model model/aura-intent-lfm2.5-1.2b-q8_0.gguf   # interactive
"""

import argparse
import sys

from .config import IntentConfig, resolve_model_path
from .model import IntentModel


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*")
    ap.add_argument("--model", default=IntentConfig.model_path)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--gpu-layers", type=int, default=0)
    args = ap.parse_args(argv)
    model = IntentModel(resolve_model_path(args.model), args.threads, n_gpu_layers=args.gpu_layers)
    model.warmup()
    lines = [" ".join(args.text)] if args.text else (line.rstrip("\n") for line in sys.stdin)
    for line in lines:
        if not line.strip():
            continue
        p = model.parse(line)
        target = f"  target={p.target!r}" if p.target else ""
        print(f"{p.intent:<14} p={p.confidence:.3f}  {p.inference_ms:5.0f} ms{target}   <- {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
