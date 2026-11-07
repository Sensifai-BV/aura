#!/usr/bin/env bash
# Merge the LoRA adapter and export the GGUF the intent node loads.
#   ./export_gguf.sh            # -> ../model/aura-intent-lfm2.5-1.2b-q8_0.gguf
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}
LLAMA_CPP_REF=${LLAMA_CPP_REF:-b6900}
OUTTYPE=${OUTTYPE:-q8_0}
OUT=../model/aura-intent-lfm2.5-1.2b-${OUTTYPE}.gguf

if [ ! -d .llama.cpp ]; then
  git clone --depth 1 --branch "$LLAMA_CPP_REF" https://github.com/ggml-org/llama.cpp .llama.cpp
fi
"$PY" merge_lora.py --adapter adapters --out fused
mkdir -p ../model
PYTHONPATH=.llama.cpp/gguf-py "$PY" .llama.cpp/convert_hf_to_gguf.py fused --outtype "$OUTTYPE" --outfile "$OUT"
ls -lh "$OUT"
