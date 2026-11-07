"""Accuracy, confidence calibration and latency of the exported GGUF on a held-out set.

The test sentences are hand written with phrasing that is not in the generator,
so this measures generalisation rather than template recall.

    python evaluate.py ../model/aura-intent-lfm2.5-1.2b-q8_0.gguf
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aura_intent.model import IntentModel  # noqa: E402

TEST = [
    # stop
    ("Stop, stop! There's someone in front of you.", "stop"), ("ROBOT HALT", "stop"), ("stop it now", "stop"),
    ("Artaban, freeze right where you are.", "stop"), ("emergency halt", "stop"), ("no no stop moving", "stop"),
    ("Hey, quit moving!", "stop"), ("stahp", "stop"),
    # pause
    ("Hold on a sec, robot.", "pause"), ("Can you wait there for a bit?", "pause"), ("pause pause", "pause"),
    ("artaban hold still there for a minute", "pause"), ("Stay right there until I say so.", "pause"),
    ("take five robot", "pause"), ("paws the mission", "pause"),
    # resume
    ("Okay, you can carry on now.", "resume"), ("continue the patrol please", "resume"),
    ("Alright robot, back to your route.", "resume"), ("you're good to go again", "resume"),
    ("resume where you stopped", "resume"), ("Go on with the inspection.", "resume"),
    # confirm
    ("Yes.", "confirm"), ("Yeah, that's it.", "confirm"), ("correct, go ahead", "confirm"),
    ("yup do that", "confirm"), ("Affirmative.", "confirm"), ("That's right, do it.", "confirm"),
    ("sure thing", "confirm"),
    # cancel
    ("No.", "cancel"), ("No, not that one.", "cancel"), ("cancel cancel", "cancel"), ("Forget about it.", "cancel"),
    ("abort the last command", "cancel"), ("nah don't do that", "cancel"), ("That's not what I said.", "cancel"),
    # next_step
    ("Next one please.", "next_step"), ("ok we're done here, move to the next point", "next_step"),
    ("go on to the following checkpoint", "next_step"), ("Skip it, next.", "next_step"),
    ("advance to the next inspection point", "next_step"), ("robot next station", "next_step"),
    # target_select
    ("Go to the compressor bay.", "target_select", "compressor bay"),
    ("robot inspect valve number nine", "target_select", "valve number nine"),
    ("Can you check the pressure gauge on pump four?", "target_select", "pressure gauge on pump four"),
    ("Artaban, walk over to the separator tank.", "target_select", "separator tank"),
    ("take a look at the hot flange", "target_select", "hot flange"),
    ("head to waypoint seven", "target_select", "waypoint seven"),
    ("Inspect the leaking pipe near the boiler.", "target_select", "leaking pipe near the boiler"),
    ("move to tank c please", "target_select", "tank c"),
    # come_follow
    ("Come here, robot.", "come_follow"), ("follow me to the exit", "come_follow"),
    ("Artaban, come with me.", "come_follow"), ("stay with me", "come_follow"), ("come back here", "come_follow"),
    ("Walk behind me.", "come_follow"), ("over here robot", "come_follow"),
    # unknown (addressed, unsupported)
    ("Robot, open that valve for me.", "unknown"), ("artaban what's the gas level", "unknown"),
    ("Can you play some music?", "unknown"), ("robot climb onto the platform", "unknown"),
    ("Take a selfie.", "unknown"), ("robot shut down the pump", "unknown"),
    # none (not addressed)
    ("Did anyone see where I left the torch?", "none"), ("We'll stop by the warehouse later.", "none"),
    ("Mike, follow the red line to the gate.", "none"), ("i think we should cancel the night shift", "none"),
    ("Control, this is team one, all clear.", "none"), ("Yeah I know, it's been a long day.", "none"),
    ("The next inspection is on Thursday.", "none"), ("Who paused the generator?", "none"),
    ("lunch is at noon", "none"), ("um", "none"), ("Thanks, see you later.", "none"),
    ("He told me to go to the compressor bay.", "none"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--gpu-layers", type=int, default=0)
    ap.add_argument("--out", default=None, help="write per-case results as JSON")
    args = ap.parse_args()
    model = IntentModel(args.model, args.threads, n_gpu_layers=args.gpu_layers)
    model.warmup()
    rows, confusion = [], Counter()
    for case in TEST:
        text, gold = case[0], case[1]
        p = model.parse(text)
        ok = p.intent == gold
        confusion[(gold, p.intent)] += 1
        rows.append(dict(text=text, gold=gold, pred=p.intent, target=p.target, gold_target=case[2] if len(case) > 2 else "",
                         confidence=round(p.confidence, 4), ms=round(p.inference_ms, 1), ok=ok))
        if not ok:
            print(f"MISS {gold:>13} -> {p.intent:<13} p={p.confidence:.2f}  {text}")
    n = len(rows)
    acc = sum(r["ok"] for r in rows) / n
    ms = sorted(r["ms"] for r in rows)
    right = [r["confidence"] for r in rows if r["ok"]]
    wrong = [r["confidence"] for r in rows if not r["ok"]]
    print(f"\naccuracy {acc:.3f} ({sum(r['ok'] for r in rows)}/{n})")
    for label in sorted({r["gold"] for r in rows}):
        sub = [r for r in rows if r["gold"] == label]
        print(f"  {label:<14} {sum(r['ok'] for r in sub)}/{len(sub)}")
    targets = [r for r in rows if r["gold_target"] and r["pred"] == "target_select"]
    for r in targets:
        print(f"  target {r['target']!r:<38} expected ~{r['gold_target']!r}")
    print(f"confidence: correct mean {statistics.mean(right):.3f}"
          + (f", wrong mean {statistics.mean(wrong):.3f}" if wrong else ""))
    for name, thr in (("accept", 0.72), ("reject", 0.52)):
        above = [r for r in rows if r["confidence"] >= thr]
        print(f"  >= {name} {thr}: {len(above)}/{n} cases, precision "
              f"{sum(r['ok'] for r in above) / max(1, len(above)):.3f}")
    print(f"latency ms: p50 {ms[n // 2]:.0f}, p95 {ms[int(n * 0.95)]:.0f}, max {ms[-1]:.0f}")
    if args.out:
        Path(args.out).write_text(json.dumps(dict(accuracy=acc, rows=rows), indent=1))


if __name__ == "__main__":
    main()
