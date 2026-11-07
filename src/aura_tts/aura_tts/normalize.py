"""Speaker text normalisation: turn plant text into words the TTS model reads correctly.
"""

import re

LETTERS = {
    "A": "ay", "B": "bee", "C": "see", "D": "dee", "E": "ee", "F": "eff", "G": "gee", "H": "aitch",
    "I": "eye", "J": "jay", "K": "kay", "L": "el", "M": "em", "N": "en", "O": "oh", "P": "pee",
    "Q": "cue", "R": "ar", "S": "ess", "T": "tee", "U": "you", "V": "vee", "W": "double you",
    "X": "ex", "Y": "why", "Z": "zee",
}
DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
ONES = DIGITS + ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
                 "eighteen", "nineteen")
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

UNITS = {
    "°C": "degrees Celsius", "°F": "degrees Fahrenheit", "%LEL": "percent of the lower explosive limit",
    "% LEL": "percent of the lower explosive limit", "%": "percent", "ppm": "parts per million",
    "dB(A)": "decibels", "dBA": "decibels", "dB": "decibels", "bar": "bar", "kPa": "kilopascals",
    "km/h": "kilometres per hour", "m/s": "metres per second", "mm": "millimetres", "cm": "centimetres",
    "m": "metres", "km": "kilometres", "s": "seconds", "min": "minutes", "h": "hours",
    "kg": "kilograms", "kW": "kilowatts", "Hz": "hertz",
}
SYMBOLS = {"&": " and ", "+": " plus ", "=": " equals ", "/": " slash ", "#": " number ", "@": " at ",
           "*": " ", "_": " ", "~": " about "}

# A tag is letters joined to digits, with an optional hyphen: V-104, P201, TT-3A, F-3.
TAG = re.compile(r"\b([A-Z]{1,4})-?(\d+)([A-Z]?)\b")
NUMBER_UNIT = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?)\s?(" + "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
    + r")(?![\w])")
CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.]\d)")
# Vowel-less capitals (STT, PPM, CCTV) cannot be read as words, so they are spelled.
# Capitals with vowels (STOP, LEL, NO) are left to the model.
ACRONYM = re.compile(r"\b[B-DF-HJ-NP-TV-XZ]{2,5}\b")


def integer_words(n):
    if n < 0:
        return "minus " + integer_words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + integer_words(rest))
    for size, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            return integer_words(n // size) + " " + name + ("" if rest == 0 else " " + integer_words(rest))
    raise AssertionError(n)


def number_words(text):
    """'4.5' -> 'four point five', '-3' -> 'minus three', '007' -> 'zero zero seven'."""
    sign = "minus " if text.startswith("-") else ""
    whole, _, frac = text.lstrip("-").partition(".")
    if len(whole) > 1 and whole.startswith("0"):
        spoken = " ".join(DIGITS[int(d)] for d in whole)
    else:
        spoken = integer_words(int(whole))
    if frac:
        spoken += " point " + " ".join(DIGITS[int(d)] for d in frac)
    return sign + spoken


def spell_tag(match):
    letters, digits, suffix = match.groups()
    parts = [LETTERS[c] for c in letters] + [DIGITS[int(d)] for d in digits]
    if suffix:
        parts.append(LETTERS[suffix])
    return " ".join(parts)


def clock_words(match):
    hour, minute = int(match[1]), int(match[2])
    if minute == 0:
        return f"{integer_words(hour)} hundred"
    return f"{integer_words(hour)} {'oh ' if minute < 10 else ''}{integer_words(minute)}"


def normalize(text):
    """Plant text -> plain words for synthesis. Idempotent on already-plain text."""
    text = " ".join(text.split())
    text = CLOCK.sub(clock_words, text)
    text = TAG.sub(spell_tag, text)
    text = NUMBER_UNIT.sub(lambda m: f"{number_words(m[1])} {UNITS[m[2]]}", text)
    text = NUMBER.sub(lambda m: number_words(m[0]), text)
    text = ACRONYM.sub(lambda m: " ".join(LETTERS[c] for c in m[0]), text)
    for symbol, spoken in SYMBOLS.items():
        text = text.replace(symbol, spoken)
    return " ".join(text.split())


SENTENCE = re.compile(r"(?<=[.!?])\s+(?=\S)")


def sentences(text):
    """Split for streaming: the first sentence plays while the next is synthesised."""
    return [s for s in SENTENCE.split(text.strip()) if s]
