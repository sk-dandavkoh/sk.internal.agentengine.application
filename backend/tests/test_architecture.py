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
    "sdk": {"fastapi", "starlette", "uvicorn", "sqlalchemy", "redis"},
    "plugins": {"fastapi", "starlette", "uvicorn", "sqlalchemy", "redis"},
    # The CLI adapts services; persistence and HTTP stay out of it (§58).
    "cli": {"fastapi", "starlette", "sqlalchemy", "redis"},
    # Workers see the queue contract, never its technology (§72).
    "workers": {"fastapi", "starlette", "uvicorn", "sqlalchemy", "redis"},
}

# Provider libraries live behind ``akasha.providers`` only (§27, §78).
PROVIDER_LIBRARIES = {"litellm", "openai", "anthropic"}
for _layer in ("domain", "application", "runtime", "nodes", "sdk", "plugins"):
    RULES[_layer] |= PROVIDER_LIBRARIES

# akasha-internal layers each outer layer must not reach into
INTERNAL_RULES: dict[str, set[str]] = {
    "domain": {"api", "application", "infrastructure", "runtime", "plugins"},
    "api": {"infrastructure"},
    # Nodes see only the contract (+ security guards); never the engine.
    "nodes": {"api", "application", "infrastructure", "runtime", "plugins"},
    # The SDK re-exports the contract; only its test harness borrows the
    # runtime's credential rule. Never HTTP or persistence.
    "sdk": {"api", "infrastructure", "providers"},
    # Plugins feed the node registry; they never touch HTTP, persistence or
    # the runtime (a plugin node is an ordinary node to both).
    "plugins": {"api", "application", "infrastructure", "runtime", "providers"},
    "workers": {"api", "infrastructure"},
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
        module = SRC / f"{layer}.py"  # a single-module layer, e.g. cli.py
        files = layer_dir.rglob("*.py") if layer_dir.exists() else [module]
        for py_file in (f for f in files if f.exists()):
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


def test_builtin_nodes_use_the_sdk_for_the_node_contract() -> None:
    """Built-ins dogfood ``akasha.sdk``: a contract name imported from the
    domain instead would let the SDK surface drift unnoticed. The registry
    is engine code and exempt."""
    import akasha.sdk

    public = set(akasha.sdk.__all__)
    violations = []
    for py_file in (SRC / "nodes").glob("*.py"):
        if py_file.name == "registry.py":
            continue
        for node in ast.walk(ast.parse(py_file.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "akasha.domain.node":
                violations += [
                    f"{py_file.name}: {a.name}" for a in node.names if a.name in public
                ]
    assert not violations, "import these from akasha.sdk:\n" + "\n".join(violations)


# Arbitrary-code paths (AGENTS §38/§64): user data is never evaluated,
# unpickled or handed to a shell. Plugin code is loaded in one place only.
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
_FORBIDDEN_MODULES = {"pickle", "marshal", "shelve", "dill", "yaml", "subprocess"}
_CODE_LOADERS = {"plugins/registry.py", "cli.py"}  # trusted, operator-chosen code
# Runs uv with a fixed argv to install plugin libraries (ADR 0004); shell=
# stays forbidden there too.
_SUBPROCESS_USERS = {"plugins/dependencies.py"}
# Starts operator-allowed programs as argv, never via a shell (ADR 0005).
_PROCESS_STARTERS = {"nodes/command.py"}


def test_no_arbitrary_code_execution_primitives() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for imported in _imports(path):
            root = imported.split(".")[0]
            if root in _FORBIDDEN_MODULES and not (
                root == "subprocess" and rel in _SUBPROCESS_USERS
            ):
                offenders.append(f"{rel}: imports {imported}")
            # importlib.metadata reads distribution metadata, it runs no code.
            if (
                root == "importlib"
                and imported != "importlib.metadata"
                and rel not in _CODE_LOADERS
            ):
                offenders.append(f"{rel}: loads code via {imported}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                offenders.append(f"{rel}:{node.lineno}: {node.func.id}()")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "system":
                offenders.append(f"{rel}:{node.lineno}: .system()")
            # asyncio starts processes without importing subprocess.
            attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if attr == "create_subprocess_shell" or (
                attr == "create_subprocess_exec" and rel not in _PROCESS_STARTERS
            ):
                offenders.append(f"{rel}:{node.lineno}: {attr}()")
            for kw in node.keywords:
                if kw.arg == "shell":
                    offenders.append(f"{rel}:{node.lineno}: shell=")
    assert offenders == []
