"""Detecting that the prompts people send no longer look like the ones the service was tested with.

Each prompt is reduced to a few coarse features (how long it is, which language it is in); the
text itself is never kept. A reference distribution of those features is compared with the
distribution of the most recent prompts, and the difference is scored with the Population
Stability Index (PSI). Rule of thumb for PSI: under 0.1 stable, 0.1-0.25 some change, over 0.25
a significant change.
"""
import logging
import math
from bisect import bisect_right
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.application.ports.language import LanguageDetector
from app.application.ports.metrics import MetricsSink
from app.application.ports.prompt_observer import PromptObserver
from app.application.ports.reference_store import ReferenceData, ReferenceStore
from app.domain.models import ChatRequest

logger = logging.getLogger("tokenscope.drift")

FEATURES = ("length", "language")

# Few buckets on purpose: PSI is noisy when there are many buckets and few samples (see
# tests/test_drift.py, which measures the false-alarm rate with these exact settings).
LENGTH_EDGES = (50, 150, 500, 2000)
LENGTH_LABELS = ("0-49", "50-149", "150-499", "500-1999", "2000+")

LANGUAGE_SAMPLE_CHARS = 500  # only the end of the conversation is looked at, to keep it cheap


def length_bucket(chars: int) -> str:
    """Which length bucket a prompt of `chars` characters falls in."""
    return LENGTH_LABELS[bisect_right(LENGTH_EDGES, chars)]


def psi(reference: Mapping[str, int], current: Mapping[str, int], smoothing: float = 0.5) -> float:
    """Population Stability Index between two counted distributions.

    Every category gets `smoothing` extra counts, so a category seen on one side only (a new
    language) gives a large but finite score instead of a division by zero.
    """
    categories = set(reference) | set(current)
    if not categories:
        return 0.0
    ref_total = sum(reference.values()) + smoothing * len(categories)
    cur_total = sum(current.values()) + smoothing * len(categories)
    score = 0.0
    for category in categories:
        expected = (reference.get(category, 0) + smoothing) / ref_total
        actual = (current.get(category, 0) + smoothing) / cur_total
        score += (actual - expected) * math.log(actual / expected)
    return score


@dataclass(frozen=True)
class DriftSnapshot:
    ready: bool  # a reference exists and the window has enough prompts to be scored
    reference_samples: int  # prompts in the reference (or captured so far)
    window_samples: int
    threshold: float
    scores: dict[str, float] = field(default_factory=dict)  # feature -> PSI, only when ready


class DriftTracker:
    """Keeps the reference distribution and a sliding window of the most recent prompts.

    The reference is loaded from the store if it has one; otherwise it is the first
    `reference_size` prompts seen, and it is saved to the store once complete. Only after that
    does the window start to fill, so the two never share a prompt.
    """

    def __init__(self, reference_size: int = 100, window_size: int = 100, min_samples: int = 100,
                 threshold: float = 0.25, store: ReferenceStore | None = None) -> None:
        if reference_size < 1 or window_size < 1:
            raise ValueError("reference_size and window_size must be at least 1")
        if not 1 <= min_samples <= window_size:
            raise ValueError("min_samples must be between 1 and window_size")
        if threshold <= 0:
            raise ValueError("threshold must be positive")

        self._reference_size = reference_size
        self._window_size = window_size
        self._min_samples = min_samples
        self._threshold = threshold
        self._store = store

        self._reference: dict[str, Counter] | None = None
        self._capturing: dict[str, Counter] = {f: Counter() for f in FEATURES}
        self._captured = 0
        self._window: deque[Mapping[str, str]] = deque()
        self._current: dict[str, Counter] = {f: Counter() for f in FEATURES}

        saved = store.load() if store else None
        if saved is not None:
            self._reference = self._checked(saved)

    @staticmethod
    def _checked(saved: ReferenceData) -> dict[str, Counter]:
        reference: dict[str, Counter] = {}
        for feature in FEATURES:
            counts = saved.get(feature)
            if not isinstance(counts, dict) or not counts:
                raise ValueError(f"the saved reference has no data for '{feature}'")
            if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in counts.values()):
                raise ValueError(f"the saved reference has invalid counts for '{feature}'")
            if sum(counts.values()) == 0:
                raise ValueError(f"the saved reference is empty for '{feature}'")
            reference[feature] = Counter(counts)
        return reference

    def observe(self, observation: Mapping[str, str]) -> None:
        if self._reference is None:
            self._capture(observation)
        else:
            self._slide(observation)

    def _capture(self, observation: Mapping[str, str]) -> None:
        for feature in FEATURES:
            self._capturing[feature][observation[feature]] += 1
        self._captured += 1
        if self._captured < self._reference_size:
            return

        self._reference = self._capturing
        if self._store:
            try:
                self._store.save({f: dict(c) for f, c in self._reference.items()})
            except OSError:
                # Losing the saved copy only means learning it again after a restart.
                logger.warning("could not save the drift reference", exc_info=True)

    def _slide(self, observation: Mapping[str, str]) -> None:
        self._window.append(observation)
        for feature in FEATURES:
            self._current[feature][observation[feature]] += 1

        if len(self._window) > self._window_size:
            oldest = self._window.popleft()
            for feature in FEATURES:
                counter = self._current[feature]
                counter[oldest[feature]] -= 1
                if counter[oldest[feature]] == 0:
                    del counter[oldest[feature]]

    def snapshot(self) -> DriftSnapshot:
        if self._reference is None:
            return DriftSnapshot(False, self._captured, 0, self._threshold)

        reference_samples = sum(self._reference[FEATURES[0]].values())
        ready = len(self._window) >= self._min_samples
        scores = ({f: psi(self._reference[f], self._current[f]) for f in FEATURES}
                  if ready else {})
        return DriftSnapshot(ready, reference_samples, len(self._window), self._threshold, scores)


class PromptDrift:
    """PromptObserver: reduces each prompt to its features and feeds the tracker and metrics."""

    def __init__(self, tracker: DriftTracker, detector: LanguageDetector,
                 metrics: MetricsSink) -> None:
        self._tracker = tracker
        self._detector = detector
        self._metrics = metrics

    def observe(self, request: ChatRequest) -> None:
        chars = request.prompt_chars
        spoken = " ".join(m.content for m in request.messages if m.role == "user")
        language = self._detector.detect(spoken[-LANGUAGE_SAMPLE_CHARS:])

        self._metrics.prompt_observed(chars, language)
        self._tracker.observe({"length": length_bucket(chars), "language": language})

    def snapshot(self) -> DriftSnapshot:
        return self._tracker.snapshot()


def observe_safely(observer: PromptObserver | None, request: ChatRequest) -> None:
    """Observation is a side concern: whatever goes wrong in it must never fail a request."""
    if observer is None:
        return
    try:
        observer.observe(request)
    except Exception:
        logger.exception("could not observe a prompt")
