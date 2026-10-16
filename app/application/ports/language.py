from typing import Protocol

UNKNOWN_LANGUAGE = "unknown"


class LanguageDetector(Protocol):
    def detect(self, text: str) -> str:
        """ISO 639-1 code of the language of `text`, or UNKNOWN_LANGUAGE when it cannot tell."""
        ...
