import unittest

from aura_stt.audio import EchoGate
from aura_stt.echo import EchoFilter, RobotSpeech, strip_echo, words


def setup(*sentences):
    """sentences: (said_at, playback start, playback end, text)."""
    gate, speech = EchoGate(tail_s=0.4), RobotSpeech()
    for said, start, end, text in sentences:
        speech.said(text, said)
        gate.speaking(True, now=start)
        gate.speaking(False, now=end)
    return EchoFilter(gate, speech)


class StripEchoTests(unittest.TestCase):
    def test_each_robot_word_removes_one_transcript_word(self):
        self.assertEqual(strip_echo(words("stop stop"), words("Stop, executing.")), ["stop"])

    def test_misrecognised_robot_words_count_as_echo(self):
        self.assertEqual(strip_echo(words("confirm come fallow"), words("Confirm come follow?")), [])

    def test_operator_word_order_is_kept(self):
        self.assertEqual(strip_echo(words("artaban confirm come follow stop now"), words("Confirm come follow?")),
                         ["artaban", "stop", "now"])


class EchoFilterTests(unittest.TestCase):
    def test_robot_hearing_itself_is_dropped(self):
        f = setup((10.0, 10.0, 11.7, "Stop, executing."))
        self.assertEqual(f.clean("Stop executing", 10.1, 12.0), ("", 2))

    def test_stop_during_a_question_is_forwarded(self):
        f = setup((10.0, 10.4, 11.8, "Confirm come follow?"))
        self.assertEqual(f.clean("Confirm come follow stop", 10.3, 12.6), ("stop", 3))

    def test_a_single_leftover_word_needs_to_be_a_command_word(self):
        f = setup((10.0, 10.4, 11.8, "Confirm come follow?"))
        self.assertEqual(f.clean("Confirm come fellow there", 10.3, 12.6)[0], "")
        self.assertEqual(f.clean("Confirm come follow yes", 10.3, 12.6)[0], "yes")

    def test_no_robot_speech_leaves_the_transcript_alone(self):
        f = setup((10.0, 10.0, 11.7, "Stop, executing."))
        self.assertEqual(f.clean("Stop the mission", 20.0, 22.0), ("Stop the mission", 0))

    def test_unrelated_sound_during_robot_speech_is_kept(self):
        f = setup((10.0, 10.0, 11.7, "Pause, executing."))
        self.assertEqual(f.clean("Low battery Bluetooth mode", 10.5, 12.0), ("Low battery Bluetooth mode", 0))

    def test_a_stop_said_earlier_by_the_robot_does_not_eat_the_operators_stop(self):
        f = setup((0.0, 0.0, 1.7, "Stop, executing."), (10.0, 10.4, 11.8, "Confirm resume?"))
        self.assertEqual(f.clean("Confirm resume stop", 10.3, 12.6)[0], "stop")

    def test_pause_over_resume_executing_is_forwarded(self):
        f = setup((10.0, 10.0, 11.7, "Resume, executing."))
        self.assertEqual(f.clean("Resume pause executing", 10.0, 12.5)[0], "pause")

    def test_early_safety_from_a_partial(self):
        gate, speech = EchoGate(tail_s=0.4), RobotSpeech()
        speech.said("Confirm next step?", 10.0)
        gate.speaking(True, now=10.3)  # still talking
        f = EchoFilter(gate, speech)
        self.assertEqual(f.early_safety("Confirm next", 10.2, 11.0), "")
        self.assertEqual(f.early_safety("Confirm next step stop", 10.2, 11.2), "stop")

    def test_early_safety_ignores_the_robots_own_stop(self):
        gate, speech = EchoGate(tail_s=0.4), RobotSpeech()
        speech.said("Stop, executing.", 10.0)
        gate.speaking(True, now=10.0)
        self.assertEqual(EchoFilter(gate, speech).early_safety("Stop", 10.1, 10.6), "")

    def test_early_safety_needs_robot_speech(self):
        f = setup((10.0, 10.0, 11.7, "Stop, executing."))
        self.assertEqual(f.early_safety("stop", 30.0, 31.0), "")


if __name__ == "__main__":
    unittest.main()
