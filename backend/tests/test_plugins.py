"""Plugin System (roadmap Phase 17): manifest validation, compatibility,
install/load/register, enable/disable/remove and startup discovery.

Real files in temporary directories and real imports — the loader is the
thing under test. The sample plugin running inside the engine (publish →
runtime → disable → remove) is ``tests/integration/test_plugin_runtime.py``.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from akasha.bootstrap import create_container
from akasha.config import Settings
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.plugin import PluginMetadata, compatibility_issues
from akasha.domain.project import Dependency, ProjectDraft
from akasha.errors import (
    AkashaError,
    ConfigurationError,
    DomainValidationError,
    InvalidStateTransitionError,
)
from akasha.nodes.registry import NodeNotFoundError, NodeRegistry
from akasha.plugins.manifest import MANIFEST_FILE, read_manifest
from akasha.plugins.registry import PluginRegistry, PluginState
from akasha.sdk.testing import run_node

SAMPLE = Path(__file__).parent.parent / "examples" / "plugins" / "text_tools"

IMPORTS = """
from akasha.sdk import (
    Node, NodeDefinition, NodeExecutionContext, NodeMetadata, NodeOutput,
    NodeResult, PortType,
)
"""
NODE_CLASS = """
class {cls}(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="{node_id}", name="Echo", description="Echo.",
            version="{version}",
        ),
        outputs=(NodeOutput("out", PortType.STRING),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({{"out": "{version}"}})
"""


def node_code(node_id: str, version: str = "1.0.0", cls: str = "Echo") -> str:
    return IMPORTS + NODE_CLASS.format(node_id=node_id, version=version, cls=cls)


def write_plugin(
    root: Path,
    plugin_id: str = "acme",
    *,
    version: str = "1.0.0",
    code: str | None = None,
    **manifest: Any,
) -> Path:
    """A valid plugin package with one node ``<id>.echo``; ``manifest``
    overrides top-level manifest fields (``None`` deletes one)."""
    package = root / f"src-{plugin_id}-{version}"
    package.mkdir(parents=True)
    node_id = f"{plugin_id}.echo"
    data: dict[str, Any] = {
        "manifest_version": 1,
        "id": plugin_id,
        "name": "Acme",
        "version": version,
        "engine": ">=0.1.0",
        "sdk": ">=1.0.0,<2.0.0",
        "components": {"nodes": [{"id": node_id, "entry_point": "nodes:Echo"}]},
    }
    data.update(manifest)
    data = {k: v for k, v in data.items() if v is not None}
    (package / MANIFEST_FILE).write_text(json.dumps(data))
    (package / "__init__.py").write_text("")
    (package / "nodes.py").write_text(
        code if code is not None else node_code(node_id, version)
    )
    return package


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


def assert_untouched(
    plugins: PluginRegistry, nodes: NodeRegistry, installed: Path
) -> None:
    assert plugins.list_plugins() == []
    assert nodes.list_nodes() == []
    assert dict(plugins.versions) == {}
    assert set(os.listdir(installed)) <= {"state.json"}


# -- sample plugin -------------------------------------------------------------


async def test_sample_plugin_installs_registers_and_executes(
    plugins: PluginRegistry, nodes: NodeRegistry, installed: Path
) -> None:
    record = plugins.install(SAMPLE)

    assert record.state is PluginState.ENABLED
    assert record.metadata is not None
    assert record.metadata.documentation == "README.md"
    assert (installed / "text_tools" / "README.md").is_file()
    assert dict(plugins.versions) == {"text_tools": "1.0.0"}
    run = await run_node(
        nodes.resolve("text_tools.slugify", ">=1.0.0")(),
        config={"max_length": 11},
        inputs={"text": "Héllo, Wörld & Friends!"},
    )
    assert run.outputs == {"slug": "hello-world"}


# -- manifest validation -------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"manifest_version": 2},
        {"id": "Bad Id"},
        {"version": "1.0"},
        {"engine": ">=banana"},
        {"sdk": None},
        {"name": 42},  # strict: no coercion
        {"unexpected": True},
        {"components": {"nodes": []}},
        {
            "components": {
                "nodes": [{"id": "acme.echo", "entry_point": "nodes:Echo"}],
                "triggers": [],
            }
        },
        {"components": {"nodes": [{"id": "core.http", "entry_point": "nodes:Echo"}]}},
        {"components": {"nodes": [{"id": "acme.echo", "entry_point": "../evil:X"}]}},
        {
            "components": {
                "nodes": [{"id": "acme.echo", "entry_point": "nodes:Echo"}] * 2
            }
        },
        {"dependencies": [{"kind": "plugin", "id": "acme"}]},
        {"dependencies": [{"kind": "theme", "id": "x"}]},
        {"documentation": "../../etc/passwd"},
        {"documentation": "MISSING.md"},
    ],
)
def test_invalid_manifests_are_rejected_before_any_change(
    overrides: dict[str, Any],
    tmp_path: Path,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    source = write_plugin(tmp_path, **overrides)
    with pytest.raises(AkashaError) as info:
        plugins.install(source)
    assert info.value.code == "PLUGIN_MANIFEST_INVALID"
    assert_untouched(plugins, nodes, installed)


@pytest.mark.parametrize(
    "content",
    [b"", b"{not json", b"[]", b"\xff\xfe", b" " * (64 * 1024 + 1)],
    ids=["empty", "not-json", "not-object", "not-utf8", "oversized"],
)
def test_unparseable_manifests_are_rejected(tmp_path: Path, content: bytes) -> None:
    source = write_plugin(tmp_path)
    (source / MANIFEST_FILE).write_bytes(content)
    with pytest.raises(AkashaError) as info:
        read_manifest(source)
    assert info.value.code == "PLUGIN_MANIFEST_INVALID"


def test_manifest_errors_name_fields_not_values(tmp_path: Path) -> None:
    source = write_plugin(tmp_path, name={"secret": "sk-canary-1234"})
    with pytest.raises(AkashaError) as info:
        read_manifest(source)
    assert info.value.details == {"fields": ["name"]}
    assert "sk-canary" not in str(info.value.details)


def test_missing_manifest_or_init_is_rejected(tmp_path: Path) -> None:
    source = write_plugin(tmp_path)
    (source / "__init__.py").unlink()
    with pytest.raises(AkashaError, match="__init__"):
        read_manifest(source)
    with pytest.raises(AkashaError, match="missing"):
        read_manifest(tmp_path / "nowhere")


def test_metadata_invariants() -> None:
    base: dict[str, Any] = {
        "plugin_id": "acme",
        "name": "Acme",
        "version": "1.0.0",
        "engine_compatibility": "*",
        "sdk_compatibility": "*",
    }
    PluginMetadata(**base)
    for bad in (
        {"version": "v1"},
        {"engine_compatibility": " "},
        {"sdk_compatibility": "~1.0"},
        {"dependencies": (Dependency("plugin", "x", "bad"),)},
    ):
        with pytest.raises(DomainValidationError):
            PluginMetadata(**{**base, **bad})


# -- compatibility -------------------------------------------------------------


def test_compatibility_issues_cover_engine_sdk_and_dependencies(
    nodes: NodeRegistry,
) -> None:
    metadata = PluginMetadata(
        plugin_id="acme",
        name="Acme",
        version="1.0.0",
        engine_compatibility=">=2.0.0",
        sdk_compatibility="<1.0.0",
        dependencies=(
            Dependency("plugin", "base", ">=1.0.0"),
            Dependency("plugin", "other"),
            Dependency("node", "core.http"),
        ),
    )
    issues = compatibility_issues(
        metadata,
        engine_version="0.1.0",
        sdk_version="1.0.0",
        definitions=nodes.definitions,
        plugins={"base": "0.9.0"},
    )
    assert [i.code for i in issues] == [
        ValidationCode.INCOMPATIBLE_DEPENDENCY,  # engine
        ValidationCode.INCOMPATIBLE_DEPENDENCY,  # sdk
        ValidationCode.INCOMPATIBLE_DEPENDENCY,  # base 0.9.0
        ValidationCode.MISSING_DEPENDENCY,  # other
        ValidationCode.MISSING_DEPENDENCY,  # core.http
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"engine": ">=99.0.0"},
        {"sdk": ">=2.0.0"},
        {"dependencies": [{"id": "base"}]},
        {"dependencies": [{"kind": "node", "id": "core.http"}]},
    ],
)
def test_incompatible_plugins_are_not_installed(
    overrides: dict[str, Any],
    tmp_path: Path,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    with pytest.raises(AkashaError) as info:
        plugins.install(write_plugin(tmp_path, **overrides))
    assert info.value.code == "PLUGIN_INCOMPATIBLE"
    assert info.value.details["issues"]
    assert_untouched(plugins, nodes, installed)


def test_project_plugin_dependencies_are_checked_when_plugins_are_given() -> None:
    draft = ProjectDraft(dependencies=[Dependency("plugin", "acme", ">=1.0.0")])

    def codes(plugins: dict[str, str] | None) -> list[ValidationCode]:
        return [
            i.code
            for i in draft.validate({}, plugins).issues
            if i.code is not ValidationCode.EMPTY_GRAPH
        ]

    assert codes(None) == []  # runtime path: node types decide
    assert codes({"acme": "1.2.0"}) == []
    assert codes({"acme": "0.1.0"}) == [ValidationCode.INCOMPATIBLE_DEPENDENCY]
    assert codes({}) == [ValidationCode.MISSING_DEPENDENCY]


# -- install / load failures leave no trace ------------------------------------


def test_duplicate_plugin_is_rejected(
    tmp_path: Path, plugins: PluginRegistry, nodes: NodeRegistry
) -> None:
    plugins.install(write_plugin(tmp_path))
    with pytest.raises(AkashaError) as info:
        plugins.install(write_plugin(tmp_path, version="2.0.0"))
    assert info.value.code == "PLUGIN_ALREADY_INSTALLED"
    assert plugins.versions["acme"] == "1.0.0"
    assert nodes.get("acme.echo").definition.version == "1.0.0"


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("raise RuntimeError('boom')", "RuntimeError"),
        ("import does_not_exist_anywhere", "ModuleNotFoundError"),
        ("Echo = 42", "TypeError"),  # not a Node class
        ("Other = 1", "AttributeError"),  # entry point attribute missing
        (node_code("acme.other"), "TypeError"),  # declared id ≠ class id
        (
            node_code("acme.echo")
            + "\n    def __init__(self) -> None:\n        raise ValueError('x')\n",
            "ValueError",
        ),
    ],
)
def test_broken_plugin_code_fails_the_install_cleanly(
    code: str,
    error: str,
    tmp_path: Path,
    plugins: PluginRegistry,
    nodes: NodeRegistry,
    installed: Path,
) -> None:
    with pytest.raises(AkashaError) as info:
        plugins.install(write_plugin(tmp_path, code=code))
    assert info.value.code == "PLUGIN_LOAD_FAILED"
    assert info.value.details["error"] == error
    assert_untouched(plugins, nodes, installed)


def test_node_id_conflict_rolls_back_every_node_of_the_plugin(
    tmp_path: Path, plugins: PluginRegistry, nodes: NodeRegistry, installed: Path
) -> None:
    # "acme.x.echo" is namespaced under both "acme" and "acme.x".
    taken = {"nodes": [{"id": "acme.x.echo", "entry_point": "nodes:Echo"}]}
    plugins.install(
        write_plugin(tmp_path, code=node_code("acme.x.echo"), components=taken)
    )
    source = write_plugin(
        tmp_path,
        "acme.x",
        code=node_code("acme.x.first", cls="First")
        + NODE_CLASS.format(node_id="acme.x.echo", version="1.0.0", cls="Echo"),
        components={
            "nodes": [
                {"id": "acme.x.first", "entry_point": "nodes:First"},
                {"id": "acme.x.echo", "entry_point": "nodes:Echo"},
            ]
        },
    )
    with pytest.raises(AkashaError) as info:
        plugins.install(source)
    assert info.value.code == "NODE_ALREADY_REGISTERED"
    assert [d.node_id for d in nodes.list_nodes()] == ["acme.x.echo"]
    assert [r.plugin_id for r in plugins.list_plugins()] == ["acme"]
    assert set(os.listdir(installed)) <= {"acme", "state.json"}


def test_symlinks_in_packages_are_rejected(
    tmp_path: Path, plugins: PluginRegistry, nodes: NodeRegistry, installed: Path
) -> None:
    source = write_plugin(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("outside the package")
    try:
        (source / "data.txt").symlink_to(secret)
    except OSError:  # Windows without Developer Mode / admin rights
        pytest.skip("this OS user cannot create symlinks")
    with pytest.raises(AkashaError) as info:
        plugins.install(source)
    assert info.value.code == "PLUGIN_PACKAGE_INVALID"
    assert_untouched(plugins, nodes, installed)


# -- lifecycle -----------------------------------------------------------------


async def test_disable_enable_remove_and_reinstall(
    tmp_path: Path, plugins: PluginRegistry, nodes: NodeRegistry, installed: Path
) -> None:
    plugins.install(write_plugin(tmp_path))

    assert plugins.disable("acme").state is PluginState.DISABLED
    with pytest.raises(NodeNotFoundError):
        nodes.get("acme.echo")
    assert dict(plugins.versions) == {}
    with pytest.raises(InvalidStateTransitionError):
        plugins.disable("acme")

    assert plugins.enable("acme").state is PluginState.ENABLED
    assert nodes.get("acme.echo")
    with pytest.raises(InvalidStateTransitionError):
        plugins.enable("acme")

    plugins.remove("acme")
    assert_untouched(plugins, nodes, installed)
    with pytest.raises(AkashaError) as info:
        plugins.get("acme")
    assert info.value.code == "PLUGIN_NOT_FOUND"

    # Upgrade = remove + install: the new code runs, not a cached module.
    plugins.install(write_plugin(tmp_path, version="2.0.0"))
    run = await run_node(nodes.get("acme.echo")())
    assert run.outputs == {"out": "2.0.0"}


def test_plugins_with_enabled_dependents_cannot_be_disabled_or_removed(
    tmp_path: Path, plugins: PluginRegistry
) -> None:
    dependent = write_plugin(tmp_path, "addon", dependencies=[{"id": "base"}])
    with pytest.raises(AkashaError, match="not compatible"):
        plugins.install(dependent)
    plugins.install(write_plugin(tmp_path, "base"))
    plugins.install(dependent)

    for action in (plugins.disable, plugins.remove):
        with pytest.raises(AkashaError) as info:
            action("base")
        assert info.value.code == "PLUGIN_IN_USE"
        assert info.value.details["dependents"] == ["addon"]
    plugins.disable("addon")
    plugins.disable("base")
    with pytest.raises(AkashaError, match="not compatible"):
        plugins.enable("addon")  # its dependency is disabled


# -- startup discovery ---------------------------------------------------------


def test_startup_restores_state_in_dependency_order(
    tmp_path: Path, plugins: PluginRegistry, installed: Path
) -> None:
    # "addon" sorts before "zbase" but must load after it.
    plugins.install(write_plugin(tmp_path, "zbase"))
    plugins.install(write_plugin(tmp_path, "addon", dependencies=[{"id": "zbase"}]))
    plugins.install(write_plugin(tmp_path, "off"))
    plugins.disable("off")

    nodes = NodeRegistry()
    restarted = PluginRegistry(installed, nodes)
    restarted.load_installed()

    assert {r.plugin_id: r.state for r in restarted.list_plugins()} == {
        "addon": PluginState.ENABLED,
        "off": PluginState.DISABLED,
        "zbase": PluginState.ENABLED,
    }
    assert [d.node_id for d in nodes.list_nodes()] == ["addon.echo", "zbase.echo"]


def test_startup_records_broken_plugins_as_failed_and_keeps_going(
    tmp_path: Path, installed: Path
) -> None:
    nodes = NodeRegistry()
    plugins = PluginRegistry(installed, nodes)
    plugins.install(write_plugin(tmp_path, "good"))
    write_plugin(tmp_path, "other").rename(installed / "renamed")  # name ≠ id
    for pid, dep in (("cyc-a", "cyc-b"), ("cyc-b", "cyc-a")):
        package = write_plugin(tmp_path, pid, dependencies=[{"id": dep}])
        package.rename(installed / pid)
    broken = write_plugin(tmp_path, "broken")
    broken.rename(installed / "broken")
    (installed / "broken" / "nodes.py").write_text("raise RuntimeError('x')")
    (installed / "stray-file").write_text("ignored")

    nodes = NodeRegistry()
    restarted = PluginRegistry(installed, nodes)
    restarted.load_installed()

    assert {r.plugin_id: (r.state, r.error) for r in restarted.list_plugins()} == {
        "broken": (PluginState.FAILED, "PLUGIN_LOAD_FAILED"),
        "cyc-a": (PluginState.FAILED, "PLUGIN_INCOMPATIBLE"),
        "cyc-b": (PluginState.FAILED, "PLUGIN_INCOMPATIBLE"),
        "good": (PluginState.ENABLED, None),
        "renamed": (PluginState.FAILED, "PLUGIN_MANIFEST_INVALID"),
    }
    assert [d.node_id for d in nodes.list_nodes()] == ["good.echo"]
    # A fixed plugin can be enabled from FAILED; one without metadata cannot.
    (installed / "broken" / "nodes.py").write_text(node_code("broken.echo"))
    assert restarted.enable("broken").state is PluginState.ENABLED
    with pytest.raises(InvalidStateTransitionError):
        restarted.enable("renamed")
    restarted.remove("renamed")
    assert not (installed / "renamed").exists()


def test_malformed_state_file_and_missing_directory_fail_fast(
    tmp_path: Path, installed: Path
) -> None:
    for content in ('{"disabled": [1]}', '{"disabled": "acme"}', "[]", "{"):
        (installed / "state.json").write_text(content)
        with pytest.raises(ConfigurationError):
            PluginRegistry(installed, NodeRegistry()).load_installed()
    with pytest.raises(ConfigurationError):
        PluginRegistry(tmp_path / "nope", NodeRegistry())


def test_container_loads_plugins_from_the_configured_directory(
    installed: Path,
) -> None:
    PluginRegistry(installed, NodeRegistry()).install(SAMPLE)

    container = create_container(Settings(environment="test", plugins_dir=installed))

    assert container.plugins is not None
    assert container.plugins.versions == {"text_tools": "1.0.0"}
    assert container.nodes.get("text_tools.slugify")
    assert container.nodes.get("core.http")  # built-ins unaffected
