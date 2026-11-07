"""Score a guided session: compares what STT heard and what intent decided with the expectation.

    python score_guided.py runs/guided.json runs/liveN.out
Needs the live runner's epoch-stamped output (run_integration.py --live).
"""
import json, re, sys

def norm(t):
    t = re.sub(r"[^a-z0-9 ]", "", t.lower())
    t = re.sub(r"^(hey )?(artaban|robot|unit one) ?", "", t)
    return " ".join(t.split())

def main(guided_path, out_path):
    guided = json.load(open(guided_path))
    rows = []
    for line in open(out_path, errors="replace"):
        m = re.match(r"\s*(\d+\.\d+) \s*([\d.]+) (transcript|event)\s+(\{.*)", line)
        if not m:
            continue
        try:
            d = json.loads(m.group(4))
        except Exception:
            # live output truncates long lines; recover the fields we need
            d = {k: v for k, v in re.findall(r'"(utterance|intent|decision|text)": "([^"]*)"', m.group(4))}
        rows.append((float(m.group(1)), m.group(3), d))
    slack = 1.0
    ok = stt_ok = 0
    print(f"{'#':>2}  {'said':36} {'STT heard':36} {'intent':14} {'decision':8}  result")
    for g in guided:
        win = [r for r in rows if g["t0"] - slack <= r[0] <= g["t1"] + slack]
        heard = " / ".join(d.get("text", "") for _, k, d in win if k == "transcript") or "(nothing)"
        ev = [d for _, k, d in win if k == "event" and d.get("utterance", None) is not None and d.get("intent") != "confirm"]
        ev = ev[0] if ev else {}
        intent, dec = ev.get("intent", "-"), ev.get("decision", "-")
        s_ok = norm(heard) == norm(g["phrase"])
        i_ok = intent == g["expect_intent"] and dec == g["expect_decision"]
        stt_ok += s_ok
        ok += i_ok
        print(f"{g['n']:>2}  {g['phrase'][:36]:36} {heard[:36]:36} {intent:14} {dec:8}  "
              f"{'PASS' if i_ok else 'FAIL'}{'' if s_ok else '  (STT differs)'}")
    n = len(guided)
    print(f"\nintent+decision correct: {ok}/{n}   STT exact (ignoring wake word/case/punctuation): {stt_ok}/{n}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
