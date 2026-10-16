import pytest

from app.config import Settings, parse_flag


class TestParseFlag:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
    def test_true_spellings(self, raw):
        assert parse_flag(raw, False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", " off "])
    def test_false_spellings(self, raw):
        assert parse_flag(raw, True) is False

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_unset_keeps_the_default(self, raw):
        assert parse_flag(raw, True) is True
        assert parse_flag(raw, False) is False

    def test_a_typo_is_an_error_not_a_silent_default(self):
        with pytest.raises(ValueError, match="true or false"):
            parse_flag("ture", True)


class TestDriftSettings:
    NAMES = ("DRIFT_ENABLED", "DRIFT_REFERENCE_SIZE", "DRIFT_WINDOW_SIZE", "DRIFT_MIN_SAMPLES",
             "DRIFT_THRESHOLD", "DRIFT_REFERENCE_FILE")

    @pytest.fixture(autouse=True)
    def clean_environment(self, monkeypatch):
        for name in self.NAMES:
            monkeypatch.delenv(name, raising=False)

    def test_defaults(self):
        settings = Settings.from_env()

        assert settings.drift_enabled is True
        assert (settings.drift_reference_size, settings.drift_window_size,
                settings.drift_min_samples) == (100, 100, 100)
        assert settings.drift_threshold == 0.25
        assert settings.drift_reference_file == ""

    def test_everything_can_be_set_from_the_environment(self, monkeypatch):
        for name, value in {"DRIFT_ENABLED": "false", "DRIFT_REFERENCE_SIZE": "250",
                            "DRIFT_WINDOW_SIZE": "200", "DRIFT_MIN_SAMPLES": "150",
                            "DRIFT_THRESHOLD": "0.1",
                            "DRIFT_REFERENCE_FILE": "/data/ref.json"}.items():
            monkeypatch.setenv(name, value)

        settings = Settings.from_env()

        assert settings.drift_enabled is False
        assert (settings.drift_reference_size, settings.drift_window_size,
                settings.drift_min_samples) == (250, 200, 150)
        assert settings.drift_threshold == 0.1
        assert settings.drift_reference_file == "/data/ref.json"
