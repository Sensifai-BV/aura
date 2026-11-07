"""LoRA fine-tune of LFM2.5-1.2B-Instruct on Apple silicon with a resource budget.

MLX has no GPU-percentage knob, so the budget is applied where it can be enforced:
Metal memory is capped at --budget of physical RAM, CPU threads at the same share
of the cores, and the process runs at lower scheduling priority.

    python train.py                 # uses lora.yaml, budget 0.7
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "lora.yaml"))
    ap.add_argument("--budget", type=float, default=0.7, help="share of RAM / CPU cores to use")
    args, rest = ap.parse_known_args()

    cores = os.cpu_count() or 1
    threads = str(max(1, int(cores * args.budget)))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "TOKENIZERS_PARALLELISM"):
        os.environ[var] = threads if var != "TOKENIZERS_PARALLELISM" else "false"
    os.nice(5)

    import mlx.core as mx

    ram = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]))
    limit = int(ram * args.budget)
    mx.set_memory_limit(limit)
    mx.set_cache_limit(limit // 4)
    print(f"Budget {args.budget:.0%}: Metal memory <= {limit / 2**30:.1f} GiB, {threads}/{cores} CPU threads")

    from mlx_lm import lora

    os.chdir(HERE)
    sys.argv = ["mlx_lm.lora", "--config", args.config, *rest]
    lora.main()
    print(f"Peak Metal memory {mx.get_peak_memory() / 2**30:.2f} GiB")


if __name__ == "__main__":
    main()
