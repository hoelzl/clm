"""Smoke tests for the MCP server wiring.

These tests exercise ``create_server`` and verify that every expected
tool is registered with the correct handler, schema fields, and default
arguments.  Individual tool behavior is already covered by
``tests/mcp/test_tools.py`` — here we only assert the server glue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.mcp import server as server_module
from clm.mcp.server import create_server, run_server

EXPECTED_TOOLS = {
    "resolve_topic",
    "search_slides",
    "course_outline",
    "validate_spec",
    "validate_slides",
    "normalize_slides",
    "get_language_view",
    "suggest_sync",
    "extract_voiceover",
    "inline_voiceover",
    "course_authoring_rules",
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
    """create_server should return a configured FastMCP with every CLM tool."""

    def test_returns_fastmcp_instance(self, tmp_data_dir: Path) -> None:
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

        resolve = server._tool_manager.get_tool("resolve_topic")
        assert "topic_id" in resolve.parameters["properties"]

        search = server._tool_manager.get_tool("search_slides")
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
    """

    async def test_call_resolve_topic(self, course_tree: Path) -> None:
        server = create_server(course_tree)
        result = await server._tool_manager.call_tool("resolve_topic", {"topic_id": "intro"})
        payload = _extract_text_payload(result)
        data = json.loads(payload)
        assert data["topic_id"] == "intro"
        assert "topic_010_intro" in (data.get("path") or "")

    async def test_call_course_outline(self, course_tree: Path) -> None:
        server = create_server(course_tree)
        result = await server._tool_manager.call_tool(
            "course_outline",
            {"spec_file": "course-specs/course.xml", "language": "en"},
        )
        payload = _extract_text_payload(result)
        data = json.loads(payload)
        assert data["course_name"] == "Course"
        assert data["language"] == "en"

    async def test_call_search_slides_forwards_max_results(self, course_tree: Path) -> None:
        server = create_server(course_tree)
        result = await server._tool_manager.call_tool(
            "search_slides", {"query": "intro", "max_results": 1}
        )
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text_payload(result: object) -> str:
    """Pull the ``.text`` payload out of an MCP tool-call result.

    FastMCP's ``call_tool`` may return either a raw string, a list of
    content objects, or a tuple ``(content, structured)`` depending on
    version.  We accept all three and return the first text chunk.
    """
    if isinstance(result, str):
        return result
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
