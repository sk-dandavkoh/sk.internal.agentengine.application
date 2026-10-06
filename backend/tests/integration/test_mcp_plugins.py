"""Plugins over MCP, through the real boundary: authoring, the trust gate,
the lifecycle, and the reproducibility of published versions
(docs/mcp.md). Real plugin directory (tmp_path), real PostgreSQL, the
official MCP client; plugin code really runs where the tools say it does.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from akasha.config import Settings
from tests.integration.test_mcp import (
    Engine,
    call,
    fails,
    mcp,
    mcp_settings,
    running_engine,
)

pytestmark = pytest.mark.usefixtures("clean_db")


def plugin_settings(db_url: str, plugins: Path, *, code: bool = True) -> Settings:
    return mcp_settings(db_url, plugins_dir=plugins, mcp_plugin_code=code)


@pytest.fixture
async def engine(db_url: str, tmp_path: Path) -> AsyncIterator[Engine]:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    async with running_engine(plugin_settings(db_url, plugins)) as running:
        yield running


def with_behavior(
    files: dict[str, str], expression: str, version: str
) -> dict[str, str]:
    """The scaffold with ``result = <expression>`` and another version."""
    files = dict(files)
    files["nodes.py"] = files["nodes.py"].replace(
        'context.inputs["text"].upper() + suffix', expression
    )
    manifest = json.loads(files["akasha-plugin.json"])
    manifest["version"] = version
    files["akasha-plugin.json"] = json.dumps(manifest)
    return files


async def scaffold(client: Client) -> dict[str, str]:
    created = await call(
        client,
        "plugin_create",
        plugin_id="acme",
        node_name="shout",
        name="Shout",
        description="Shouts a text.",
    )
    assert created["node_type"] == "acme.shout"
    files: dict[str, str] = created["files"]
    return files


async def shout_workflow(client: Client) -> dict[str, Any]:
    """core.input(title) → acme.shout(suffix '!') → core.output."""
    workflow = await call(client, "workflow_create", name="shouting")
    pid = workflow["id"]
    source = await call(
        client,
        "graph_node_add",
        workflow_id=pid,
        expected_revision=workflow["revision"],
        node_type="core.input",
        config={"field": "title"},
    )
    shout = await call(
        client,
        "graph_node_add",
        workflow_id=pid,
        expected_revision=source["revision"],
        node_type="acme.shout",
    )
    configured = await call(
        client,
        "graph_node_update",
        workflow_id=pid,
        expected_revision=shout["revision"],
        node_id=shout["node_id"],
        config={"suffix": "!"},
    )
    sink = await call(
        client,
        "graph_node_add",
        workflow_id=pid,
        expected_revision=configured["revision"],
        node_type="core.output",
    )
    first = await call(
        client,
        "graph_edge_add",
        workflow_id=pid,
        expected_revision=sink["revision"],
        source_node_id=source["node_id"],
        source_port="value",
        target_node_id=shout["node_id"],
        target_port="text",
    )
    last = await call(
        client,
        "graph_edge_add",
        workflow_id=pid,
        expected_revision=first["revision"],
        source_node_id=shout["node_id"],
        source_port="result",
        target_node_id=sink["node_id"],
        target_port="value",
    )
    return last | {"shout": shout["node_id"], "sink": sink["node_id"]}


async def run(engine: Engine, client: Client, pid: str, **start: Any) -> dict[str, Any]:
    started = await call(
        client, "execution_start", workflow_id=pid, input={"title": "hi"}, **start
    )
    await engine.run(started["id"])
    return await call(client, "execution_get", execution_id=started["id"])


# -- the acceptance scenario -------------------------------------------------


async def test_an_agent_extends_the_engine_with_a_node_it_needs(
    engine: Engine,
) -> None:
    """Phase acceptance scenario: every step through MCP, in order."""
    async with mcp(engine, engine.admin_token) as client:  # 1. connect
        await call(client, "system_info")
        # 2-3. discover the catalog; the capability is missing
        found = await call(client, "node_types_list", query="shout")
        assert found["items"] == []
        # 4. discover the SDK
        sdk = await call(client, "plugin_sdk_get")
        assert sdk["plugin_code_allowed"] is True
        assert "NodeResult" in sdk["guide"]
        assert "components" in sdk["manifest_schema"]["properties"]
        # 5-10. create a plugin with a node: inputs, outputs, config, code
        files = await scaffold(client)
        # 11. validate (static: no code runs)
        check = await call(client, "plugin_validate", files=files)
        assert check == {
            "valid": True,
            "plugin_id": "acme",
            "version": "0.1.0",
            "node_types": ["acme.shout"],
            "installed_state": None,
            "issues": [],
        }
        # 12. test the node
        tested = await call(
            client,
            "plugin_test",
            files=files,
            node_type="acme.shout",
            config={"suffix": "?"},
            inputs={"text": "abc"},
        )
        assert tested["outputs"] == {"result": "ABC?"}
        # 13. package
        package = await call(client, "plugin_package", files=files)
        assert package["filename"] == "acme-0.1.0.zip"
        again = await call(client, "plugin_package", files=files)
        assert again["sha256"] == package["sha256"]  # deterministic
        # 14-15. install (enables it)
        installed = await call(
            client, "plugin_install", package_base64=package["package_base64"]
        )
        assert installed["state"] == "enabled"
        assert installed["metadata"]["node_types"] == ["acme.shout"]
        # 16. discover the new node in the canonical catalog
        found = await call(client, "node_types_list", query="shout")
        assert [(n["node_type"], n["plugin_id"]) for n in found["items"]] == [
            ("acme.shout", "acme")
        ]
        contract = await call(client, "node_type_get", node_type="acme.shout")
        assert [p["name"] for p in contract["inputs"]] == ["text"]
        assert "suffix" in contract["config_schema"]["properties"]
        # 17-20. a workflow using it: add, configure, connect
        built = await shout_workflow(client)
        pid = built["workflow_id"]
        # 21. validate
        assert (await call(client, "workflow_validate", workflow_id=pid))["valid"]
        # 22. publish: the version pins the plugin version
        version = await call(
            client,
            "workflow_publish",
            workflow_id=pid,
            expected_revision=built["revision"],
        )
        assert version["plugin_versions"] == {"acme": "0.1.0"}
        # 23-24. run and inspect
        execution = await run(engine, client, pid)
        assert execution["status"] == "completed"
        assert execution["node_versions"]["acme.shout"] == "0.1.0"
        # 25. the node's result and the logs
        node = await call(
            client,
            "execution_node_get",
            execution_id=execution["id"],
            node_id=built["sink"],
        )
        assert node["outputs"] == {"value": "HI!"}
        logs = await call(client, "execution_logs_get", execution_id=execution["id"])
        assert logs["has_more"] is False
    # The frontend and REST see the same plugin node in the same catalog.
    async with engine.rest(engine.admin_token) as rest:
        catalog = (await rest.get("/api/v1/nodes")).json()
        assert "acme.shout" in [n["node_type"] for n in catalog["items"]]


# -- reproducibility ------------------------------------------------------------


async def test_published_versions_never_run_on_another_plugin_version(
    engine: Engine,
) -> None:
    async with mcp(engine, engine.admin_token) as client:
        v1_files = await scaffold(client)
        await call(client, "plugin_install", files=v1_files)
        built = await shout_workflow(client)
        pid = built["workflow_id"]
        await call(client, "workflow_publish", workflow_id=pid)

        v2_files = with_behavior(v1_files, '"v2:" + context.inputs["text"]', "0.2.0")
        blocked = await fails(client, "plugin_update", files=v2_files)
        assert blocked["code"] == "PLUGIN_IN_USE"
        assert blocked["details"]["workflows"] == [pid]
        updated = await call(client, "plugin_update", files=v2_files, force=True)
        assert updated["plugin"]["metadata"]["version"] == "0.2.0"
        assert updated["affected_workflows"] == [pid]

        # Version 1 was published against acme 0.1.0: it refuses to run on
        # 0.2.0 instead of silently changing behavior.
        old = await run(engine, client, pid, version=1)
        assert old["status"] == "failed"
        assert old["error"]["code"] == "EXECUTION_GRAPH_INVALID"
        assert old["error"]["details"] == {"issue_codes": "incompatible_dependency"}
        assert not [n for n in old["nodes"] if n["status"] == "completed"]

        # Publishing again pins the new version, which runs the new code.
        v2 = await call(client, "workflow_publish", workflow_id=pid)
        assert v2["plugin_versions"] == {"acme": "0.2.0"}
        new = await run(engine, client, pid, version=2)
        assert new["status"] == "completed"
        out = await call(
            client, "execution_node_get", execution_id=new["id"], node_id=built["sink"]
        )
        assert out["outputs"] == {"value": "v2:hi"}

        # Reinstalling 0.1.0 brings version 1 back.
        await call(client, "plugin_update", files=v1_files, force=True)
        assert (await run(engine, client, pid, version=1))["status"] == "completed"


async def test_a_failed_update_restores_the_previous_version(engine: Engine) -> None:
    async with mcp(engine, engine.admin_token) as client:
        files = await scaffold(client)
        await call(client, "plugin_install", files=files)
        broken = with_behavior(files, "1 / 0", "0.3.0")
        broken["nodes.py"] += "\nraise RuntimeError('import-time failure')\n"
        error = await fails(client, "plugin_update", files=broken)
        assert error["code"] == "PLUGIN_LOAD_FAILED"
        plugin = await call(client, "plugin_get", plugin_id="acme")
        assert (plugin["state"], plugin["metadata"]["version"]) == ("enabled", "0.1.0")
        tested = await call(client, "node_type_get", node_type="acme.shout")
        assert tested["version"] == "0.1.0"
        built = await shout_workflow(client)
        await call(client, "workflow_publish", workflow_id=built["workflow_id"])
        assert (await run(engine, client, built["workflow_id"]))[
            "status"
        ] == "completed"


# -- lifecycle ---------------------------------------------------------------------


async def test_disable_enable_remove_are_state_and_dependency_aware(
    engine: Engine,
) -> None:
    async with mcp(engine, engine.admin_token) as client:
        files = await scaffold(client)
        await call(client, "plugin_install", files=files)
        repeated = await fails(client, "plugin_install", files=files)
        assert repeated["code"] == "PLUGIN_ALREADY_INSTALLED"
        enabled_twice = await fails(client, "plugin_enable", plugin_id="acme")
        assert enabled_twice["code"] == "INVALID_STATE_TRANSITION"

        disabled = await call(client, "plugin_disable", plugin_id="acme")
        assert disabled["state"] == "disabled"
        assert (await call(client, "node_types_list", query="shout"))["items"] == []
        # Metadata stays inspectable while disabled.
        assert (await call(client, "plugin_get", plugin_id="acme"))["metadata"]
        assert (await call(client, "plugin_validate", files=files))[
            "installed_state"
        ] == "disabled"
        await call(client, "plugin_enable", plugin_id="acme")

        built = await shout_workflow(client)
        await call(client, "workflow_publish", workflow_id=built["workflow_id"])
        blocked = await fails(client, "plugin_remove", plugin_id="acme")
        assert blocked["code"] == "PLUGIN_IN_USE"
        assert blocked["details"]["workflows"] == [built["workflow_id"]]
        assert (await call(client, "plugin_list"))["items"][0]["state"] == "enabled"

        removed = await call(client, "plugin_remove", plugin_id="acme", force=True)
        assert removed["affected_workflows"] == [built["workflow_id"]]
        assert (await call(client, "plugin_list"))["items"] == []
        gone = await fails(client, "node_type_get", node_type="acme.shout")
        assert gone["code"] == "NODE_NOT_FOUND"
        run_after = await run(engine, client, built["workflow_id"])
        assert run_after["status"] == "failed"


async def test_concurrent_installs_of_one_package_install_once(engine: Engine) -> None:
    async with mcp(engine, engine.admin_token) as client:
        files = await scaffold(client)
        results = await asyncio.gather(
            *(client.call_tool("plugin_install", {"files": files}) for _ in range(3))
        )
    codes = sorted(
        "ok" if not r.is_error else r.structured_content["code"] for r in results
    )
    assert codes == ["PLUGIN_ALREADY_INSTALLED"] * 2 + ["ok"]


# -- trust boundary and malicious input ---------------------------------------------


async def test_plugin_code_needs_the_operator_opt_in(
    db_url: str, tmp_path: Path
) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    marker = tmp_path / "executed"
    settings = plugin_settings(db_url, plugins, code=False)
    async with (
        running_engine(settings) as engine,
        mcp(engine, engine.admin_token) as client,
    ):
        info = await call(client, "system_info")
        assert info["plugin_code_allowed"] is False
        files = await scaffold(client)
        files["nodes.py"] += f"\nopen({str(marker)!r}, 'w').close()\n"
        # Authoring without running code works.
        assert (await call(client, "plugin_validate", files=files))["valid"]
        await call(client, "plugin_package", files=files)
        for tool, extra in (
            ("plugin_install", {}),
            ("plugin_update", {}),
            ("plugin_test", {"node_type": "acme.shout", "inputs": {"text": "x"}}),
        ):
            error = await fails(client, tool, files=files, **extra)
            assert error["code"] == "PLUGIN_CODE_NOT_ALLOWED"
            assert error["details"] == {"setting": "AKASHA_MCP_PLUGIN_CODE"}
        assert not marker.exists()  # no line of the plugin ever ran
        assert list(plugins.iterdir()) == []


async def test_validation_never_executes_the_package(
    engine: Engine, tmp_path: Path
) -> None:
    marker = tmp_path / "executed"
    async with mcp(engine, engine.admin_token) as client:
        files = await scaffold(client)
        files["nodes.py"] += f"\nopen({str(marker)!r}, 'w').close()\n"
        assert (await call(client, "plugin_validate", files=files))["valid"]
        await call(client, "plugin_package", files=files)
        assert not marker.exists()
        # plugin_test is documented to run it: the gate above is what
        # stands between an agent and this.
        await call(
            client,
            "plugin_test",
            files=files,
            node_type="acme.shout",
            inputs={"text": "x"},
        )
        assert marker.exists()


@pytest.mark.parametrize(
    "path",
    ["../escape.py", "/etc/evil.py", "C:/evil.py", "a/../../evil.py", "a\\..\\evil.py"],
)
async def test_package_paths_cannot_escape(
    engine: Engine, tmp_path: Path, path: str
) -> None:
    async with mcp(engine, engine.admin_token) as client:
        files = await scaffold(client) | {path: "x = 1\n"}
        check = await call(client, "plugin_validate", files=files)
        assert check["valid"] is False
        assert check["issues"][0]["code"] == "PLUGIN_PACKAGE_INVALID"
        error = await fails(client, "plugin_install", files=files)
        assert error["code"] == "PLUGIN_PACKAGE_INVALID"
    assert not (tmp_path / "escape.py").exists()
    assert list((tmp_path / "plugins").iterdir()) == []


async def test_validation_reports_actionable_issues(engine: Engine) -> None:
    async with mcp(engine, engine.admin_token) as client:
        files = await scaffold(client)
        manifest = json.loads(files["akasha-plugin.json"])

        bad_id = dict(files)
        bad_id["akasha-plugin.json"] = json.dumps(manifest | {"id": "Bad Id!"})
        issue = (await call(client, "plugin_validate", files=bad_id))["issues"][0]
        assert issue["code"] == "PLUGIN_MANIFEST_INVALID"

        unknown_field = dict(files)
        unknown_field["akasha-plugin.json"] = json.dumps(manifest | {"run": "rm -rf"})
        check = await call(client, "plugin_validate", files=unknown_field)
        issue = check["issues"][0]
        assert issue["code"] == "PLUGIN_MANIFEST_INVALID"
        assert "run" in issue["message"]

        incompatible = dict(files)
        incompatible["akasha-plugin.json"] = json.dumps(
            manifest | {"engine": ">=9.0.0"}
        )
        check = await call(client, "plugin_validate", files=incompatible)
        assert [i["code"] for i in check["issues"]] == ["PLUGIN_INCOMPATIBLE"]

        syntax = files | {"nodes.py": files["nodes.py"] + "\ndef broken(:\n"}
        check = await call(client, "plugin_validate", files=syntax)
        assert check["issues"][0]["code"] == "PYTHON_SYNTAX_ERROR"
        assert check["issues"][0]["path"] == "nodes.py"
        assert check["issues"][0]["line"] is not None

        renamed = files | {
            "nodes.py": files["nodes.py"].replace("class Shout", "class Other")
        }
        check = await call(client, "plugin_validate", files=renamed)
        assert [i["code"] for i in check["issues"]] == ["ENTRY_POINT_NOT_FOUND"]

        neither = await fails(client, "plugin_validate")
        assert neither["code"] == "PLUGIN_PACKAGE_INVALID"


async def test_a_failing_plugin_test_is_reported_not_raised(engine: Engine) -> None:
    async with mcp(engine, engine.admin_token) as client:
        files = with_behavior(await scaffold(client), "1 / 0", "0.1.0")
        error = await fails(
            client,
            "plugin_test",
            files=files,
            node_type="acme.shout",
            inputs={"text": "x"},
        )
        assert error["code"] == "PLUGIN_TEST_FAILED"
        assert error["details"]["error"] == "ZeroDivisionError"
        bad_input = await fails(
            client, "plugin_test", files=files, node_type="acme.shout", inputs={}
        )
        assert bad_input["code"] == "NODE_VALIDATION_ERROR"
        hanging = with_behavior(
            await scaffold(client), "await asyncio.sleep(3600)", "0.1.0"
        )
        hanging["nodes.py"] = "import asyncio\n" + hanging["nodes.py"]
        stuck = await fails(
            client,
            "plugin_test",
            files=hanging,
            node_type="acme.shout",
            inputs={"text": "x"},
            timeout_seconds=0.2,
        )
        assert stuck["code"] == "PLUGIN_TEST_FAILED"
        assert stuck["details"]["timeout_seconds"] == 0.2
        undeclared = await fails(
            client, "plugin_test", files=files, node_type="acme.other", inputs={}
        )
        assert undeclared["code"] == "PLUGIN_NODE_NOT_FOUND"
