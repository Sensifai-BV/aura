"""Fine-tuned LFM2.5 intent parser on llama.cpp (GGUF, CPU or Metal).

The intent label is decoded over the closed label set: starting after
`{"intent": "`, every step keeps only tokens that continue some label, picks the
best one and multiplies its unrestricted probability. The product is the model's
probability of the emitted label sequence, used as the command confidence.
The free-text target is generated greedily only for target_select.

LFM2 is a hybrid conv/attention model: its recurrent conv state cannot be rewound
to a token position, so instead of KV prefix reuse the context state after the
shared system prompt is snapshotted once and restored for every transcript.
"""

import math
import time

import numpy as np

from .schema import LABELS, Parse, messages

PREFIX = '{"intent": "'
TARGET_PREFIX = '", "target": "'
TARGET_KEY = ' "target": "'  # after the label token that holds the closing '",'


class IntentModel:
    def __init__(self, model_path, n_threads=4, n_ctx=512, n_gpu_layers=0, max_target_tokens=24):
        import llama_cpp
        from llama_cpp.llama_chat_format import Jinja2ChatFormatter

        self._llama_cpp = llama_cpp
        self.max_target_tokens = max_target_tokens
        self.llm = llama_cpp.Llama(model_path=str(model_path), n_ctx=n_ctx, n_threads=n_threads,
                                   n_threads_batch=n_threads, n_gpu_layers=n_gpu_layers, verbose=False)
        eos = self._token_text(self.llm.token_eos())
        bos = self._token_text(self.llm.token_bos())
        self.formatter = Jinja2ChatFormatter(self.llm.metadata["tokenizer.chat_template"], eos_token=eos,
                                             bos_token=bos, add_generation_prompt=True)
        self._build_trie()
        self.target_key = self._tokenize(TARGET_KEY)
        self._snapshot_system_prompt()

    def _token_text(self, token):
        return self.llm.detokenize([token], special=True).decode("utf-8", errors="ignore")

    def _tokenize(self, text):
        return self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=True)

    def _build_trie(self):
        """Tokenize every label in context; the shared token prefix goes into the prompt.

        Each label sequence runs up to the token holding its closing quote (which BPE
        merges with the following comma), so the label is scored as trained."""
        seqs = {}
        for label in LABELS:
            tokens = self._tokenize(PREFIX + label + TARGET_PREFIX)
            for end in range(1, len(tokens) + 1):
                if self.llm.detokenize(tokens[:end], special=True).decode("utf-8", "ignore").endswith(
                        (label + '"', label + '",')):
                    break
            seqs[label] = tokens[:end]
        common = 0
        first = seqs[LABELS[0]]
        while all(len(s) > common and s[common] == first[common] for s in seqs.values()):
            common += 1
        self.prefix_tokens = first[:common]
        self.label_tokens = {label: s[common:] for label, s in seqs.items()}

    def prompt_tokens(self, transcript):
        return self._tokenize(self.formatter(messages=messages(transcript)).prompt)

    def _snapshot_system_prompt(self):
        a, b = self.prompt_tokens("a"), self.prompt_tokens("b")
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        self.system_tokens = a[:n]
        self.llm.reset()
        self.llm.eval(self.system_tokens)
        self.system_state = self.llm.save_state()

    def _last_logits(self):
        ptr = self._llama_cpp.llama_get_logits_ith(self.llm.ctx, -1)
        return np.ctypeslib.as_array(ptr, shape=(self.llm.n_vocab(),)).astype(np.float64)

    def _start(self, tokens):
        n = len(self.system_tokens)
        if tokens[:n] == self.system_tokens and len(tokens) > n:
            self.llm.load_state(self.system_state)
            self.llm.eval(tokens[n:])
        else:
            self.llm.reset()
            self.llm.eval(tokens)

    def parse(self, transcript):
        start = time.perf_counter()
        self._start(self.prompt_tokens(transcript) + self.prefix_tokens)
        candidates, path, logprob = dict(self.label_tokens), [], 0.0
        while True:
            depth = len(path)
            done = [label for label, seq in candidates.items() if len(seq) == depth]
            if done:
                label = done[0]
                break
            logits = self._last_logits()
            allowed = {seq[depth] for seq in candidates.values()}
            best = max(allowed, key=lambda t: logits[t])
            peak = logits.max()
            logprob += logits[best] - peak - math.log(np.exp(logits - peak).sum())
            path.append(best)
            candidates = {label: seq for label, seq in candidates.items() if seq[depth] == best}
            self.llm.eval([best])
        target = self._target() if label == "target_select" else ""
        return Parse(label, target, float(math.exp(logprob)), (time.perf_counter() - start) * 1000)

    def _target(self):
        self.llm.eval(self.target_key)
        out = b""
        for _ in range(self.max_target_tokens):
            token = int(np.argmax(self._last_logits()))
            piece = self.llm.detokenize([token])
            if token == self.llm.token_eos() or b'"' in piece or b"\n" in piece:
                out += piece.split(b'"')[0]
                break
            out += piece
            self.llm.eval([token])
        return out.decode("utf-8", errors="ignore").strip()

    def warmup(self):
        self.parse("stop")
