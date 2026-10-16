import logging
import random

import pytest

from app.application.drift import (
    DriftTracker,
    PromptDrift,
    length_bucket,
    observe_safely,
    psi,
)
from app.domain.models import ChatMessage, ChatRequest
from tests.fakes import RecordingMetrics, RecordingObserver


def obs(length="0-49", language="en") -> dict:
    return {"length": length, "language": language}


class MemoryStore:
    def __init__(self, saved=None, save_error: Exception | None = None) -> None:
        self.saved = saved
        self.save_error = save_error
        self.save_calls = 0

    def load(self):
        return self.saved

    def save(self, reference) -> None:
        self.save_calls += 1
        if self.save_error:
            raise self.save_error
        self.saved = reference


class TestLengthBucket:
    @pytest.mark.parametrize("chars, label", [
        (0, "0-49"), (49, "0-49"), (50, "50-149"), (149, "50-149"), (150, "150-499"),
        (499, "150-499"), (500, "500-1999"), (1999, "500-1999"), (2000, "2000+"),
        (100_000, "2000+"),
    ])
    def test_boundaries(self, chars, label):
        assert length_bucket(chars) == label


class TestPsi:
    def test_identical_distributions_score_zero(self):
        assert psi({"a": 30, "b": 70}, {"a": 30, "b": 70}) == pytest.approx(0.0)

    def test_nothing_to_compare_scores_zero(self):
        assert psi({}, {}) == 0.0

    def test_only_the_proportions_matter_not_the_sample_sizes(self):
        assert psi({"a": 30, "b": 70}, {"a": 300, "b": 700}) < 0.01

    def test_the_score_is_symmetric(self):
        one, other = {"a": 80, "b": 20}, {"a": 40, "b": 60}

        assert psi(one, other) == pytest.approx(psi(other, one))

    def test_a_bigger_change_scores_higher(self):
        base = {"a": 50, "b": 50}

        assert psi(base, {"a": 55, "b": 45}) < psi(base, {"a": 70, "b": 30}) \
            < psi(base, {"a": 95, "b": 5})

    def test_a_category_never_seen_before_gives_a_large_but_finite_score(self):
        score = psi({"en": 100}, {"fr": 100})

        assert score > 1
        assert score < 100  # smoothing keeps it finite instead of dividing by zero

    def test_a_category_that_disappears_is_noticed_too(self):
        assert psi({"en": 50, "es": 50}, {"en": 100}) > 0.25


class TestTrackerLifecycle:
    def tracker(self, **overrides) -> DriftTracker:
        settings = dict(reference_size=10, window_size=10, min_samples=10, threshold=0.25)
        return DriftTracker(**{**settings, **overrides})

    def test_while_learning_the_reference_nothing_is_scored(self):
        tracker = self.tracker()
        for _ in range(4):
            tracker.observe(obs())

        snap = tracker.snapshot()

        assert (snap.ready, snap.reference_samples, snap.window_samples) == (False, 4, 0)
        assert snap.scores == {}

    def test_the_window_starts_filling_only_after_the_reference_is_complete(self):
        tracker = self.tracker()
        for _ in range(10):
            tracker.observe(obs())

        assert tracker.snapshot().window_samples == 0  # the 10th prompt completed the reference

        tracker.observe(obs())

        assert tracker.snapshot().window_samples == 1

    def test_scores_appear_once_the_window_has_enough_prompts(self):
        tracker = self.tracker(min_samples=6)
        for _ in range(10):
            tracker.observe(obs())
        for _ in range(5):
            tracker.observe(obs())
        assert tracker.snapshot().ready is False

        tracker.observe(obs())
        snap = tracker.snapshot()

        assert snap.ready is True
        assert set(snap.scores) == {"length", "language"}

    def test_the_same_traffic_scores_near_zero(self):
        tracker = self.tracker()
        for _ in range(20):
            tracker.observe(obs("50-149", "en"))

        assert all(score < 0.05 for score in tracker.snapshot().scores.values())

    def test_a_change_in_one_feature_shows_in_that_feature_only(self):
        tracker = self.tracker()
        for _ in range(10):
            tracker.observe(obs("50-149", "en"))
        for _ in range(10):
            tracker.observe(obs("2000+", "en"))  # much longer prompts, same language

        scores = tracker.snapshot().scores

        assert scores["length"] > 0.25
        assert scores["language"] < 0.05

    def test_a_new_language_is_noticed(self):
        tracker = self.tracker()
        for _ in range(10):
            tracker.observe(obs("50-149", "en"))
        for _ in range(10):
            tracker.observe(obs("50-149", "es"))

        scores = tracker.snapshot().scores

        assert scores["language"] > 0.25
        assert scores["length"] < 0.05

    def test_the_window_slides_and_old_prompts_stop_counting(self):
        tracker = self.tracker()
        for _ in range(10):
            tracker.observe(obs("50-149", "en"))
        for _ in range(10):
            tracker.observe(obs("2000+", "es"))
        assert tracker.snapshot().scores["length"] > 0.25

        for _ in range(10):  # traffic goes back to normal and pushes the odd prompts out
            tracker.observe(obs("50-149", "en"))
        snap = tracker.snapshot()

        assert snap.window_samples == 10  # never more than the window size
        assert all(score < 0.05 for score in snap.scores.values())

    def test_the_threshold_is_reported(self):
        assert self.tracker(threshold=0.3).snapshot().threshold == 0.3

    @pytest.mark.parametrize("overrides", [
        {"reference_size": 0}, {"window_size": 0}, {"min_samples": 0},
        {"min_samples": 11}, {"threshold": 0}, {"threshold": -1},
    ])
    def test_invalid_settings_are_rejected(self, overrides):
        with pytest.raises(ValueError):
            self.tracker(**overrides)


class TestReferenceStore:
    GOOD = {"length": {"50-149": 70, "150-499": 30}, "language": {"en": 100}}

    def test_a_saved_reference_is_used_without_learning_a_new_one(self):
        store = MemoryStore(self.GOOD)
        tracker = DriftTracker(100, 5, 5, store=store)

        for _ in range(5):
            tracker.observe(obs("50-149", "en"))
        snap = tracker.snapshot()

        assert snap.ready is True  # the very first prompts already went into the window
        assert snap.reference_samples == 100
        assert store.save_calls == 0

    def test_a_learned_reference_is_saved_once_when_complete(self):
        store = MemoryStore()
        tracker = DriftTracker(4, 5, 5, store=store)
        for _ in range(3):
            tracker.observe(obs("50-149", "en"))
        assert store.save_calls == 0

        tracker.observe(obs("150-499", "es"))
        for _ in range(10):
            tracker.observe(obs())

        assert store.save_calls == 1
        assert store.saved == {"length": {"50-149": 3, "150-499": 1},
                               "language": {"en": 3, "es": 1}}

    def test_failing_to_save_does_not_break_the_tracker(self, caplog):
        store = MemoryStore(save_error=OSError("disk full"))
        tracker = DriftTracker(2, 2, 2, store=store)

        with caplog.at_level(logging.WARNING, logger="tokenscope.drift"):
            for _ in range(6):
                tracker.observe(obs())

        assert tracker.snapshot().ready is True
        assert "could not save" in caplog.text

    @pytest.mark.parametrize("saved", [
        {"length": {"0-49": 5}},                                   # no language
        {"length": {"0-49": 5}, "language": {}},                   # empty
        {"length": {"0-49": 0}, "language": {"en": 1}},            # no samples at all
        {"length": {"0-49": -1, "50-149": 9}, "language": {"en": 1}},
        {"length": {"0-49": 1.5}, "language": {"en": 1}},
        {"length": {"0-49": True}, "language": {"en": 1}},
        {"length": ["not", "a", "dict"], "language": {"en": 1}},
    ])
    def test_a_broken_saved_reference_stops_the_start_instead_of_being_trusted(self, saved):
        with pytest.raises(ValueError):
            DriftTracker(10, 10, 10, store=MemoryStore(saved))


class TestStatistics:
    """Measures what the default settings really do, with a fixed seed so it never flaps."""

    LENGTHS = ["0-49", "50-149", "150-499", "500-1999", "2000+"]
    NORMAL_LENGTH = [.30, .35, .20, .10, .05]
    LONGER_LENGTH = [.05, .10, .20, .35, .30]
    NORMAL_LANG = (["en", "es", "unknown"], [.6, .3, .1])
    OTHER_LANG = (["en", "fr", "de"], [.2, .6, .2])

    def trial(self, rng, length_weights, lang) -> dict:
        tracker = DriftTracker()  # the real defaults: 100 reference, 100 window, threshold 0.25
        for phase_lengths, phase_lang in ((self.NORMAL_LENGTH, self.NORMAL_LANG),
                                          (length_weights, lang)):
            for _ in range(100):
                tracker.observe({
                    "length": rng.choices(self.LENGTHS, phase_lengths)[0],
                    "language": rng.choices(phase_lang[0], phase_lang[1])[0],
                })
        return tracker.snapshot().scores

    def test_unchanged_traffic_rarely_raises_a_false_alarm(self):
        rng = random.Random(11)
        trials = 300

        alarms = sum(
            any(score > 0.25 for score in
                self.trial(rng, self.NORMAL_LENGTH, self.NORMAL_LANG).values())
            for _ in range(trials))

        assert alarms / trials < 0.05

    def test_a_big_change_is_always_caught(self):
        rng = random.Random(12)
        trials = 300

        caught = sum(
            max(self.trial(rng, self.LONGER_LENGTH, self.OTHER_LANG).values()) > 0.25
            for _ in range(trials))

        assert caught / trials > 0.99


class TestPromptDrift:
    class Detector:
        def __init__(self, language="en") -> None:
            self.language = language
            self.texts: list[str] = []

        def detect(self, text: str) -> str:
            self.texts.append(text)
            return self.language

    def setup_drift(self, language="en"):
        self.metrics = RecordingMetrics()
        self.detector = self.Detector(language)
        self.tracker = DriftTracker(2, 2, 2)
        return PromptDrift(self.tracker, self.detector, self.metrics)

    @staticmethod
    def chat(*messages: tuple[str, str]) -> ChatRequest:
        return ChatRequest(tuple(ChatMessage(role, text) for role, text in messages))

    def test_reports_the_length_and_language_to_the_metrics(self):
        drift = self.setup_drift("es")

        drift.observe(self.chat(("user", "x" * 120)))

        assert self.metrics.prompts == [(120, "es")]

    def test_the_length_counts_every_message_the_model_will_read(self):
        drift = self.setup_drift()

        drift.observe(self.chat(("system", "a" * 30), ("user", "b" * 40), ("assistant", "c" * 30)))

        assert self.metrics.prompts[0][0] == 100

    def test_only_what_the_user_wrote_is_used_to_guess_the_language(self):
        drift = self.setup_drift()

        drift.observe(self.chat(("system", "You are a helpful assistant."),
                                ("user", "hola"), ("assistant", "Hello there"),
                                ("user", "gracias")))

        assert self.detector.texts == ["hola gracias"]

    def test_only_the_end_of_a_long_conversation_is_inspected(self):
        drift = self.setup_drift()

        drift.observe(self.chat(("user", "a" * 3000 + "THE END")))

        assert len(self.detector.texts[0]) == 500
        assert self.detector.texts[0].endswith("THE END")

    def test_feeds_the_tracker_and_exposes_its_snapshot(self):
        drift = self.setup_drift()

        for _ in range(2):
            drift.observe(self.chat(("user", "x" * 100)))

        assert drift.snapshot().reference_samples == 2


class TestObserveSafely:
    REQUEST = ChatRequest((ChatMessage("user", "hi"),))

    def test_no_observer_is_fine(self):
        observe_safely(None, self.REQUEST)

    def test_it_passes_the_request_on(self):
        observer = RecordingObserver()

        observe_safely(observer, self.REQUEST)

        assert observer.requests == [self.REQUEST]

    def test_a_failing_observer_never_fails_the_caller(self, caplog):
        observer = RecordingObserver(error=RuntimeError("bug in the observer"))

        with caplog.at_level(logging.ERROR, logger="tokenscope.drift"):
            observe_safely(observer, self.REQUEST)

        assert "could not observe" in caplog.text
