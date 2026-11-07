"""Aggregate synth / ASR / ROS 2 results into CSV tables and figures.

    python report.py --results results --ros results_ros2 --out report
"""

import argparse
import json
from pathlib import Path

import jiwer
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from asr_eval import normalise  # noqa: E402

KITTEN = "kitten2-packed-student_w4"
KITTEN_GPU = KITTEN + "-mlx"
MODELS = ("piper", "kokoro", "supertonic", KITTEN, KITTEN_GPU)
LABEL = {"piper": "Piper lessac-medium", "kokoro": "Kokoro-82M int8", "supertonic": "Supertonic 3 int8",
         KITTEN: "Kitten-TTS-2 1.7B", KITTEN_GPU: "Kitten-GPU (MLX)"}
SHORT = {"piper": "Piper", "kokoro": "Kokoro", "supertonic": "Supertonic", KITTEN: "Kitten 2\nCPU",
         KITTEN_GPU: "Kitten 2\nGPU (MLX)"}
COLOR = {"piper": "#2a78d6", "kokoro": "#eb6834", "supertonic": "#1baf7a", KITTEN: "#eda100", KITTEN_GPU: "#e87ba4"}
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.facecolor": "#fcfcfb",
                     "axes.facecolor": "#fcfcfb", "savefig.dpi": 160})


def jl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def corpus_wer(rows, key):
    return jiwer.wer([normalise(r["ref"]) for r in rows], [normalise(r[key]) or "<empty>" for r in rows])


def write_csv(path, rows):
    keys = list(rows[0])
    path.write_text(",".join(keys) + "\n" + "".join(
        ",".join(f"{r[k]:.4g}" if isinstance(r[k], float) else str(r[k]) for k in keys) + "\n" for r in rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--ros", default="results_ros2")
    ap.add_argument("--out", default="report")
    args = ap.parse_args()
    res, out = Path(args.results), Path(args.out)
    (out / "figs").mkdir(parents=True, exist_ok=True)

    # Latency
    lat, synth = [], {}
    for d in sorted(res.glob("*_t*")):
        rows, s = jl(d / "synth.jsonl"), json.loads((d / "summary.json").read_text())
        synth[(s["model"], s["threads"])] = rows
        ms, rtf = np.array([r["synth_ms"] for r in rows]), np.array([r["rtf"] for r in rows])
        lat.append({"model": s["model"], "threads": s["threads"], "p50_ms": float(np.median(ms)),
                    "p95_ms": float(np.percentile(ms, 95)), "max_ms": float(ms.max()),
                    "rtf_p50": float(np.median(rtf)), "rtf_p95": float(np.percentile(rtf, 95)),
                    "ms_per_char": float(np.median(ms / np.array([r["chars"] for r in rows]))),
                    "load_s": s["load_s"], "peak_rss_mb": s["peak_rss_mb"], "onnx_mb": s["onnx_mb"],
                    "sample_rate": s["sample_rate"]})
    lat.sort(key=lambda r: (-r["threads"], MODELS.index(r["model"])))
    write_csv(out / "latency_summary.csv", lat)

    # Intelligibility and confidence
    asr = {m: jl(res / f"{m}_t4" / "asr.jsonl") for m in MODELS if (res / f"{m}_t4" / "asr.jsonl").exists()}
    intel = []
    for m, rows in asr.items():
        for cond in ("clean", "snr5"):
            rs = [r for r in rows if r["cond"] == cond]
            conf = np.array([r["whisper_conf"] for r in rs])
            intel.append({"model": m, "cond": cond, "whisper_wer": corpus_wer(rs, "whisper"),
                          "nemotron_wer": corpus_wer(rs, "nemotron"),
                          "utt_exact_nemotron": float(np.mean([r["nemotron_wer"] == 0 for r in rs])),
                          "conf_mean": float(conf.mean()), "conf_p10": float(np.percentile(conf, 10)),
                          "min_word_conf_p10": float(np.percentile([r["whisper_min_conf"] for r in rs], 10)),
                          "n": len(rs)})
    write_csv(out / "intelligibility_summary.csv", intel)
    errors = [{"model": m, "cond": r["cond"], "idx": r["idx"], "ref": r["ref"], "whisper": r["whisper"],
               "nemotron": r["nemotron"], "whisper_wer": r["whisper_wer"], "nemotron_wer": r["nemotron_wer"]}
              for m, rows in asr.items() for r in rows if r["whisper_wer"] > 0 or r["nemotron_wer"] > 0]
    (out / "asr_errors.jsonl").write_text("".join(json.dumps(e) + "\n" for e in errors))

    # ROS 2 end to end
    ros = []
    for f in sorted(Path(args.ros).glob("*.jsonl")) if Path(args.ros).exists() else []:
        rows = jl(f)
        e2e = np.array([r["e2e_ms"] for r in rows])
        ros.append({"model": rows[0]["model"], "run": f.stem, "ok": sum(r["ok"] for r in rows), "n": len(rows),
                    "p50_ms": float(np.median(e2e)), "p95_ms": float(np.percentile(e2e, 95))})
    ros.sort(key=lambda r: MODELS.index(r["model"]))
    if ros:
        write_csv(out / "ros2_e2e_summary.csv", ros)

    figures(out / "figs", lat, synth, intel, asr, ros)


def label_bars(ax, bars, fmt):
    for b in bars:
        ax.annotate(fmt(b.get_height()),
                    (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", va="bottom",
                    xytext=(0, 2), textcoords="offset points", fontsize=8, color=INK)


def figures(figs, lat, synth, intel, asr, ros):
    present = [m for m in MODELS if any(r["model"] == m for r in lat)]

    # Latency per utterance vs text length, 4 threads
    fig, ax = plt.subplots(figsize=(7, 4))
    for m in present:
        rows = synth.get((m, 4), [])
        ax.scatter([r["chars"] for r in rows], [r["synth_ms"] for r in rows], s=14, color=COLOR[m],
                   edgecolor="#fcfcfb", linewidth=0.6, label=LABEL[m])
    ax.set_xlabel("Prompt length (characters)")
    ax.set_ylabel("Synthesis time (ms, log scale)")
    ax.set_yscale("log")
    ax.set_title("Synthesis latency vs prompt length, 4 CPU threads", color=INK, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figs / "latency_vs_length.png")
    plt.close(fig)

    # p50 / p95 by threads
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True, gridspec_kw={"width_ratios": [5, 3]})
    for ax, threads in zip(axes, (4, 2)):
        rows = [r for r in lat if r["threads"] == threads]
        x = np.arange(len(rows))
        b = ax.bar(x, [r["p50_ms"] for r in rows], width=0.6, color=[COLOR[r["model"]] for r in rows],
                   edgecolor="#fcfcfb", linewidth=2)
        ax.scatter(x, [r["p95_ms"] for r in rows], marker="_", s=300, color=INK, zorder=3, label="p95")
        label_bars(ax, b, lambda v: f"{v:.0f}")
        ax.set_xticks(x, [SHORT[r["model"]] for r in rows])
        ax.set_title(f"{threads} threads", color=INK, loc="left")
        ax.grid(axis="y", color=GRID, linewidth=0.8)
    axes[0].set_ylabel("ms per prompt (log); bar p50, tick p95")
    axes[0].set_yscale("log")
    axes[1].legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figs / "latency_threads.png")
    plt.close(fig)

    if intel:
        fig, axes = plt.subplots(1, 2, figsize=(8, 3.6), sharey=True)
        for ax, key, title in zip(axes, ("nemotron_wer", "whisper_wer"),
                                  ("AURA STT (Nemotron)", "Whisper large-v3-turbo")):
            width = 0.38
            for j, cond in enumerate(("clean", "snr5")):
                rows = [r for r in intel if r["cond"] == cond]
                x = np.arange(len(rows)) + (j - 0.5) * width
                b = ax.bar(x, [100 * r[key] for r in rows], width=width, edgecolor="#fcfcfb", linewidth=2,
                           color=[COLOR[r["model"]] for r in rows], alpha=1.0 if cond == "clean" else 0.55,
                           hatch=None if cond == "clean" else "///")
                label_bars(ax, b, lambda v: f"{v:.1f}")
            ax.set_xticks(np.arange(len(rows)), [SHORT[r["model"]] for r in rows])
            ax.set_title(title, color=INK, loc="left")
            ax.grid(axis="y", color=GRID, linewidth=0.8)
        axes[0].set_ylabel("WER % (solid clean, hatched 5 dB SNR pink noise)")
        fig.tight_layout()
        fig.savefig(figs / "wer.png")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(7, 3.6))
        data, pos, cols = [], [], []
        for i, m in enumerate(asr):
            for j, cond in enumerate(("clean", "snr5")):
                data.append([r["whisper_conf"] for r in asr[m] if r["cond"] == cond])
                pos.append(i * 3 + j)
                cols.append(COLOR[m])
        parts = ax.violinplot(data, positions=pos, showmedians=True, widths=0.8)
        for body, c, p in zip(parts["bodies"], cols, pos):
            body.set_facecolor(c)
            body.set_alpha(0.75 if p % 3 == 0 else 0.4)
        for k in ("cmedians", "cmins", "cmaxes", "cbars"):
            parts[k].set_color(INK2)
        ax.set_xticks([i * 3 + 0.5 for i in range(len(asr))], [LABEL[m] for m in asr])
        ax.set_ylabel("ASR word confidence per utterance")
        ax.set_title("Listener confidence (Whisper word probability); left clean, right 5 dB SNR",
                     color=INK, loc="left", fontsize=9.5)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        fig.tight_layout()
        fig.savefig(figs / "confidence.png")
        plt.close(fig)

    if ros:
        fig, ax = plt.subplots(figsize=(6, 3.2))
        x = np.arange(len(ros))
        b = ax.bar(x, [r["p50_ms"] for r in ros], width=0.6, color=[COLOR[r["model"]] for r in ros],
                   edgecolor="#fcfcfb", linewidth=2)
        ax.scatter(x, [r["p95_ms"] for r in ros], marker="_", s=300, color=INK, zorder=3, label="p95")
        label_bars(ax, b, lambda v: f"{v:.0f}")
        ax.set_xticks(x, [SHORT[r["model"]] for r in ros])
        ax.set_ylabel("say topic → audio topic (ms)")
        ax.set_title("ROS 2 Lyrical end to end, arm64 VM, 4 threads", color=INK, loc="left")
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(figs / "ros2_e2e.png")
        plt.close(fig)


if __name__ == "__main__":
    main()
