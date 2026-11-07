"""Merge the MLX LoRA adapter into the original Hugging Face checkpoint.

mlx_lm.fuse writes MLX-layout tensors (LFM2 conv kernels are transposed), which
llama.cpp's converter does not expect; merging into the untouched HF safetensors
keeps the layout the converter knows. MLX LoRA: y = xW^T + scale * (x A) B,
so W' = W + scale * (A B)^T with A: (in, r), B: (r, out).

    python merge_lora.py --adapter adapters --out fused
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from huggingface_hub import snapshot_download
from safetensors.numpy import load_file
from safetensors.torch import load_file as load_torch, save_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="LiquidAI/LFM2.5-1.2B-Instruct")
    ap.add_argument("--adapter", default="adapters")
    ap.add_argument("--out", default="fused")
    args = ap.parse_args()

    base = Path(snapshot_download(args.base))
    adapter_dir = Path(args.adapter)
    cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    scale = float(cfg["lora_parameters"]["scale"])
    lora = load_file(str(adapter_dir / "adapters.safetensors"))
    pairs = {k[: -len(".lora_a")]: (lora[k], lora[k[:-1] + "b"]) for k in lora if k.endswith(".lora_a")}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in base.iterdir():
        if f.suffix != ".safetensors" and f.is_file():
            shutil.copy(f, out / f.name)
    merged = 0
    for shard in sorted(base.glob("*.safetensors")):
        tensors = load_torch(str(shard))
        for prefix, (a, b) in pairs.items():
            key = prefix + ".weight"
            if key not in tensors:
                continue
            w = tensors[key]
            delta = (scale * (a.astype(np.float32) @ b.astype(np.float32))).T
            if delta.shape != tuple(w.shape):
                raise ValueError(f"{key}: delta {delta.shape} vs weight {tuple(w.shape)}")
            import torch

            tensors[key] = (w.float() + torch.from_numpy(delta)).to(w.dtype)
            merged += 1
        save_file(tensors, str(out / shard.name), metadata={"format": "pt"})
    if merged != len(pairs):
        raise RuntimeError(f"merged {merged} of {len(pairs)} LoRA layers; check key names")
    print(f"Merged {merged} LoRA layers (scale {scale}) into {out}")


if __name__ == "__main__":
    main()
