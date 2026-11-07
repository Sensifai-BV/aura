"""Guided voice test on the host: shows each command, you say it, timestamps are logged for scoring.

Phrases come from the intent model's fine-tuning templates (src/aura_intent/training/generate_data.py),
half of them with the wake word "Artaban". Side talk is published as intent unknown with decision IGNORE. Expected result assumes aura_intent confirm_mode=never
(one event per utterance); score with score_guided.py.

    python guided_commands.py [--window 10] [--out runs/guided.json]
"""
import argparse, json, time

# (phrase, expected intent, expected decision)
SET = [
    ("Artaban, stop", "stop", "ACCEPT"), ("halt", "stop", "ACCEPT"), ("emergency stop", "stop", "ACCEPT"),
    ("pause the mission", "pause", "ACCEPT"), ("Artaban, wait", "pause", "ACCEPT"), ("hold position", "pause", "ACCEPT"),
    ("resume the mission", "resume", "ACCEPT"), ("Artaban, carry on", "resume", "ACCEPT"),
    ("never mind", "cancel", "ACCEPT"), ("Artaban, cancel that", "cancel", "ACCEPT"),
    ("next waypoint", "next_step", "ACCEPT"), ("Artaban, go to the next point", "next_step", "ACCEPT"),
    ("follow me", "come_follow", "ACCEPT"), ("Artaban, come here", "come_follow", "ACCEPT"),
    ("inspect valve seven", "target_select", "ACCEPT"), ("Artaban, go to the compressor bay", "target_select", "ACCEPT"),
    ("check pump two", "target_select", "ACCEPT"),
    ("Artaban, open the valve", "unknown", "REJECT"), ("tell me a joke", "unknown", "REJECT"),
    ("we should stop for lunch soon", "unknown", "IGNORE"), ("hey mike come here", "unknown", "IGNORE"),
    ("the pump stopped working yesterday", "unknown", "IGNORE"),
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=float, default=10.0, help="seconds per phrase")
    ap.add_argument("--out", default="runs/guided.json")
    a = ap.parse_args()
    print(f"\n{len(SET)} phrases. Speak each one clearly when you see SAY. Starting in 5 s...\n", flush=True)
    time.sleep(5)
    log = []
    for i, (phrase, intent, decision) in enumerate(SET, 1):
        print(f"\n[{i}/{len(SET)}]  SAY:  \033[1m{phrase}\033[0m", flush=True)
        t0 = time.time()
        time.sleep(a.window)
        log.append({"n": i, "phrase": phrase, "expect_intent": intent, "expect_decision": decision,
                    "t0": t0, "t1": time.time()})
        json.dump(log, open(a.out, "w"), indent=1)
    print("\nDone. Thank you.", flush=True)
