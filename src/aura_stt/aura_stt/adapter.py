"""Run encoder adapters trained in NeMo on the Transformers model.

NeMo's LinearAdapter sits after each Conformer block's final LayerNorm and adds
`up(silu(down(norm(x))))` back onto that output. The Transformers blocks end at
the same LayerNorm, so a forward hook on each block reproduces it exactly.

An adapter directory holds only the adapter tensors and a pointer to its base
model; the base weights are frozen during adapter training, so they are shared:

    models/nemotron-welding/adapter_config.json   {"base_model": "../nemotron-speech-streaming-en-0.6b"}
    models/nemotron-welding/adapter.safetensors   encoder.layers.<i>.adapter.{norm,down,up}.*

`scripts/train/export_adapter.py` writes one from a NeMo checkpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

from safetensors.torch import load_file
from torch import nn

CONFIG_NAME = "adapter_config.json"
WEIGHTS_NAME = "adapter.safetensors"


class EncoderAdapter(nn.Module):
    """NeMo `LinearAdapter` (pre-norm, swish) with its residual add folded in."""

    def __init__(self, hidden_size: int, dim: int) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(hidden_size)
        self.down = nn.Linear(hidden_size, dim, bias=False)
        self.up = nn.Linear(dim, hidden_size, bias=False)

    def forward(self, x):
        return x + self.up(nn.functional.silu(self.down(self.norm(x))))


def base_of(model_dir: Path) -> Path:
    """The directory holding the Transformers weights: `model_dir` itself, or
    the base an adapter directory points at."""
    model_dir = Path(model_dir)
    config = model_dir / CONFIG_NAME
    if not config.exists():
        return model_dir
    return (model_dir / json.loads(config.read_text())["base_model"]).resolve()


def attach(model: nn.Module, adapter_dir: Path) -> None:
    """Give every encoder block of `model` its adapter from `adapter_dir`."""
    path = Path(adapter_dir) / WEIGHTS_NAME
    weights = load_file(path)
    blocks = model.encoder.layers
    used = 0
    for i, block in enumerate(blocks):
        prefix = f"encoder.layers.{i}.adapter."
        own = {k[len(prefix):]: v for k, v in weights.items() if k.startswith(prefix)}
        if not own:
            raise ValueError(f"{path} has no adapter for encoder layer {i}")
        adapter = EncoderAdapter(own["norm.weight"].shape[0], own["down.weight"].shape[0])
        adapter.load_state_dict(own)
        block.adapter = adapter
        block.register_forward_hook(lambda module, args, output: module.adapter(output))
        used += len(own)
    if used != len(weights):
        raise ValueError(f"{path} has {len(weights) - used} tensors for layers this model lacks "
                         f"({len(blocks)} encoder layers)")
