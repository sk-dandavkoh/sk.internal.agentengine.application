"""Dependency-direction guards (§5/§6 backend/AGENTS.md).

Statically forbidden imports per layer. Cheap, runs everywhere, and fails the
moment the boundary is crossed — no framework needed, an AST scan suffices.
"""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "akasha"

# layer -> top-level import roots it must never use
RULES: dict[str, set[str]] = {
    "domain": {
        "fastapi",
        "starlette",
        "uvicorn",
        "sqlalchemy",
        "redis",
        "httpx",
        "pydantic_settings",
    },
    "application": {"fastapi", "starlette", "uvicorn", "sqlalchemy", "redis"},
    "runtime": {"fastapi", "starlette", "uvicorn"},
    "nodes": {"fastapi", "starlette", "uvicorn"},
}

# akasha-internal layers each outer layer must not reach into
INTERNAL_RULES: dict[str, set[str]] = {
    "domain": {"api", "application", "infrastructure", "runtime"},
    "api": {"infrastructure"},
}


def _imports(path: Path) -> set[str]:
    """Full dot-paths of every import in a module."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_dependency_direction() -> None:
    violations: list[str] = []
    for layer, forbidden in RULES.items():
        layer_dir = SRC / layer
        if not layer_dir.exists():
            continue
        for py_file in layer_dir.rglob("*.py"):
            imports = _imports(py_file)
            bad = {imp.split(".")[0] for imp in imports} & forbidden
            bad |= {
                imp.split(".")[1] for imp in imports if imp.startswith("akasha.")
            } & INTERNAL_RULES.get(layer, set())
            for found in bad:
                violations.append(f"{py_file.relative_to(SRC)} must not import {found}")
    assert not violations, "\n".join(violations)


def test_layers_exist() -> None:
    for layer in [
        "api",
        "application",
        "domain",
        "infrastructure",
        "runtime",
        "nodes",
        "providers",
        "plugins",
        "security",
        "observability",
    ]:
        assert (SRC / layer / "__init__.py").exists(), f"missing layer: {layer}"
