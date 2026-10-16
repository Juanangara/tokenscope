import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.application.ports.reference_store import ReferenceData

FORMAT_VERSION = 1


class JsonFileReferenceStore:
    """ReferenceStore that keeps the reference distribution in a small JSON file.

    The file holds only counts per bucket and language, never prompt text. Delete it (or point
    the setting somewhere else) to make the service learn a new reference.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> ReferenceData | None:
        if not self._path.exists():
            return None
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            features = document["features"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"the drift reference file {self._path} is not valid: {exc!r}") from exc
        if not isinstance(features, dict):
            raise ValueError(f"the drift reference file {self._path} is not valid: no features")
        return features

    def save(self, reference: ReferenceData) -> None:
        document = {
            "version": FORMAT_VERSION,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "features": reference,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
        os.replace(temporary, self._path)  # a reader never sees a half-written file
