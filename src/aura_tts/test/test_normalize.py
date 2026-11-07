import pytest

from aura_tts.normalize import normalize, number_words, sentences


@pytest.mark.parametrize("text,spoken", [
    # Tags misread in the A/B ("V one home four", "N twelve") are spelled letter by letter, digit by digit.
    ("Inspect valve V-104.", "Inspect valve vee one zero four."),
    ("Motor M-12 is hot.", "Motor em one two is hot."),
    ("Leak near flange F-3.", "Leak near flange eff three."),
    ("Pump P201 and sensor TT-3A.", "Pump pee two zero one and sensor tee tee three ay."),
    ("Thermal reading 84 °C.", "Thermal reading eighty four degrees Celsius."),
    ("Ambient -3.5°C.", "Ambient minus three point five degrees Celsius."),
    ("Methane at 20 %LEL.", "Methane at twenty percent of the lower explosive limit."),
    ("Pressure 4.5 bar.", "Pressure four point five bar."),
    ("Paused at 14:30, resumed 09:05, done 18:00.",
     "Paused at fourteen thirty, resumed nine oh five, done eighteen hundred."),
    ("Battery at 15%.", "Battery at fifteen percent."),
    ("Walked 1500 m.", "Walked one thousand five hundred metres."),
    ("Check the CCTV and STT.", "Check the see see tee vee and ess tee tee."),
    ("Gauge 007.", "Gauge zero zero seven."),
    ("Doors A & B.", "Doors A and B."),
])
def test_plant_text(text, spoken):
    assert normalize(text) == spoken


@pytest.mark.parametrize("text", [
    "Stop, executing.", "Did you say go to pump station three? Say yes or show thumb up.",
    "STOP. NO. OK.", "I am following you.",
])
def test_plain_text_unchanged(text):
    assert normalize(text) == text


def test_idempotent():
    once = normalize("Valve V-104 at 84 °C, 20 %LEL, 14:30.")
    assert normalize(once) == once


@pytest.mark.parametrize("n,words", [(0, "zero"), (13, "thirteen"), (40, "forty"), (101, "one hundred one"),
                                     (2026, "two thousand twenty six"), (1000000, "one million")])
def test_number_words(n, words):
    assert number_words(str(n)) == words


def test_sentences():
    assert sentences("Hazard zone ahead. Keep three metres away! OK?") == [
        "Hazard zone ahead.", "Keep three metres away!", "OK?"]
    assert sentences("Pressure four point five bar.") == ["Pressure four point five bar."]
    assert sentences("  ") == []
