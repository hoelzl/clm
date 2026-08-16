"""Security regression tests for MCP tool path containment (S8, refs #798).

Threat model
------------
The MCP client is only semi-trusted: tool arguments are model-generated and
can be steered by prompt injection. Before this fix every path-accepting
handler resolved its argument as ``Path(p)`` when absolute (pass-through, any
location on disk) or ``data_dir / p`` otherwise (unchecked ``..`` traversal).
That gave injected tool calls:

- an arbitrary-file READ into model context (``slides_language_view``,
  ``harvest_trace_show``, spec/validation reads), and
- an arbitrary-file WRITE/DELETE (``slides_normalize`` rewrites,
  ``voiceover_extract`` companions, ``voiceover_inline`` rewrites a deck and
  unlinks its companion, harvest cache writes via an explicit ``cache_root``).

The contract after the fix: every model-supplied path resolves to a location
under the resolved ``data_dir`` (absolute-inside stays legal —
``topic_resolve`` returns absolute paths agents round-trip), or the handler
refuses with a uniform ``{"error": ...}`` JSON naming the boundary. The
default cache root (operator config, ``resolve_cache_root``) is unaffected;
only the explicit ``cache_root`` parameter is contained.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from clm.mcp.tools import (
    handle_course_authoring_rules,
    handle_get_language_view,
    handle_harvest_backfill_dry,
    handle_harvest_cache_list,
    handle_harvest_compare,
    handle_harvest_identify_rev,
    handle_harvest_report,
    handle_harvest_trace_show,
    handle_harvest_transcribe,
    handle_inline_voiceover,
    handle_normalize_slides,
    handle_resolve_topic,
    handle_validate_slides,
)

# --------------------------------------------------------------------------- helpers


def _err(result: str, *, require_boundary: bool = True) -> str:
    """The ``error`` field of a handler JSON result (fails if absent).

    With ``require_boundary=True`` (default) the message must name the
    data-directory boundary — a refusal *because* of containment. A bare
    ``"error" in data`` would also be satisfied by an incidental
    FileNotFoundError (e.g. after a refactor breaks file reads), silently
    un-pinning the security property; demanding the boundary phrase keeps
    each refusal test discriminating.
    """
    data = json.loads(result)
    assert "error" in data, f"expected an error payload, got: {result[:200]}"
    msg = data["error"]
    if require_boundary:
        assert "data directory" in msg or "data_dir" in msg, (
            f"error is not a containment refusal (missing boundary phrase): {msg}"
        )
    return msg


def _data_tree(tmp_path: Path) -> Path:
    data_dir = tmp_path / "course"
    topic = data_dir / "slides" / "module_100_basics" / "topic_010_intro"
    topic.mkdir(parents=True, exist_ok=True)
    (topic / "slides_intro.py").write_text(
        '# %% [markdown]\n# {{ header("Intro", "Intro") }}\n', encoding="utf-8"
    )
    (data_dir / "course-specs").mkdir(exist_ok=True)
    return data_dir


def _outside_sentinels(tmp_path: Path) -> tuple[Path, Path]:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    deck = outside / "secret_deck.py"
    deck.write_text(
        '# %% [markdown]\n# {{ header("Geheim", "Secret") }}\n'
        "# %% voiceover\n# Diese Zeile gehoert nicht in den Kontext.\n",
        encoding="utf-8",
    )
    trace = outside / "trace.jsonl"
    trace.write_text('{"schema": "v1", "kind": "t"}\n', encoding="utf-8")
    return deck, trace


def _deck_needing_normalization(path: Path) -> None:
    """A deck the normalizer actually rewrites (voiceover cell un-annotated)."""
    path.write_text(
        '# %% [markdown]\n# {{ header("Geheim", "Secret") }}\n'
        "# %% voiceover\n# Diese Zeile gehoert nicht in den Kontext.\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------- read exfiltration


class TestReadContainment:
    async def test_language_view_refuses_absolute_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_get_language_view(str(deck), data_dir, language="de")
        assert "Geheim" not in result
        assert "data" in _err(result).lower() or "outside" in _err(result).lower()

    async def test_language_view_refuses_traversal(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        _outside_sentinels(tmp_path)
        result = await handle_get_language_view(
            "../outside/secret_deck.py", data_dir, language="de"
        )
        assert "Geheim" not in result
        _err(result)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-path semantics")
    async def test_language_view_refuses_drive_paths(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        result = await handle_get_language_view("C:/Windows/win.ini", data_dir, language="de")
        _err(result)

    async def test_trace_show_refuses_absolute_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        _, trace = _outside_sentinels(tmp_path)
        result = await handle_harvest_trace_show(str(trace), data_dir)
        assert "entries" not in json.loads(result)
        _err(result)

    async def test_trace_show_refuses_traversal(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        _outside_sentinels(tmp_path)
        result = await handle_harvest_trace_show("../outside/trace.jsonl", data_dir)
        _err(result)

    async def test_sibling_prefix_spoof_refused(self, tmp_path):
        """``course2`` next to ``course`` must not pass a string-prefix check."""
        data_dir = _data_tree(tmp_path)
        sibling = tmp_path / "course2"
        sibling.mkdir()
        deck = sibling / "deck.py"
        deck.write_text('# %% [markdown]\n# {{ header("x", "x") }}\n', encoding="utf-8")
        result = await handle_get_language_view(str(deck), data_dir, language="de")
        _err(result)


# --------------------------------------------------------------------- mutation containment


class TestMutateContainment:
    async def test_normalize_refuses_absolute_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_normalize_slides(str(deck), data_dir)
        _err(result)
        assert "voiceover\n# Diese" in deck.read_text(encoding="utf-8")  # untouched

    async def test_normalize_refuses_traversal_and_leaves_file(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        _deck_needing_normalization(deck)
        before = deck.read_text(encoding="utf-8")
        result = await handle_normalize_slides("../outside/secret_deck.py", data_dir)
        _err(result)
        assert deck.read_text(encoding="utf-8") == before  # NOT rewritten

    async def test_inline_voiceover_refuses_absolute_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_inline_voiceover(str(deck), data_dir, dry_run=True)
        _err(result)

    async def test_validate_refuses_absolute_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_validate_slides(str(deck), data_dir)
        _err(result)


# --------------------------------------------------------------------- harvest family


class TestHarvestContainment:
    async def test_transcribe_refuses_outside_video(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        outside_video = tmp_path / "outside" / "video.mp4"
        outside_video.parent.mkdir(exist_ok=True)
        outside_video.write_bytes(b"\0" * 16)
        result = await handle_harvest_transcribe(str(outside_video), data_dir)
        _err(result)

    async def test_compare_refuses_outside_source_and_target(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_harvest_compare(
            str(deck),
            "slides/module_100_basics/topic_010_intro/slides_intro.py",
            data_dir,
            lang="de",
        )
        _err(result)
        result = await handle_harvest_compare(
            "slides/module_100_basics/topic_010_intro/slides_intro.py",
            str(deck),
            data_dir,
            lang="de",
        )
        _err(result)

    async def test_identify_rev_refuses_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_harvest_identify_rev(
            str(deck), ["../outside/video.mp4"], data_dir, lang="de"
        )
        _err(result)

    async def test_backfill_dry_refuses_outside(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_harvest_backfill_dry(
            str(deck), ["../outside/video.mp4"], data_dir, lang="de"
        )
        # backfill returns {returncode,...} for subprocess failures; a refusal
        # must be an error payload, not a spawned subprocess against outside paths
        data = json.loads(result)
        assert "error" in data or data.get("command", "").find("outside") == -1

    async def test_report_refuses_outside_slides_and_videos(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        # An existing inside video so a pre-fix run gets past part-existence
        # checks — the refusal must come from containment, not a missing file.
        inside_video = data_dir / "v.mp4"
        inside_video.write_bytes(b"\0" * 16)
        inside = "slides/module_100_basics/topic_010_intro/slides_intro.py"
        # slides outside
        r = await handle_harvest_report(str(deck), [str(inside_video)], data_dir, lang="de")
        _err(r)
        # videos outside
        r = await handle_harvest_report(inside, [str(deck)], data_dir, lang="de")
        _err(r)

    async def test_report_refuses_outside_overrides_with_split_pair(self, tmp_path):
        """The override refusals must be containment, not the incidental
        split-twin error a lone stem produces.

        ``slides_intro.py`` (no twin) never reaches the override validation —
        the bundle loader refuses it first with "is not a split deck
        half/stem". A real split pair (``slides_intro.de.py`` +
        ``slides_intro.en.py``) gets past the twin check, so an outside
        transcript/alignment is refused *by containment*.
        """
        data_dir = _data_tree(tmp_path)
        _, trace = _outside_sentinels(tmp_path)
        inside_video = data_dir / "v.mp4"
        inside_video.write_bytes(b"\0" * 16)
        topic = data_dir / "slides" / "module_100_basics" / "topic_010_intro"
        (topic / "slides_intro.de.py").write_text(
            '# %% [markdown]\n# {{ header("Intro", "Intro") }}\n', encoding="utf-8"
        )
        (topic / "slides_intro.en.py").write_text(
            '# %% [markdown]\n# {{ header("Intro", "Intro") }}\n', encoding="utf-8"
        )
        inside = "slides/module_100_basics/topic_010_intro/slides_intro.de.py"
        # transcript override outside
        r = await handle_harvest_report(
            inside, [str(inside_video)], data_dir, lang="de", transcript=str(trace)
        )
        _err(r)
        # alignment override outside
        r = await handle_harvest_report(
            inside, [str(inside_video)], data_dir, lang="de", alignment=str(trace)
        )
        _err(r)

    async def test_cache_list_refuses_outside_cache_root(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        outside_root = tmp_path / "outside" / "cache"
        result = await handle_harvest_cache_list(data_dir, cache_root=str(outside_root))
        _err(result)


# --------------------------------------------------------------------- spec/slug params


class TestSpecParamContainment:
    async def test_authoring_rules_refuses_traversal_slug(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "evil.authoring.md").write_text("# outside rules\n", encoding="utf-8")
        result = await handle_course_authoring_rules(data_dir, course_spec="../outside/evil")
        data = json.loads(result)
        merged = data.get("merged", "")
        assert "outside rules" not in merged
        assert "error" in data

    async def test_authoring_rules_refuses_outside_slide_path(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_course_authoring_rules(data_dir, slide_path=str(deck))
        _err(result)

    async def test_resolve_topic_spec_param_resolves_under_data_dir(self, tmp_path):
        """Pre-fix bug: a relative course_spec resolved against CWD, not data_dir."""
        data_dir = _data_tree(tmp_path)
        spec = data_dir / "course-specs" / "test.xml"
        spec.write_text("<course><sections /></course>", encoding="utf-8")
        import os

        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)  # a same-named spec at CWD level must NOT shadow
            (tmp_path / "test.xml").write_text("<course><sections /></course>", encoding="utf-8")
            result = await handle_resolve_topic(
                "intro", data_dir, course_spec="course-specs/test.xml"
            )
        finally:
            os.chdir(cwd)
        data = json.loads(result)
        assert "error" not in data


# --------------------------------------------------------------------- symlink escapes


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege on Windows CI")
class TestSymlinkContainment:
    async def test_language_view_refuses_path_through_symlink(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        link = data_dir / "linked_deck.py"
        link.symlink_to(deck)
        result = await handle_get_language_view(str(link), data_dir, language="de")
        assert "Geheim" not in result
        _err(result)

    async def test_normalize_refuses_symlinked_dir(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir(exist_ok=True)
        deck = outside / "deck.py"
        _deck_needing_normalization(deck)
        before = deck.read_text(encoding="utf-8")
        vendor = data_dir / "vendor"
        vendor.symlink_to(outside, target_is_directory=True)
        result = await handle_normalize_slides("vendor/deck.py", data_dir)
        _err(result)
        assert deck.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------- positive pins


class TestLegitimateUsePreserved:
    async def test_absolute_inside_data_dir_still_works(self, tmp_path):
        """topic_resolve returns absolute paths; agents round-trip them."""
        data_dir = _data_tree(tmp_path)
        deck = data_dir / "slides" / "module_100_basics" / "topic_010_intro" / "slides_intro.py"
        result = await handle_get_language_view(str(deck), data_dir, language="de")
        assert "error" not in json.loads(result) if result.strip().startswith("{") else True
        assert "Intro" in result or "error" not in result

    async def test_relative_inside_data_dir_still_works(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        result = await handle_get_language_view(
            "slides/module_100_basics/topic_010_intro/slides_intro.py", data_dir, language="de"
        )
        assert "Intro" in result

    async def test_data_dir_itself_allowed_for_validate(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        result = await handle_validate_slides(".", data_dir)
        # a directory target is legitimate; must not be a containment error
        assert json.loads(result).get("error") is None or "slides" in json.loads(result).get(
            "error", ""
        )

    async def test_error_message_names_the_boundary(self, tmp_path):
        data_dir = _data_tree(tmp_path)
        deck, _ = _outside_sentinels(tmp_path)
        result = await handle_get_language_view(str(deck), data_dir, language="de")
        msg = _err(result)
        assert "data" in msg.lower()
