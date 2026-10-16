"""The layering rules of docs/architecture.md, enforced instead of just promised."""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# Libraries that do I/O or belong to a delivery mechanism: the inner layers must not use them.
OUTSIDE_WORLD = {"fastapi", "starlette", "uvicorn", "httpx", "pydantic", "prometheus_client",
                 "psutil", "requests", "locust"}


def imports_of(path: Path) -> set[str]:
    """Every module a file imports, as a dotted name."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def files_in(layer: str) -> list[Path]:
    return sorted((APP / layer).rglob("*.py"))


def violations(layer: str, forbidden: set[str]) -> list[str]:
    found = []
    for path in files_in(layer):
        for name in imports_of(path):
            if name.split(".")[0] in forbidden or any(
                    name == f or name.startswith(f + ".") for f in forbidden):
                found.append(f"{path.relative_to(APP)} imports {name}")
    return found


def test_the_layers_exist():
    assert files_in("domain") and files_in("application") and files_in("adapters")


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inner_layers_use_no_web_or_io_library(layer):
    assert violations(layer, OUTSIDE_WORLD) == []


def test_domain_depends_on_nothing_of_ours_but_itself():
    assert violations("domain", {"app.application", "app.adapters", "app.config",
                                 "app.main"}) == []


def test_application_does_not_know_the_adapters():
    assert violations("application", {"app.adapters", "app.config", "app.main"}) == []


def test_adapters_do_not_depend_on_each_other():
    assert violations("adapters/inbound", {"app.adapters.outbound"}) == []
    assert violations("adapters/outbound", {"app.adapters.inbound"}) == []
