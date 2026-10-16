from typing import Protocol

# feature name -> {category -> how many prompts fell in it}
ReferenceData = dict[str, dict[str, int]]


class ReferenceStore(Protocol):
    def load(self) -> ReferenceData | None:
        """The saved reference distribution, or None if there is none yet."""
        ...

    def save(self, reference: ReferenceData) -> None: ...
