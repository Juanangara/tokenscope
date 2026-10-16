import json

import pytest

from app.adapters.outbound.language_detector import LangdetectDetector, NoLanguageDetector
from app.adapters.outbound.prometheus_metrics import PrometheusMetrics
from app.adapters.outbound.reference_file import JsonFileReferenceStore
from app.application.drift import DriftSnapshot
from app.application.ports.language import UNKNOWN_LANGUAGE


class TestLangdetectDetector:
    detector = LangdetectDetector()

    @pytest.mark.parametrize("text, language", [
        ("Write a story about a lighthouse keeper who finds a message in a bottle.", "en"),
        ("Escribe una historia sobre un farero que encuentra un mensaje en una botella.", "es"),
        ("Ecris une histoire sur un gardien de phare qui trouve un message dans une bouteille.",
         "fr"),
        ("Schreibe eine Geschichte ueber einen Leuchtturmwaerter, der eine Flaschenpost findet.",
         "de"),
    ])
    def test_recognizes_a_full_sentence(self, text, language):
        assert self.detector.detect(text) == language

    @pytest.mark.parametrize("short", ["", "   ", "Hola", "Say hello.", "How are you today?",
                                       "Explain quantum computing"])
    def test_short_text_is_unknown_instead_of_a_confident_guess(self, short):
        # These are exactly the cases where the library is wrong with high confidence
        assert self.detector.detect(short) == UNKNOWN_LANGUAGE

    def test_text_without_letters_is_unknown(self):
        assert self.detector.detect("1234567890 " * 8) == UNKNOWN_LANGUAGE

    def test_the_answer_does_not_change_between_calls(self):
        text = "Explain how a rainbow forms, step by step, in simple words for a child."

        assert {self.detector.detect(text) for _ in range(20)} == {"en"}

    def test_building_the_detector_loads_the_language_profiles_up_front(self, monkeypatch):
        import app.adapters.outbound.language_detector as module

        calls = []
        real = module.detect_langs
        monkeypatch.setattr(module, "detect_langs", lambda text: calls.append(text) or real(text))

        LangdetectDetector()

        assert len(calls) == 1  # the slow first detection happened at startup, not for a user

    def test_the_null_detector_never_guesses(self):
        assert NoLanguageDetector().detect("Write a story about a lighthouse keeper.") == \
            UNKNOWN_LANGUAGE


class TestJsonFileReferenceStore:
    REFERENCE = {"length": {"0-49": 3, "50-149": 7}, "language": {"en": 10}}

    def test_a_missing_file_means_no_reference_yet(self, tmp_path):
        assert JsonFileReferenceStore(tmp_path / "nope.json").load() is None

    def test_what_is_saved_can_be_loaded(self, tmp_path):
        store = JsonFileReferenceStore(tmp_path / "ref.json")

        store.save(self.REFERENCE)

        assert store.load() == self.REFERENCE

    def test_the_file_holds_counts_and_no_prompt_text(self, tmp_path):
        path = tmp_path / "ref.json"
        JsonFileReferenceStore(path).save(self.REFERENCE)

        document = json.loads(path.read_text(encoding="utf-8"))

        assert set(document) == {"version", "captured_at", "features"}
        assert document["features"] == self.REFERENCE

    def test_missing_folders_are_created(self, tmp_path):
        path = tmp_path / "deep" / "er" / "ref.json"

        JsonFileReferenceStore(path).save(self.REFERENCE)

        assert path.exists()

    def test_saving_over_an_old_reference_replaces_it_and_leaves_no_temp_file(self, tmp_path):
        store = JsonFileReferenceStore(tmp_path / "ref.json")
        store.save({"length": {"0-49": 1}, "language": {"en": 1}})

        store.save(self.REFERENCE)

        assert store.load() == self.REFERENCE
        assert [p.name for p in tmp_path.iterdir()] == ["ref.json"]

    @pytest.mark.parametrize("content", ["not json at all", "[]", '{"version": 1}',
                                         '{"features": "oops"}', "null"])
    def test_a_corrupt_file_is_an_error_not_a_silent_new_start(self, tmp_path, content):
        path = tmp_path / "ref.json"
        path.write_text(content, encoding="utf-8")

        with pytest.raises(ValueError, match="not valid"):
            JsonFileReferenceStore(path).load()


class TestPrometheusPromptMetrics:
    @staticmethod
    def value(metrics, name, **labels):
        return metrics.registry.get_sample_value(name, labels)

    def test_prompt_length_and_language_are_counted(self):
        metrics = PrometheusMetrics()
        metrics.prompt_observed(120, "en")
        metrics.prompt_observed(3000, "es")
        metrics.prompt_observed(80, "en")

        assert self.value(metrics, "tokenscope_prompt_length_chars_count") == 3
        assert self.value(metrics, "tokenscope_prompt_length_chars_sum") == 3200
        assert self.value(metrics, "tokenscope_prompt_length_chars_bucket", le="150.0") == 2
        assert self.value(metrics, "tokenscope_prompts_total", language="en") == 2
        assert self.value(metrics, "tokenscope_prompts_total", language="es") == 1

    def test_while_the_reference_is_filling_no_score_is_published(self):
        metrics = PrometheusMetrics()
        metrics.watch_drift(lambda: DriftSnapshot(False, 42, 0, 0.25))

        assert self.value(metrics, "tokenscope_prompt_drift_ready") == 0
        assert self.value(metrics, "tokenscope_prompt_drift_reference_samples") == 42
        assert self.value(metrics, "tokenscope_prompt_drift_score", feature="length") is None

    def test_scores_are_published_per_feature_once_ready(self):
        metrics = PrometheusMetrics()
        metrics.watch_drift(lambda: DriftSnapshot(
            True, 100, 100, 0.25, {"length": 0.31, "language": 0.04}))

        assert self.value(metrics, "tokenscope_prompt_drift_ready") == 1
        assert self.value(metrics, "tokenscope_prompt_drift_score", feature="length") == 0.31
        assert self.value(metrics, "tokenscope_prompt_drift_score", feature="language") == 0.04
        assert self.value(metrics, "tokenscope_prompt_drift_threshold") == 0.25
        assert self.value(metrics, "tokenscope_prompt_drift_window_samples") == 100

    def test_the_scores_are_read_again_at_every_scrape(self):
        metrics = PrometheusMetrics()
        state = {"snap": DriftSnapshot(True, 100, 100, 0.25, {"length": 0.1, "language": 0.1})}
        metrics.watch_drift(lambda: state["snap"])
        assert self.value(metrics, "tokenscope_prompt_drift_score", feature="length") == 0.1

        state["snap"] = DriftSnapshot(True, 100, 100, 0.25, {"length": 0.9, "language": 0.1})

        assert self.value(metrics, "tokenscope_prompt_drift_score", feature="length") == 0.9

    def test_the_drift_metrics_appear_in_the_scrape_output(self):
        metrics = PrometheusMetrics()
        metrics.watch_drift(lambda: DriftSnapshot(True, 100, 100, 0.25, {"length": 0.5}))

        text = metrics.render()[0].decode()

        assert 'tokenscope_prompt_drift_score{feature="length"} 0.5' in text
