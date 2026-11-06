"""Guard ordinary import boundaries; runtime behavior still requires review."""

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "klados_world"
ALLOWED: dict[str, set[str]] = {
    "society": {"society"},
    "application": {"application", "society"},
    "api": {"api", "application", "society"},
    "workers": {"workers", "application", "society"},
    "persistence": {"persistence", "application", "society"},
    "integrations": {"integrations", "application", "society"},
}


def imported_modules(source: str, package: str) -> set[str]:
    """Resolve import and from-import targets, including relative submodules."""
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            targets.add(base)
            targets.update(f"{base}.{alias.name}" for alias in node.names)
    return targets


def forbidden_imports(source: str, package: str, layer: str) -> set[str]:
    forbidden: set[str] = set()
    for target in imported_modules(source, package):
        parts = target.split(".")
        if parts[0] == "klados_world":
            if len(parts) > 1 and parts[1] not in ALLOWED[layer]:
                forbidden.add(target)
        elif (
            layer in {"society", "application"} and parts[0] not in sys.stdlib_module_names
        ) or parts[0] in {"tee_arena", "arena"}:
            forbidden.add(target)
    return forbidden


def test_source_imports_respect_layer_boundaries() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        if len(relative.parts) == 1:
            # The root is intentionally inert; no imports or executable statements.
            tree = ast.parse(path.read_text())
            assert all(
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                for node in tree.body
            )
            continue
        layer = relative.parts[0]
        assert layer in ALLOWED, f"New layer needs architecture review: {layer}"
        package = "klados_world." + ".".join(relative.parts[:-1])
        forbidden = forbidden_imports(path.read_text(), package, layer)
        if forbidden:
            violations.append(f"{relative}: {sorted(forbidden)}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "import klados_world.persistence",
        "from klados_world import persistence",
        "from ..persistence import repository",
        "from .. import persistence",
        "import psycopg",
        "from tee_arena.society import rules",
    ],
)
def test_guard_rejects_domain_infrastructure_imports(source: str) -> None:
    assert forbidden_imports(source, "klados_world.society", "society")


def test_guard_accepts_domain_and_standard_library_imports() -> None:
    assert not forbidden_imports(
        "from datetime import datetime\nfrom . import rules",
        "klados_world.society",
        "society",
    )
