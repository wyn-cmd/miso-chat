# Tests for the core logic in rp_core.py and web/quench.py: the token and
# request limits, the window and clip boundary math, clipping, and quenching.
# Run from the project folder: python -m unittest discover -s tests
# These only touch temp files and never call a model.

import json
import os
import shutil
import tempfile
import unittest
import datetime

try:
    import rp_core
    from web import quench
    IMPORT_ERROR = None
except ImportError as e:
    rp_core = None
    quench = None
    IMPORT_ERROR = str(e)


def engine(tmp, **kw):
    # An Engine against empty temp folders. It reads no character, memory or
    # dream files that do not exist, and its client is never called here.
    ai = os.path.join(tmp, "char")
    state = os.path.join(tmp, "state")
    os.makedirs(ai, exist_ok=True)
    os.makedirs(state, exist_ok=True)
    return rp_core.Engine(client=None, ai_dir=ai, state_dir=state, **kw)


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class TokenLimits(unittest.TestCase):
    # The per-day token tracker and request bar, against temp files.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCallback = self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "tokens.json")
        self.today = datetime.datetime.now().strftime("%d/%m/%y")

    def write_tracker(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_fresh_tracker_counts_turns_and_returns_the_limit(self):
        used, limit, _, recent = rp_core.check_and_update_tokens(100, path=self.path)
        self.assertEqual(used, 100)
        self.assertEqual(limit, rp_core.TOKEN_LIMIT)
        self.assertEqual(recent, [100])
        used, _, _, _ = rp_core.check_and_update_tokens(50, path=self.path)
        self.assertEqual(used, 150)

    def test_only_positive_integers_count(self):
        for bad in (0, -5, "?", None, 10.5):
            rp_core.check_and_update_tokens(bad, path=self.path)
        used, _, _, recent = rp_core.check_and_update_tokens(0, path=self.path)
        self.assertEqual(used, 0)
        self.assertEqual(recent, [])

    def test_recent_turns_keeps_five(self):
        for _ in range(7):
            rp_core.check_and_update_tokens(10, path=self.path)
        used, _, _, recent = rp_core.check_and_update_tokens(0, path=self.path)
        self.assertEqual(used, 70)
        self.assertEqual(recent, [10] * 5)

    def test_an_old_date_resets_the_day(self):
        self.write_tracker({"date": "01/01/00", "tokens_used": 500, "recent_turns": [500]})
        used, _, _, _ = rp_core.check_and_update_tokens(10, path=self.path)
        self.assertEqual(used, 10)

    def test_a_broken_date_resets_the_day(self):
        self.write_tracker({"date": "not-a-date", "tokens_used": 999, "recent_turns": [999]})
        used, _, _, _ = rp_core.check_and_update_tokens(10, path=self.path)
        self.assertEqual(used, 10)

    def test_today_does_not_reset(self):
        self.write_tracker({"date": self.today, "tokens_used": 400, "recent_turns": [400]})
        used, _, _, _ = rp_core.check_and_update_tokens(10, path=self.path)
        self.assertEqual(used, 410)

    def test_a_file_without_a_limit_keeps_the_default(self):
        self.write_tracker({"date": self.today, "tokens_used": 5})
        _, limit, _, _ = rp_core.check_and_update_tokens(0, path=self.path)
        self.assertEqual(limit, rp_core.TOKEN_LIMIT)

    def test_estimate_for_one_turn_floors_at_100_tokens(self):
        _, _, est, _ = rp_core.check_and_update_tokens(1, path=self.path)
        left = rp_core.TOKEN_LIMIT - 1
        self.assertEqual(est, left // 100)

    def test_estimate_for_two_flat_turns_uses_the_average(self):
        rp_core.check_and_update_tokens(100, path=self.path)
        _, _, est, _ = rp_core.check_and_update_tokens(100, path=self.path)
        left = rp_core.TOKEN_LIMIT - 200
        self.assertEqual(est, left // 100)

    def test_estimate_for_a_rising_trend_predicts_the_next_turn(self):
        rp_core.check_and_update_tokens(100, path=self.path)
        _, _, est, _ = rp_core.check_and_update_tokens(200, path=self.path)
        left = rp_core.TOKEN_LIMIT - 300
        # The ratio 2.0 is above 1.1, so the next turn is predicted at 400 and the average cost is 300.
        self.assertEqual(est, left // 300)

    def test_no_turns_leaves_the_estimate_unknown(self):
        _, _, est, _ = rp_core.check_and_update_tokens(0, path=self.path)
        self.assertEqual(est, "?")


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class RequestBar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "requests.json")

    def test_requests_count_up(self):
        self.assertEqual(rp_core.check_and_update_requests(path=self.path), 1)
        self.assertEqual(rp_core.check_and_update_requests(path=self.path), 2)

    def test_an_old_date_resets_the_count(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"date": "01/01/00", "requests_made": 999}, f)
        self.assertEqual(rp_core.check_and_update_requests(path=self.path), 1)

    def test_bar_shows_the_count(self):
        rp_core.check_and_update_requests(path=self.path)
        rp_core.check_and_update_requests(path=self.path)
        bar = rp_core.get_request_bar(path=self.path)
        self.assertIn("(2/%d)" % rp_core.REQUEST_LIMIT, bar)
        self.assertEqual(len(bar[1:21]), 20)

    def test_bar_caps_at_the_limit(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"date": datetime.datetime.now(pytz_ok()).strftime("%d/%m/%y"),
                       "requests_made": rp_core.REQUEST_LIMIT * 2}, f)
        bar = rp_core.get_request_bar(path=self.path)
        self.assertIn("100.0%", bar)
        self.assertEqual(len(bar[1:21]), 20)


def pytz_ok():
    # The request tracker counts by the Pacific day, unlike the token tracker.
    import pytz
    return pytz.timezone("US/Pacific")


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class WindowAndClipMath(unittest.TestCase):
    # The pure position math: the clip boundary, the window start and the summary cut.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.e = engine(self.tmp)

    def test_clip_boundary_moves_in_steps(self):
        self.assertEqual([self.e.clip_cut(t) for t in (0, 8, 17)], [0, 0, 0])
        self.assertEqual([self.e.clip_cut(t) for t in (18, 27)], [10, 10])
        self.assertEqual([self.e.clip_cut(t) for t in (28, 37)], [20, 20])

    def test_clip_boundary_is_stable_within_a_step(self):
        self.assertEqual(len({self.e.clip_cut(t) for t in range(28, 38)}), 1)

    def test_window_start_sits_steps_behind_the_boundary(self):
        # Solo default 25: back = max(1, round((25 - 8) / 10)) = 2, so 20 behind.
        self.assertEqual(self.e.window_start(100), 70)
        # A small chat keeps everything: the floor is 0.
        self.assertEqual(self.e.window_start(25), 0)

    def test_window_start_honours_the_override(self):
        self.e.window_override = 60
        self.assertEqual(self.e.window_start(100), 40)

    def test_summary_cut_follows_the_clip_switch(self):
        self.assertEqual(self.e.summary_cut(100), self.e.clip_cut(100))
        self.e.clip_enabled = False
        self.assertEqual(self.e.summary_cut(100), self.e.window_start(100))


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class Clipping(unittest.TestCase):
    # Older window messages are cut to about CLIP_CHARS at a word break.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.e = engine(self.tmp)

    def test_only_messages_before_the_boundary_are_cut(self):
        long = "a" * 1000
        texts = [long] * 30
        out = self.e._clip_older(texts, 0, 30)
        # Boundary for a chat of 30 is 20: messages 0-19 cut, 20-29 whole.
        self.assertTrue(out[0].endswith(" ..."))
        self.assertLessEqual(len(out[0]), rp_core.CLIP_CHARS + 4)
        self.assertEqual(out[20], long)
        self.assertEqual(out[29], long)

    def test_cut_prefers_a_word_break_late_in_the_message(self):
        text = "a" * 500 + " " + "b" * 500
        out = self.e._clip_older([text], 0, 30)
        self.assertEqual(out[0], "a" * 500 + " ...")

    def test_cut_without_a_late_word_break_keeps_the_chars(self):
        text = "a" * 1000
        out = self.e._clip_older([text], 0, 30)
        self.assertEqual(out[0], "a" * rp_core.CLIP_CHARS + " ...")

    def test_short_messages_stay_whole(self):
        out = self.e._clip_older(["short"] * 30, 0, 30)
        self.assertEqual(out[0], "short")

    def test_clipping_off_returns_everything_whole(self):
        self.e.clip_enabled = False
        text = "a" * 1000
        out = self.e._clip_older([text] * 30, 0, 30)
        self.assertEqual(out[0], text)

    def test_the_position_is_respected(self):
        # first=25 puts every message at or after the boundary of 20, so nothing is cut.
        text = "a" * 1000
        out = self.e._clip_older([text, text], 25, 30)
        self.assertEqual(out, [text, text])


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class WindowParts(unittest.TestCase):
    # The window as the model sees it, with quenching off and clipping on.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.e = engine(self.tmp)

    def test_leading_replies_are_dropped(self):
        # A chat can begin on a reply once the user's message is deleted; the
        # prompt must start on a user message.
        self.e.full_history = [
            {"role": "model", "text": "early one"},
            {"role": "model", "text": "early two"},
            {"role": "user", "text": "hi"},
            {"role": "model", "text": "hey"},
            {"role": "user", "text": "again"},
        ]
        parts = self.e._window_parts(rp_core.SOLO_WINDOW, labelled=False, total=5)
        self.assertEqual(parts["start"], 2)
        self.assertEqual(parts["formatted"], [
            ("user", "hi"), ("model", "hey"), ("user", "again")])

    def test_labels_use_the_speaker(self):
        self.e.full_history = [{"role": "user", "text": "hi", "speaker": "Miso"}]
        parts = self.e._window_parts(rp_core.SOLO_WINDOW, labelled=True, total=1)
        self.assertEqual(parts["formatted"], [("user", "[Miso]: hi")])

    def test_older_messages_are_clipped_and_the_saving_is_counted(self):
        long = "a" * 1000
        self.e.full_history = [{"role": "user" if i % 2 == 0 else "model", "text": long}
                               for i in range(30)]
        parts = self.e._window_parts(rp_core.SOLO_WINDOW, labelled=False, total=30)
        clipped = [text for _, text in parts["formatted"]]
        self.assertTrue(clipped[0].endswith(" ..."))
        self.assertEqual(clipped[20], long)
        saved_chars = sum(len(long) - len(t) for t in clipped)
        self.assertEqual(parts["clip_saved"], (saved_chars + 3) // 4)


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class QuenchOff(unittest.TestCase):
    # With quenching off, nothing changes and there is no report.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.e = engine(self.tmp)

    def test_quench_returns_texts_and_no_report(self):
        texts, report = self.e._quench(["hi", "there"], ["user", "model"])
        self.assertEqual(texts, ["hi", "there"])
        self.assertIsNone(report)

    def test_quench_window_returns_texts_and_no_report(self):
        texts, report = self.e._quench_window(["hi", "there"], ["user", "model"], 0, 30)
        self.assertEqual(texts, ["hi", "there"])
        self.assertIsNone(report)


@unittest.skipUnless(rp_core, "rp_core imports need pytz and google-genai: " + str(IMPORT_ERROR))
class QuenchWindow(unittest.TestCase):
    # The engine's quenching of a window: the older part is gated and memoised.

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.e = engine(self.tmp)
        self.e.quench_enabled = True

    def raw_texts(self, n):
        return ["turn %d of the talk" % i for i in range(n)]

    def test_memo_reuses_the_older_result(self):
        raw, roles = self.raw_texts(24), ["model"] * 24
        _, report = self.e._quench_window(raw, roles, 0, 30)
        again_raw, again_report = self.e._quench_window(raw, roles, 0, 30)
        self.assertIsNotNone(report)
        self.assertIs(report, again_report)

    def test_a_changed_older_message_recomputes(self):
        raw, roles = self.raw_texts(24), ["model"] * 24
        _, report = self.e._quench_window(raw, roles, 0, 30)
        raw2 = list(raw)
        raw2[0] += " tail"
        _, report2 = self.e._quench_window(raw2, roles, 0, 30)
        self.assertIsNot(report2, report)

    def test_user_messages_pass_through_unharmed(self):
        # The account's own wording must survive stage 2 word for word.
        raw = self.raw_texts(24)
        roles = ["model"] * 24
        raw[3] = "my own wording for the scene stays as it is, all of it"
        roles[3] = "user"
        texts, _ = self.e._quench_window(raw, roles, 0, 30)
        self.assertEqual(texts[3], raw[3])

    def test_newer_messages_get_stage_one_only(self):
        # Messages from the clip boundary on are outside the memo key, so the
        # same older part with newer messages changed keeps the memo.
        raw, roles = self.raw_texts(24), ["model"] * 24
        _, report = self.e._quench_window(raw, roles, 0, 30)
        raw2 = list(raw)
        raw2[22] += " tail"
        _, report2 = self.e._quench_window(raw2, roles, 0, 30)
        self.assertIs(report2, report)


@unittest.skipUnless(quench, "quench imports come with rp_core: " + str(IMPORT_ERROR))
class Strip(unittest.TestCase):
    # Stage 1: whitespace and decoration only, never a word.

    def strip(self, text):
        return quench.strip(text)

    def test_zero_width_characters_go(self):
        self.assertEqual(self.strip("a\u200bb\u200cc\u2060d\ufeffe"), "abcde")

    def test_nbsp_becomes_a_space(self):
        self.assertEqual(self.strip("a\u00a0b"), "a b")

    def test_html_comments_go(self):
        self.assertEqual(self.strip("keep<!--hidden-->this"), "keepthis")

    def test_divider_lines_go(self):
        for mark in ("---", "***", "___", "~~~", "..."):
            self.assertEqual(self.strip("one\n%s\n" % mark), "one")

    def test_space_runs_collapse(self):
        self.assertEqual(self.strip("a   b\t\tc  "), "a b c")

    def test_blank_line_runs_collapse(self):
        self.assertEqual(self.strip("a\n\n\n\n\nb"), "a\n\nb")

    def test_every_word_survives(self):
        text = "one  two\tthree\n\nfour   five"
        self.assertEqual(self.strip(text).split(), text.split())


@unittest.skipUnless(quench, "quench imports come with rp_core: " + str(IMPORT_ERROR))
class Gate(unittest.TestCase):
    # Stage 2: the entropy gate and its fidelity floor.

    # A sentence with more than MIN_WORDS content words, no anchors (lowercase,
    # no figures, no quotes), and wording that repeats across the window, so a
    # removal keeps the floor.
    FILLER = "the tall trees stood along the quiet walls beside the old stone bridge"
    FILLER_MESSAGE = FILLER + ". and the road went on."
    KEEP_MESSAGE = "the tall trees stood along the quiet walls beside the old stone bridge today."

    def window(self):
        return [self.FILLER_MESSAGE, self.KEEP_MESSAGE, self.KEEP_MESSAGE,
                self.KEEP_MESSAGE, self.KEEP_MESSAGE, self.KEEP_MESSAGE]

    def test_a_short_window_is_left_alone(self):
        texts = ["hello there. and again.", "word after word", "x", "y"]
        out, report = quench.gate(texts)
        self.assertEqual(out, texts)
        self.assertEqual(report["removed"], 0)
        self.assertEqual(report["fidelity"], 1.0)

    def test_filler_sentences_in_older_messages_can_go(self):
        texts = self.window()
        out, report = quench.gate(texts)
        self.assertGreaterEqual(report["removed"], 1)
        self.assertLess(len(out[0]), len(texts[0]))
        self.assertGreaterEqual(report["fidelity"], quench.FLOOR)

    def test_protected_messages_keep_every_sentence(self):
        texts = self.window()
        protect = [True] * len(texts)
        out, report = quench.gate(texts, protect=protect)
        self.assertEqual(out, texts)
        self.assertEqual(report["removed"], 0)

    def test_anchors_survive_any_removal(self):
        texts = [m + " I paid 12.50 for it, said \"hold the line\" at 5pm." for m in self.window()]
        out, report = quench.gate(texts)
        for anchor in ("12.50", "5pm", '"hold the line"'):
            self.assertIn(anchor, "\n".join(out))
        self.assertGreaterEqual(report["fidelity"], quench.FLOOR)

    def test_the_removal_budget_holds(self):
        texts = self.window()
        out, report = quench.gate(texts)
        before = sum(len(t) for t in texts)
        after = sum(len(t) for t in out)
        self.assertGreaterEqual(after, before * (1.0 - quench.MAX_DROP))

    def test_a_protect_list_shorter_than_the_window_is_padded(self):
        texts = self.window()
        out, _ = quench.gate(texts, protect=[True])
        self.assertEqual(len(out), len(texts))

    def test_an_empty_window_returns_nothing_to_report(self):
        out, report = quench.gate(["", "", "   "])
        self.assertEqual(out, ["", "", "   "])
        self.assertEqual(report["removed"], 0)


if __name__ == "__main__":
    unittest.main()
