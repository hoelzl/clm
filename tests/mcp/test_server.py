"""Smoke tests for the MCP server wiring.

These tests exercise ``create_server`` and verify that every expected
tool is registered with the correct handler, schema fields, and default
arguments.  Individual tool behavior is already covered by
``tests/mcp/test_tools.py`` — here we only assert the server glue.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

# ``clm.mcp.server`` imports the SDK's server class at module level, so the
# whole file is unimportable without the ``[mcp]`` extra.  Skip collection
# cleanly in environments that don't install it (e.g. the Docker integration
# CI job, which only installs the default extras).  Skip on the *package*,
# not on a submodule: the server-class import path differs between mcp 1
# (``mcp.server.fastmcp``) and mcp 2 (``mcp.server.mcpserver``) and
# ``clm.mcp.server`` resolves whichever is installed (#914).
pytest.importorskip("mcp", reason="mcp SDK not installed (needs [mcp] extra)")

from clm.mcp import server as server_module  # noqa: E402
from clm.mcp.server import create_server, run_server  # noqa: E402

EXPECTED_TOOLS = {
    "topic_resolve",
    "slides_search",
    "course_outline",
    "course_context",
    "validate",
    "slides_normalize",
    "slides_language_view",
    "slides_suggest_sync",
    "slides_sync_report",
    "voiceover_extract",
    "voiceover_inline",
    "authoring_rules",
    "harvest_transcribe",
    "harvest_identify_rev",
    "harvest_backfill_dry",
    "harvest_cache_list",
    "harvest_trace_show",
    "harvest_report",
    "harvest_task",
}


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Provide an empty data directory."""
    return tmp_path


@pytest.fixture()
def course_tree(tmp_path: Path) -> Path:
    """Minimal course tree with one topic and one spec."""
    slides = tmp_path / "slides"
    topic = slides / "module_100_basics" / "topic_010_intro"
    topic.mkdir(parents=True)
    (topic / "slides_intro.py").write_text(
        '# %% [markdown]\n# {{ header("Einführung", "Introduction") }}\n',
        encoding="utf-8",
    )

    specs = tmp_path / "course-specs"
    specs.mkdir()
    (specs / "course.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections>
        <section>
            <name><de>S1</de><en>S1</en></name>
            <topics>
                <dir-group>
                    <dir>module_100_basics</dir>
                    <topic>topic_010_intro</topic>
                </dir-group>
            </topics>
        </section>
    </sections>
</course>
""",
        encoding="utf-8",
    )
    return tmp_path


class TestCreateServer:
    """create_server should return a configured server with every CLM tool."""

    def test_returns_named_server_instance(self, tmp_data_dir: Path) -> None:
        server = create_server(tmp_data_dir)
        assert server.name == "clm"

    def test_all_expected_tools_registered(self, tmp_data_dir: Path) -> None:
        server = create_server(tmp_data_dir)
        registered = set(server._tool_manager._tools.keys())
        assert registered == EXPECTED_TOOLS

    async def test_list_tools_exposes_all(self, tmp_data_dir: Path) -> None:
        server = create_server(tmp_data_dir)
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert names == EXPECTED_TOOLS

    def test_tool_schemas_have_descriptions(self, tmp_data_dir: Path) -> None:
        """Every tool should have a non-trivial docstring-derived description."""
        server = create_server(tmp_data_dir)
        for name in EXPECTED_TOOLS:
            tool = server._tool_manager.get_tool(name)
            assert tool is not None, f"{name} missing from tool manager"
            assert tool.description, f"{name} has no description"
            # Should mention its primary subject in the first line
            first_line = tool.description.splitlines()[0]
            assert len(first_line) > 10, f"{name} description too short"

    def test_tools_accept_expected_parameters(self, tmp_data_dir: Path) -> None:
        """Spot-check parameter schemas for a handful of tools."""
        server = create_server(tmp_data_dir)

        resolve = server._tool_manager.get_tool("topic_resolve")
        assert "topic_id" in resolve.parameters["properties"]

        search = server._tool_manager.get_tool("slides_search")
        search_props = search.parameters["properties"]
        assert "query" in search_props
        assert "max_results" in search_props

        outline = server._tool_manager.get_tool("course_outline")
        outline_props = outline.parameters["properties"]
        assert "spec_file" in outline_props
        assert "language" in outline_props
        assert "include_disabled" in outline_props


class TestServerDispatch:
    """Smoke-call selected tools through the MCP dispatch path.

    This verifies that the closures created by ``create_server`` actually
    bind ``data_dir`` correctly and forward arguments to the handler.

    Calls go through the server's public ``call_tool`` rather than the
    private tool manager: mcp 2 made the manager's ``context`` argument
    mandatory, while the public entry point takes ``(name, arguments)`` in
    both majors (#914).
    """

    async def test_call_resolve_topic(self, course_tree: Path) -> None:
        server = create_server(course_tree)
        result = await server.call_tool("topic_resolve", {"topic_id": "intro"})
        payload = _extract_text_payload(result)
        data = json.loads(payload)
        assert data["topic_id"] == "intro"
        assert "topic_010_intro" in (data.get("path") or "")

    async def test_call_course_outline(self, course_tree: Path) -> None:
        server = create_server(course_tree)
        result = await server.call_tool(
            "course_outline",
            {"spec_file": "course-specs/course.xml", "language": "en"},
        )
        payload = _extract_text_payload(result)
        data = json.loads(payload)
        assert data["course_name"] == "Course"
        assert data["language"] == "en"

    async def test_call_search_slides_forwards_max_results(self, course_tree: Path) -> None:
        server = create_server(course_tree)
        result = await server.call_tool("slides_search", {"query": "intro", "max_results": 1})
        payload = _extract_text_payload(result)
        data = json.loads(payload)
        assert "results" in data
        assert len(data["results"]) <= 1


class TestRunServer:
    """run_server should configure the server and invoke stdio transport."""

    def test_run_server_uses_stdio_transport(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []

        real_create_server = server_module.create_server

        def fake_create_server(data_dir: Path):
            srv = real_create_server(data_dir)

            def fake_run(*args, **kwargs) -> None:
                calls.append({"args": args, "kwargs": kwargs})

            srv.run = fake_run  # type: ignore[method-assign]
            return srv

        monkeypatch.setattr(server_module, "create_server", fake_create_server)
        run_server(tmp_data_dir)

        assert len(calls) == 1
        assert calls[0]["kwargs"].get("transport") == "stdio"


class TestStdioHandshake:
    """A real ``clm mcp`` process speaks MCP over stdio (regression for #914).

    The in-process tests above prove the tool wiring; this one proves the
    *server can start* on the installed SDK -- the failure mode of #914 was
    an ``ImportError`` at ``clm.mcp.server`` import time, which no in-process
    test that already imported the module can see.  The handshake is driven
    as raw JSON-RPC lines rather than through the SDK's client API, which
    changed between mcp 1 and 2, so the same test covers both majors.
    """

    HANDSHAKE_TIMEOUT_S = 90.0  # generous: cold CLI start on a loaded box

    def test_initialize_and_list_tools(self, tmp_data_dir: Path) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-m", "clm", "mcp", "--data-dir", str(tmp_data_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(tmp_data_dir),
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.monotonic() + self.HANDSHAKE_TIMEOUT_S

        def send(message: dict[str, object]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()

        def await_response(wanted_id: int) -> dict[str, object]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"no response for id={wanted_id} in time")
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty as exc:
                    raise AssertionError(f"no response for id={wanted_id} in time") from exc
                if line is None:
                    raise AssertionError(f"server exited before answering id={wanted_id}")
                if not line.strip():
                    continue
                message = json.loads(line)
                if message.get("id") == wanted_id:
                    return message

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "clm-test", "version": "0"},
                    },
                }
            )
            init = await_response(1)
            assert "result" in init, init
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            listed = await_response(2)
        except AssertionError:
            proc.kill()
            _, stderr = proc.communicate(timeout=30)
            raise AssertionError(f"MCP stdio handshake failed; server stderr:\n{stderr}") from None
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)

        assert "result" in listed, listed
        result = listed["result"]
        assert isinstance(result, dict)
        names = {tool["name"] for tool in result["tools"]}
        assert names == EXPECTED_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text_payload(result: object) -> str:
    """Pull the ``.text`` payload out of an MCP tool-call result.

    The server's ``call_tool`` may return a raw string, a list of content
    objects, a tuple ``(content, structured)`` (mcp 1) or a
    ``CallToolResult`` carrying a ``.content`` list (mcp 2), depending on
    the SDK version.  We accept all of them and return the first text chunk.
    """
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, list):
        return _extract_text_payload(content)
    if isinstance(result, tuple) and result:
        return _extract_text_payload(result[0])
    if isinstance(result, list) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text is not None:
            return text
        return str(first)
    text = getattr(result, "text", None)
    if text is not None:
        return text
    return str(result)
