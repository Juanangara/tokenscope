from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

from app.application.ports.language import UNKNOWN_LANGUAGE

# langdetect is unreliable on short text, and it is wrong with confidence: measured on 15 real
# prompts of 18-30 characters it got 11 right, and "Say hello." came back as Somali at 0.86.
# Below this length it is better to say "unknown" than to invent a language.
MIN_CHARS = 40
MIN_PROBABILITY = 0.8

DetectorFactory.seed = 0  # without a seed the same text can get different answers


class LangdetectDetector:
    """LanguageDetector backed by the `langdetect` library (pure Python, no model download).

    The first detection loads the language profiles and takes about 0.6 s; a call is made when
    the detector is built so that cost is paid at startup, not by the first user. Afterwards a
    detection takes 5-7 ms, and it runs inside the event loop.
    """

    def __init__(self) -> None:
        self.detect("The quick brown fox jumps over the lazy dog near the river bank.")

    def detect(self, text: str) -> str:
        text = text.strip()
        if len(text) < MIN_CHARS:
            return UNKNOWN_LANGUAGE
        try:
            best = detect_langs(text)[0]
        except LangDetectException:  # e.g. the text has no letters
            return UNKNOWN_LANGUAGE
        return best.lang if best.prob >= MIN_PROBABILITY else UNKNOWN_LANGUAGE


class NoLanguageDetector:
    """Used when language detection is not wanted: every prompt is 'unknown'."""

    def detect(self, text: str) -> str:
        return UNKNOWN_LANGUAGE
