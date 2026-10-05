"""Python libraries of plugins (ADR 0004): declared in the manifest,
installed per plugin with the real uv, pinned against the engine and the
other plugins, removed with the plugin.

No network: the "index" is a directory of wheels built here
(``UV_OFFLINE`` + ``UV_FIND_LINKS``). Library names are unique per test
because a loaded library version is process-wide state.
"""

import json
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

import packaging
import pytest

from akasha.errors import AkashaError
from akasha.nodes.registry import NodeRegistry
from akasha.plugins.dependencies import DEPS_DIR, MARKER_FILE, _summary
from akasha.plugins.registry import PluginRegistry, PluginState
from akasha.sdk.testing import run_node
from tests.test_plugins import IMPORTS, assert_untouched, write_plugin

NODE_USING = """
import {module}


class Echo(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="{node_id}", name="Echo", description="Echo.", version="1.0.0",
        ),
        outputs=(NodeOutput("out", PortType.STRING),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({{"out": {module}.VERSION}})
"""


class Index:
    """A local package index: a directory of wheels uv may install from."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def add(self, name: str, version: str) -> None:
        module = name.replace("-", "_")
        info = f"{module}-{version}.dist-info"
        files = {
            f"{module}/__init__.py": f"VERSION = {version!r}\n",
            f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\n"
            f"Version: {version}\n",
            f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        }
        files[f"{info}/RECORD"] = "".join(
            f"{p},,\n" for p in [*files, f"{info}/RECORD"]
        )
        wheel = self.directory / f"{module}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            for path, content in files.items():
                archive.writestr(path, content)

    def library(self, *versions: str) -> str:
        """A new library, unique to the test, in ``versions``."""
        name = f"akfake-{uuid.uuid4().hex[:10]}"
        for version in versions:
            self.add(name, version)
        return name


def local_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Index:
    directory = tmp_path / "wheels"
    directory.mkdir()
    for variable in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")  # never the public index
    monkeypatch.setenv("UV_FIND_LINKS", str(directory))
    return Index(directory)


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Index:
    return local_index(tmp_path, monkeypatch)


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    path = tmp_path / "installed"
    path.mkdir()
    return path


@pytest.fixture
def nodes() -> NodeRegistry:
    return NodeRegistry()


@pytest.fixture
def plugins(installed: Path, nodes: NodeRegistry) -> PluginRegistry:
    return PluginRegistry(installed, nodes)


def plugin_using(
    root: Path, library: str, *requirements: str, plugin_id: str = "acme", **kw: Any
) -> Path:
    code = IMPORTS + NODE_USING.format(
        module=library.replace("-", "_"), node_id=f"{plugin_id}.echo"
    )
    return write_plugin(
        root, plugin_id, code=code, python_requirements=list(requirements), **kw
    )


async def output(nodes: NodeRegistry, node_id: str = "acme.echo") -> object:
    run = await run_node(nodes.resolve(node_id, ">=1.0.0")())
    return run.outputs["out"]


async def test_libraries_install_with_the_plugin_and_go_with_it(
    tmp_path: Path,
    index: Index,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    lib = index.library("1.0", "2.0")
    deps = installed / DEPS_DIR / "acme"

    plugins.install(plugin_using(tmp_path, lib, f"{lib}==1.0"))

    assert await output(nodes) == "1.0"
    marker = json.loads((deps / MARKER_FILE).read_text())
    assert marker["requirements"] == [f"{lib}==1.0"]
    assert str(deps) in sys.path
    assert sys.path.index(str(deps)) > sys.path.index(
        str(Path(packaging.__file__).parent.parent)
    )

    plugins.disable("acme")
    assert str(deps) not in sys.path
    assert deps.is_dir()  # kept for enable
    plugins.enable("acme")
    assert await output(nodes) == "1.0"

    plugins.remove("acme")
    assert str(deps) not in sys.path
    assert_untouched(plugins, nodes, installed)


async def test_startup_reinstalls_missing_or_stale_libraries(
    tmp_path: Path, index: Index, plugins: PluginRegistry, installed: Path
) -> None:
    lib = index.library("1.0")
    plugins.install(plugin_using(tmp_path, lib, lib))
    deps = installed / DEPS_DIR / "acme"
    for damage in ("delete", "stale"):
        if damage == "delete":  # e.g. a restored plugins volume
            for path in sorted(deps.rglob("*"), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            deps.rmdir()
        else:  # e.g. an engine upgrade changed the engine's libraries
            (deps / MARKER_FILE).write_text('{"requirements": []}')

        nodes = NodeRegistry()
        restarted = PluginRegistry(installed, nodes)
        restarted.load_installed()

        assert restarted.get("acme").state is PluginState.ENABLED
        assert await output(nodes) == "1.0"
        marker = json.loads((deps / MARKER_FILE).read_text())
        assert marker["requirements"] == [lib]


async def test_engine_libraries_are_pinned_to_the_engine_version(
    tmp_path: Path,
    index: Index,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    # Only an older "packaging" is on the index; the engine runs its own.
    index.add("packaging", "1.0")

    with pytest.raises(AkashaError) as info:
        plugins.install(plugin_using(tmp_path, "packaging", "packaging<99"))

    assert info.value.code == "PLUGIN_DEPENDENCIES_FAILED"
    assert "packaging" in str(info.value.details["output"])
    assert_untouched(plugins, nodes, installed)


async def test_plugins_share_one_version_of_a_library(
    tmp_path: Path,
    index: Index,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
) -> None:
    lib = index.library("1.0", "2.0")
    plugins.install(plugin_using(tmp_path, lib, f"{lib}==1.0", plugin_id="first"))

    # Unpinned, the second plugin gets the version already in use ...
    plugins.install(plugin_using(tmp_path, lib, lib, plugin_id="second"))
    assert await output(nodes, "second.echo") == "1.0"
    # ... and a plugin that needs another version is refused.
    with pytest.raises(AkashaError) as info:
        plugins.install(plugin_using(tmp_path, lib, f"{lib}==2.0", plugin_id="third"))

    assert info.value.code == "PLUGIN_DEPENDENCIES_FAILED"
    assert [r.plugin_id for r in plugins.list_plugins()] == ["first", "second"]
    assert await output(nodes, "first.echo") == "1.0"


async def test_another_version_of_a_loaded_library_needs_a_restart(
    tmp_path: Path,
    index: Index,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    lib = index.library("1.0", "2.0")
    plugins.install(plugin_using(tmp_path, lib, f"{lib}==1.0"))
    assert await output(nodes) == "1.0"  # imported: stays loaded
    plugins.remove("acme")

    with pytest.raises(AkashaError) as info:
        plugins.install(plugin_using(tmp_path, lib, f"{lib}==2.0", version="2.0.0"))

    assert info.value.code == "PLUGIN_DEPENDENCIES_FAILED"
    assert "restart" in info.value.message
    assert info.value.details["distributions"] == [lib]
    assert_untouched(plugins, nodes, installed)


async def test_unresolvable_requirements_and_missing_uv_change_nothing(
    tmp_path: Path,
    index: Index,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lib = index.library("1.0")
    with pytest.raises(AkashaError) as info:
        plugins.install(plugin_using(tmp_path, lib, f"{lib}==3.0"))
    assert info.value.code == "PLUGIN_DEPENDENCIES_FAILED"
    assert_untouched(plugins, nodes, installed)

    monkeypatch.setenv("PATH", "")
    with pytest.raises(AkashaError) as info:
        plugins.install(plugin_using(tmp_path, lib, lib, version="1.0.1"))
    assert info.value.code == "PLUGIN_DEPENDENCIES_FAILED"
    assert "uv" in info.value.message
    assert_untouched(plugins, nodes, installed)


def test_a_plugin_without_requirements_needs_no_uv(
    tmp_path: Path,
    plugins: PluginRegistry,
    installed: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    plugins.install(write_plugin(tmp_path))
    assert not (installed / DEPS_DIR).exists()


@pytest.mark.parametrize(
    "requirements",
    [
        ["not a requirement!"],
        ["lib @ https://example.invalid/lib-1.0-py3-none-any.whl"],
        ["lib @ file:///etc/passwd"],
        ["akasha-engine>=0.1"],
        ["Some_Lib", "some-lib==1.0"],
        ["x" * 201],
        "lib",  # not a list
        ["lib"] * 51,
    ],
)
def test_invalid_requirements_are_rejected_before_any_change(
    requirements: Any,
    tmp_path: Path,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    source = write_plugin(tmp_path, python_requirements=requirements)
    with pytest.raises(AkashaError) as info:
        plugins.install(source)
    assert info.value.code == "PLUGIN_MANIFEST_INVALID"
    assert info.value.details == {"fields": ["python_requirements"]}
    assert_untouched(plugins, nodes, installed)


def test_uv_output_is_bounded_and_drops_index_credentials() -> None:
    stderr = "\n".join(
        [f"line {i}" for i in range(100)]
        + ["  x No solution found from https://user:sk-canary-123@pypi.example/simple"]
    )
    summary = _summary(stderr)
    assert "sk-canary-123" not in summary
    assert "https://***@pypi.example/simple" in summary
    assert "line 69" not in summary
    assert len(summary) <= 4000
