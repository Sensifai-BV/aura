"""Build the LoRA dataset of operator commands for the AURA intent model.

Synthetic transcripts in the style of the streaming ASR output (with and without
punctuation, fillers, wake words and typical recognition errors), plus side
conversation the robot must ignore. Writes chat-format JSONL for mlx_lm.lora:

    python generate_data.py --out data            # train.jsonl, valid.jsonl
The held-out test set (test.jsonl, hand written, different phrasing) is not generated.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aura_intent.schema import answer, messages  # noqa: E402

WAKE = ["", "", "", "", "robot", "artaban", "hey artaban", "hey robot", "okay robot", "artaban,", "robot,", "unit one"]
POLITE_PRE = ["", "", "", "", "please", "can you", "could you", "i need you to", "i want you to", "now", "okay", "alright"]
POLITE_POST = ["", "", "", "", "please", "now", "right now", "thanks", "for me", "okay"]
FILLERS = ["uh", "um", "er", "so", "well"]

PLACES = [
    "compressor bay", "pump two", "pump 3", "valve seven", "valve 12", "the separator", "tank farm", "tank b",
    "the flare stack", "control room door", "heat exchanger", "the gas detector", "manifold a", "pipe rack",
    "waypoint three", "waypoint 5", "wp 03", "wp-07", "the charging dock", "the north gate", "the left tank",
    "the red valve", "the pressure gauge", "the cooling tower", "the boiler", "generator two", "the stairs",
    "the leak", "that pipe", "the transformer", "loading bay", "inspection point four", "the scrubber",
    "the oil drum", "the fire extinguisher", "the sump pit", "the motor on the right", "the thermal hotspot",
]

TEMPLATES = {
    "stop": [
        "stop", "stop now", "stop stop", "stop stop stop", "halt", "emergency stop", "stop right there",
        "stop moving", "freeze", "don't move", "stop immediately", "hold it stop", "e-stop", "stop the robot",
        "full stop", "stop where you are", "kill the motion", "stand still", "stop walking", "whoa stop",
    ],
    "pause": [
        "pause", "pause the mission", "hold on", "wait", "wait a second", "hold position", "pause here",
        "hold there", "stay there", "wait here", "pause for a moment", "hang on", "hold your position",
        "give me a minute", "pause the inspection", "wait right there", "sit tight", "take a break there",
        "stay put", "hold up",
    ],
    "resume": [
        "resume", "resume the mission", "carry on", "continue", "keep going", "continue the mission",
        "go on", "you can continue", "resume inspection", "back to work", "continue where you left off",
        "start again", "proceed", "unpause", "resume the patrol", "continue your route", "pick up where you stopped",
        "okay keep going", "resume operation", "you can move again",
    ],
    "confirm": [
        "yes", "yes do it", "affirmative", "correct", "confirmed", "confirm", "that's right", "do it", "yep",
        "yeah", "go ahead", "yes please", "exactly", "that is correct", "sure", "right", "roger that", "yes go",
        "absolutely", "ok do it", "yes that's what i said", "positive",
    ],
    "cancel": [
        "cancel", "no", "never mind", "abort", "abort that", "negative", "don't do that", "scratch that",
        "cancel that", "no cancel", "not that", "nope", "forget it", "that's wrong", "no that's not what i said",
        "cancel the command", "undo", "abort the task", "no don't", "wrong", "ignore that",
    ],
    "next_step": [
        "next", "next step", "next point", "go to the next point", "move on", "next waypoint",
        "go to the next waypoint", "skip this one", "move to the next one", "continue to the next point",
        "next inspection point", "on to the next", "done here move on", "that's enough here next",
        "go to the next station", "proceed to the next checkpoint", "skip ahead", "next one",
    ],
    "target_select": [
        "go to {p}", "inspect {p}", "check {p}", "move to {p}", "walk to {p}", "head to {p}",
        "take a look at {p}", "look at {p}", "navigate to {p}", "go check {p}", "scan {p}",
        "go and inspect {p}", "select {p}", "target {p}", "focus on {p}", "get closer to {p}",
        "I want you at {p}", "show me {p}", "go over to {p}", "approach {p}", "measure {p}",
        "point the camera at {p}",
    ],
    "come_follow": [
        "follow me", "come here", "come with me", "come to me", "come over here", "follow", "stay behind me",
        "come back to me", "follow me please", "walk with me", "come", "this way", "come this way",
        "keep following me", "stay close to me", "track me", "follow my lead", "come closer", "over here",
        "come over", "return to me",
    ],
    "unknown": [
        "open the valve", "dance", "what's your battery level", "turn on the lights", "sing a song",
        "how hot is it", "tell me a joke", "pick up the box", "climb the ladder", "what time is it",
        "call the supervisor", "close the door", "jump", "take off", "turn left ninety degrees",
        "lift your leg", "what can you do", "fix the leak", "record a video", "send the report",
        "charge yourself", "speed up", "slow down", "turn around", "lie down", "sit", "bark",
    ],
}

# Side conversation, radio and narration: never commands, even with command words in them.
NONE_LINES = [
    "did you bring the gas detector", "we should stop for lunch soon", "i told him to stop smoking near the tank",
    "the pump stopped working yesterday", "can you pass me the wrench", "let's wait for the supervisor",
    "he said cancel the meeting", "i'll go to the compressor bay after this", "the robot looks fine today",
    "copy that control, we're at the north gate", "next week we inspect the boiler", "yes i saw the email",
    "no i don't think so", "follow the procedure in the manual", "the readings look normal",
    "check your mask seal", "what did the foreman say", "i'm going to grab a coffee", "it's really loud in here",
    "did you hear that alarm", "the shift ends at six", "come on man seriously", "turn the radio down",
    "who left the valve open", "we need more batteries", "my gloves are wet", "i think it's going to rain",
    "the maintenance team will come later", "tell john to follow us", "are you done with the clipboard",
    "pause the music", "i paused the video", "the next train is at five", "keep going like this and we finish early",
    "she will resume work on monday", "abort mission was a good movie", "the stop button on the pump is broken",
    "hey mike come here", "sarah follow me to the office", "yes boss", "okay see you tomorrow",
    "control room this is team two", "temperature is twenty five degrees", "go to the canteen without me",
    "that's the wrong form", "mm hmm", "uh huh", "thank you", "hello", "good morning everyone", "testing one two",
    "", "the", "and then", "oh", "hmm", "check", "noise", "[inaudible]",
]
NONE_PREFIX = ["", "", "", "hey mike", "john", "guys", "control", "sarah", "team two"]

# Commands with the reason said after them: the command still counts.
REASONS = [
    "watch out", "careful", "there's a worker ahead", "a person is crossing", "you're too close to the edge",
    "the floor is slippery", "gas alarm", "the alarm is going off", "something's wrong", "there's a forklift coming",
    "you're blocking the path", "that's the wrong way", "i need to check something", "we found a leak",
    "i'll be right back", "the readings look good", "we're done here", "i want to show you something",
    "it's too hot over there", "the path is clear now",
]
REASON_INTENTS = ("stop", "pause", "resume", "come_follow", "next_step", "target_select", "cancel")

# People addressed by name, plans and reported speech: command words that are not for the robot.
PEOPLE = ["mike", "john", "sarah", "ali", "maria", "tom", "boss", "guys", "team", "dave", "control"]
FRAMES = ["{n}, {c}", "{n} {c}", "{n} can you {c}", "hey {n}, {c}", "i think we should {c}", "let's {c} later",
          "he told me to {c}", "she asked us to {c}", "they want to {c} tomorrow", "did you {c} yesterday",
          "remember to {c} after lunch", "we always {c} on fridays", "the manual says {c}", "i was going to {c}"]
HUMAN_CMDS = ["stop by the office", "go to the canteen", "follow the red line", "check the gauge", "cancel the order",
              "pause the radio", "come to the meeting", "continue the paperwork", "inspect the ladder",
              "go to the north gate", "follow the procedure", "move to the next room", "come here for a second",
              "stop talking", "wait for the truck", "head to the parking lot", "look at the schedule",
              "resume the call", "skip the next one", "open the valve"]
ACKS = ["bye", "cheers", "see ya", "talk later", "uh", "ah", "hmm okay", "nice", "great job everyone",
        "thanks a lot", "good night", "excuse me", "sorry", "what", "oh no", "wow", "let me think",
        "one moment john", "hold on john", "yes mike", "no sarah", "copy that", "over and out"]

# Typical recognition errors of the ASR on these words.
ASR_SWAPS = {
    "pause": ["paws", "pose", "pores"], "stop": ["stopp", "stuck", "top"], "halt": ["hold", "alt"],
    "resume": ["re zoom", "resumed", "presume"], "cancel": ["cancer", "council", "can sell"],
    "follow": ["fallow", "hollow"], "next": ["necks", "nest"], "inspect": ["in spect", "expect"],
    "valve": ["valv", "vowel"], "artaban": ["arta ban", "art a ban", "ardaban"], "abort": ["a bort", "aboard"],
    "affirmative": ["a firmative"], "waypoint": ["way point", "weight point"], "compressor": ["compressed"],
}


def asr_noise(text, rng, keep=""):
    words = text.split()
    out = []
    for w in words:
        key = w.strip(",.?!").lower()
        if key in ASR_SWAPS and rng.random() < 0.12 and key not in keep.split():
            w = rng.choice(ASR_SWAPS[key])
        out.append(w)
    if len(out) > 2 and not keep and rng.random() < 0.05:
        out.pop(rng.randrange(1, len(out)))
    if rng.random() < 0.1:
        out.insert(rng.randrange(0, len(out) + 1), rng.choice(FILLERS))
    return " ".join(out)


def surface(text, rng):
    """Punctuation and casing of the ASR output varies with the model / latency setting."""
    text = re.sub(r"\s+,", ",", re.sub(r"\s+", " ", text)).strip(" ,")
    style = rng.random()
    if style < 0.45:
        return text.lower()
    text = text[:1].upper() + text[1:]
    if style < 0.85:
        return text + rng.choice([".", ".", "!", "?" if text.lower().startswith(("can", "could", "what", "how")) else "."])
    return text.upper() if style > 0.97 else text


def command(intent, rng):
    tpl = rng.choice(TEMPLATES[intent])
    target = ""
    if "{p}" in tpl:
        target = rng.choice(PLACES)
        tpl = tpl.replace("{p}", target)
    parts = []
    if intent not in ("confirm", "cancel") or rng.random() < 0.3:
        parts.append(rng.choice(WAKE))
    if intent not in ("stop", "confirm", "cancel"):
        parts.append(rng.choice(POLITE_PRE))
    parts.append(tpl)
    if intent not in ("stop",):
        parts.append(rng.choice(POLITE_POST))
    if intent in REASON_INTENTS and rng.random() < 0.18:
        reason = rng.choice(REASONS)
        parts = [reason + ","] + parts if rng.random() < 0.3 else parts + [", " + reason]
    if intent == "stop" and rng.random() < 0.15:
        parts.insert(0, tpl + ",")
    text = " ".join(p for p in parts if p)
    return text, target


def side_talk(rng):
    r = rng.random()
    if r < 0.4:
        return rng.choice(FRAMES).format(n=rng.choice(PEOPLE), c=rng.choice(HUMAN_CMDS))
    if r < 0.55:
        return rng.choice(ACKS)
    line = rng.choice(NONE_LINES)
    prefix = rng.choice(NONE_PREFIX)
    return f"{prefix} {line}" if prefix and line else line


def build(n_per_intent, n_none, seed):
    rng = random.Random(seed)
    rows, seen = [], set()

    def add(text, intent, target):
        text = surface(asr_noise(text, rng, target), rng)
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        rows.append({"messages": messages(text) + [{"role": "assistant", "content": answer(intent, target)}]})

    for intent in TEMPLATES:
        weight = 2 if intent == "target_select" else 1
        for _ in range(n_per_intent * weight * 3):
            if sum(1 for r in rows if f'"intent": "{intent}"' in r["messages"][-1]["content"]) >= n_per_intent * weight:
                break
            text, target = command(intent, rng)
            add(text, intent, target)
    for _ in range(n_none * 3):
        if sum(1 for r in rows if '"intent": "none"' in r["messages"][-1]["content"]) >= n_none:
            break
        add(side_talk(rng), "none", "")
    rng.shuffle(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).with_name("data")))
    ap.add_argument("--per-intent", type=int, default=320)
    ap.add_argument("--none", type=int, default=900)
    ap.add_argument("--valid", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rows = build(args.per_intent, args.none, args.seed)
    n_valid = int(len(rows) * args.valid)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, part in (("valid", rows[:n_valid]), ("train", rows[n_valid:])):
        with open(out / f"{name}.jsonl", "w") as f:
            for row in part:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = {}
    for r in rows:
        intent = json.loads(r["messages"][-1]["content"])["intent"]
        counts[intent] = counts.get(intent, 0) + 1
    print(f"{len(rows) - n_valid} train, {n_valid} valid -> {out}: {counts}")


if __name__ == "__main__":
    main()
