"""CLI tests without infrastructure: parsing, configuration errors, exit
codes, output, and the node/plugin commands that need no database.
Database-backed commands: tests/integration/test_cli.py."""

import json
import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from akasha.cli import EXIT_FAILED, EXIT_INTERNAL, EXIT_OK, main
from tests.test_plugins import SAMPLE

GREET = """
from akasha.sdk import (
    Node, NodeDefinition, NodeExecutionContext, NodeInput, NodeMetadata,
    NodeOutput, NodeResult, PortType, NodeConfigSchema,
)


class Greet(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="acme.greet", name="Greet",
            description="Greets someone.", version="1.2.0",
        ),
        inputs=(NodeInput("name", PortType.STRING),),
        outputs=(NodeOutput("greeting", PortType.STRING),),
        config_schema=NodeConfigSchema({
            "type": "object",
            "properties": {"greeting": {"type": "string"}},
            "additionalProperties": False,
        }),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        word = context.config.get("greeting", "Hello")
        return NodeResult({"greeting": f"{word}, {context.inputs['name']}"})


class NoExecute(Node):
    definition = Greet.definition


NOT_A_NODE = 42
"""


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self) -> object:
        return json.loads(self.out)


Cli = Callable[..., Result]


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only what a test sets: no developer AKASHA_* variable leaks in."""
    for name in list(os.environ):
        if name.startswith("AKASHA_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AKASHA_ENVIRONMENT", "test")


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Iterator[Cli]:
    def run(*argv: str) -> Result:
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse: usage errors, --help, --version
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    yield run


@pytest.fixture
def greet_file(tmp_path: Path) -> Path:
    path = tmp_path / "greet.py"
    path.write_text(GREET)
    return path


# -- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["project"],
        ["project", "publish"],  # missing project id
        ["project", "export", "proj_1"],  # missing --output
        ["project", "run", "proj_1", "--version", "one"],
        ["project", "run", "proj_1", "--input", "{not json"],
        ["project", "run", "proj_1", "--input", "[1, 2]"],
        ["node", "test", "core.log", "--config", "@/nonexistent/config.json"],
    ],
)
def test_usage_errors_exit_2(cli: Cli, argv: list[str]) -> None:
    result = cli(*argv)
    assert result.code == 2
    assert "usage: akasha" in result.err
    assert result.out == ""


def test_version_and_help(cli: Cli) -> None:
    assert cli("--version").code == EXIT_OK
    result = cli("--help")
    assert result.code == EXIT_OK
    for group in ("project", "node", "plugin", "execution", "backup", "restore"):
        assert group in result.out


def test_json_input_accepts_a_file(cli: Cli, tmp_path: Path) -> None:
    # Parsing succeeds (exit is the missing database, not usage).
    doc = tmp_path / "input.json"
    doc.write_text('{"title": "x"}')
    assert cli("project", "run", "proj_1", "--input", f"@{doc}").code == EXIT_FAILED


# -- configuration ---------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["project", "list"],
        ["project", "create", "demo"],
        ["execution", "get", "exec_1"],
        ["backup", "-o", "unused.backup"],
    ],
)
def test_database_commands_need_a_database(cli: Cli, argv: list[str]) -> None:
    result = cli(*argv)
    assert result.code == EXIT_FAILED
    assert "CONFIGURATION_ERROR" in result.err
    assert "AKASHA_DATABASE_URL" in result.err
    assert result.out == ""


def test_plugin_commands_need_a_plugin_directory(cli: Cli) -> None:
    result = cli("plugin", "list")
    assert result.code == EXIT_FAILED
    assert "AKASHA_PLUGINS_DIR" in result.err


def test_invalid_configuration_does_not_echo_values(
    cli: Cli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AKASHA_METRICS_TOKEN", "too-short-secret-token")
    result = cli("node", "list")
    assert result.code == EXIT_FAILED
    assert "CONFIGURATION_ERROR" in result.err
    assert "metrics_token" in result.err
    assert "too-short-secret-token" not in result.err


def test_errors_as_json_on_stderr(cli: Cli) -> None:
    result = cli("project", "list", "--json")
    assert result.code == EXIT_FAILED
    error = json.loads(result.err)
    assert error["code"] == "CONFIGURATION_ERROR"
    assert error["details"] == {"setting": "AKASHA_DATABASE_URL"}


def test_unreachable_database_is_an_internal_error_without_secrets(
    cli: Cli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Port 1 on loopback: refused at once, no network involved.
    monkeypatch.setenv(
        "AKASHA_DATABASE_URL", "postgresql://akasha:db-password-value@127.0.0.1:1/x"
    )
    result = cli("project", "list")
    assert result.code == EXIT_INTERNAL
    assert "INTERNAL_ERROR" in result.err
    assert "db-password-value" not in result.err + result.out


# -- nodes -----------------------------------------------------------------


def test_node_list(cli: Cli) -> None:
    text = cli("node", "list")
    assert text.code == EXIT_OK
    assert text.out.splitlines()[0].split() == ["NODE", "VERSION", "NAME"]
    assert "core.input" in text.out

    listed = cli("node", "list", "--json").json()
    assert isinstance(listed, list)
    core_input = next(n for n in listed if n["node_id"] == "core.input")
    assert core_input["version"] == "1.0.0"
    assert core_input["outputs"] == [{"name": "value", "type": "json"}]


def test_node_validate(cli: Cli, greet_file: Path) -> None:
    ok = cli("node", "validate", f"{greet_file}:Greet")
    assert (ok.code, ok.out.strip()) == (EXIT_OK, "valid: acme.greet 1.2.0")
    assert cli("node", "validate", "core.log").code == EXIT_OK

    broken = cli("node", "validate", f"{greet_file}:NoExecute")
    assert broken.code == EXIT_FAILED
    assert "NODE_CONTRACT_ERROR" in broken.err


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("core.missing", "NODE_NOT_FOUND"),
        ("{file}:NOT_A_NODE", "NODE_NOT_FOUND"),
        ("{file}:Absent", "NODE_NOT_FOUND"),
        ("{dir}/absent.py:Greet", "NODE_NOT_FOUND"),
        ("no_such_module_xyz:Greet", "NODE_NOT_FOUND"),
    ],
)
def test_node_validate_rejects_bad_targets(
    cli: Cli,
    greet_file: Path,
    target: str,
    code: str,
) -> None:
    target = target.format(file=greet_file, dir=greet_file.parent)
    result = cli("node", "validate", target)
    assert result.code == EXIT_FAILED
    assert code in result.err


def test_node_test_runs_the_node(cli: Cli, greet_file: Path) -> None:
    result = cli(
        "node",
        "test",
        f"{greet_file}:Greet",
        "--config",
        '{"greeting": "Hi"}',
        "--inputs",
        '{"name": "Ada"}',
    )
    assert result.code == EXIT_OK
    assert result.json() == {
        "outputs": {"greeting": "Hi, Ada"},
        "events": [],
        "metrics": {},
    }


def test_node_test_registered_node(cli: Cli) -> None:
    result = cli(
        "node",
        "test",
        "core.input",
        "--config",
        '{"field": "title"}',
        "--execution-input",
        '{"title": "hello"}',
    )
    assert result.code == EXIT_OK
    assert result.json()["outputs"] == {"value": "hello"}  # type: ignore[index]


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (
            ["--config", '{"unknown": 1}', "--inputs", '{"name": "a"}'],
            "NODE_CONFIGURATION_ERROR",
        ),
        (["--inputs", '{"name": 7}'], "NODE_VALIDATION_ERROR"),
        (["--inputs", "{}"], "NODE_VALIDATION_ERROR"),
        # A plaintext secret is refused before the node runs.
        (
            ["--config", '{"api_key": "sk-plain"}', "--inputs", '{"name": "a"}'],
            "NODE_CONFIGURATION_ERROR",
        ),
    ],
)
def test_node_test_failures(
    cli: Cli,
    greet_file: Path,
    args: list[str],
    code: str,
) -> None:
    result = cli("node", "test", f"{greet_file}:Greet", *args)
    assert result.code == EXIT_FAILED
    assert code in result.err
    assert "sk-plain" not in result.err


# -- plugins ---------------------------------------------------------------


def test_plugin_install_and_list(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AKASHA_PLUGINS_DIR", str(tmp_path))
    assert cli("plugin", "list").out.strip() == "(none)"

    installed = cli("plugin", "install", str(SAMPLE), "--json")
    assert installed.code == EXIT_OK
    assert installed.json() == {
        "plugin_id": "text_tools",
        "state": "enabled",
        "version": "1.0.0",
        "nodes": ["text_tools.slugify"],
        "error": None,
    }
    # A fresh process loads it from the directory.
    listed = cli("plugin", "list")
    assert listed.out.splitlines()[1].split() == ["text_tools", "enabled", "1.0.0"]
    assert "text_tools.slugify" in cli("node", "list").out

    again = cli("plugin", "install", str(SAMPLE))
    assert again.code == EXIT_FAILED
    assert "PLUGIN_ALREADY_INSTALLED" in again.err


def test_plugin_install_rejects_invalid_package(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("AKASHA_PLUGINS_DIR", str(plugins))
    result = cli("plugin", "install", str(tmp_path / "missing"))
    assert result.code == EXIT_FAILED
    assert "PLUGIN" in result.err
    assert list(plugins.iterdir()) == []


def test_plugin_remove_needs_the_database(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without the database the usage check cannot run: refuse, never guess.
    monkeypatch.setenv("AKASHA_PLUGINS_DIR", str(tmp_path))
    assert cli("plugin", "install", str(SAMPLE)).code == EXIT_OK
    result = cli("plugin", "remove", "text_tools")
    assert result.code == EXIT_FAILED
    assert "AKASHA_DATABASE_URL" in result.err
    assert (tmp_path / "text_tools").is_dir()


def test_backup_file_mode_helper(tmp_path: Path) -> None:
    from akasha.cli import _write_private

    path = tmp_path / "b.backup"
    _write_private(str(path), b"data")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_private(str(path), b"other")
    assert path.read_bytes() == b"data"
