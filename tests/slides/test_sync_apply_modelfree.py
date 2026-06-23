"""``clm slides sync apply`` — deterministic tier-1 apply, never a model (epic #440).

Decision (B): ``apply`` applies only the mechanical tier (move / remove / retag /
neutral propagation / unambiguous id-migration) and treats every model-requiring
item (edit / add / cold-start / ambiguous realign) as **residue** — *deferred*, not
errored, so the watermark holds and the pass exits non-zero. These tests pin both the
engine flag (``apply_plan(deterministic_only=True)``) and the CLI verb, and guard the
contrast that the autopilot/human path (``deterministic_only=False`` with no model)
still treats a missing model as an *error*.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import apply_plan
from clm.slides.sync_plan import build_sync_plan, watermark_rows

# ---------------------------------------------------------------------------
# Deck builders (mirroring the established sync-test shapes)
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _slide_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


def _code_shared(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}\n'


def _pair(tmp_path: Path, de: str, en: str, stem: str = "deck_x") -> tuple[Path, Path]:
    de_path = (tmp_path / f"{stem}.de.py").resolve()
    en_path = (tmp_path / f"{stem}.en.py").resolve()
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slide_ids(path: Path) -> set[str]:
    return {c.metadata.slide_id for c in parse_cells(_text(path)) if c.metadata.slide_id}


def _seed_then_edit(
    tmp_path: Path, de0: str, en0: str, de1: str, en1: str, *, stem: str = "deck_x"
) -> tuple[Path, Path, SyncWatermarkCache]:
    """Seed a watermark at (de0, en0), then write the post-edit (de1, en1) and return
    the still-open cache so an apply can advance it."""
    de_path, en_path = _pair(tmp_path, de0, en0, stem=stem)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    return de_path, en_path, cache


def _model_free_apply(de_path: Path, en_path: Path, cache: SyncWatermarkCache):  # noqa: ANN202
    plan = build_sync_plan(
        de_path, en_path, watermark_cache=cache, provider_available=False, detect_rename=True
    )
    result = apply_plan(
        plan,
        judge=None,
        translator=None,
        recoverer=None,
        verifier=None,
        watermark_cache=cache,
        deterministic_only=True,
    )
    return plan, result


# ---------------------------------------------------------------------------
# Engine: deterministic_only applies tier-1 and DEFERS (never errors) the rest
# ---------------------------------------------------------------------------


class TestEngineDeterministicOnly:
    def test_tier1_remove_applies_clean_no_model(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
            _slide("de", "a", "# ## A"),  # b removed on DE
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        try:
            _plan, result = _model_free_apply(de_path, en_path, cache)
        finally:
            cache.close()
        assert result.applied_remove == 1
        assert result.deferred == 0
        assert result.errors == []
        assert "b" not in _slide_ids(en_path)  # the deterministic remove propagated

    def test_tier1_neutral_propagation_applies_clean_no_model(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A") + _code_shared("x = 1"),
            _slide("en", "a", "# ## A") + _code_shared("x = 1"),
            _slide("de", "a", "# ## A") + _code_shared("x = 2"),  # neutral edit on DE
            _slide("en", "a", "# ## A") + _code_shared("x = 1"),
        )
        try:
            _plan, result = _model_free_apply(de_path, en_path, cache)
        finally:
            cache.close()
        assert result.errors == []
        assert result.deferred == 0
        assert "x = 2" in _text(en_path)  # the verbatim shared-cell change propagated

    def test_tier2_add_defers_without_error(self, tmp_path: Path):
        # A brand-new id-less DE slide → one tier-2 `add` (needs a translator).
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
            _slide("en", "a", "# ## A"),
        )
        en_before = _text(en_path)
        try:
            plan, result = _model_free_apply(de_path, en_path, cache)
        finally:
            cache.close()
        assert plan.count("add") == 1
        assert result.deferred == 1
        assert result.errors == []  # residue, NOT an error
        assert result.applied_add == 0
        assert result.watermark_recorded is False  # a deferred pass holds the watermark
        assert _text(en_path) == en_before  # nothing translated/inserted on the twin

    def test_tier2_edit_defers_without_error(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),  # id'd markdown edit on DE
            _slide("en", "a", "# ## A"),
        )
        en_before = _text(en_path)
        try:
            plan, result = _model_free_apply(de_path, en_path, cache)
        finally:
            cache.close()
        assert plan.count("edit") == 1
        assert result.deferred == 1
        assert result.errors == []
        assert result.applied_edit == 0
        assert _text(en_path) == en_before  # the target half is untouched


class TestEngineAutopilotContrastStillErrors:
    """Guard: the human/autopilot path (deterministic_only=False, no model) keeps the
    old contract — a missing model for a tier-2 item is an ERROR, not silent residue."""

    def test_add_without_translator_errors(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
            _slide("en", "a", "# ## A"),
        )
        try:
            plan = build_sync_plan(
                de_path, en_path, watermark_cache=cache, allow_git_fallback=False
            )
            result = apply_plan(
                plan, judge=None, translator=None, watermark_cache=cache, deterministic_only=False
            )
        finally:
            cache.close()
        assert result.errors  # "add/rename present but no translator available"

    def test_edit_without_judge_errors(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),
            _slide("en", "a", "# ## A"),
        )
        try:
            plan = build_sync_plan(
                de_path, en_path, watermark_cache=cache, allow_git_fallback=False
            )
            result = apply_plan(
                plan, judge=None, translator=None, watermark_cache=cache, deterministic_only=False
            )
        finally:
            cache.close()
        assert result.errors  # "edit ...: no judge (LLM unavailable)"


# ---------------------------------------------------------------------------
# CLI verb (`clm slides sync apply`)
# ---------------------------------------------------------------------------


def _run(*args: str) -> tuple[int, str]:
    from clm.cli.commands.slides.sync import slides_sync_group

    res = CliRunner().invoke(slides_sync_group, list(args))
    return res.exit_code, res.output


class TestApplyCli:
    def test_clean_tier1_exits_zero_and_writes(self, tmp_path: Path, monkeypatch):
        # No API key anywhere: model-free apply must still run.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        cache.close()  # the CLI opens its own cache from --cache-dir
        code, out = _run("apply", str(de_path), "--use-watermark", "--cache-dir", str(tmp_path))
        assert code == 0, out
        assert "b" not in _slide_ids(en_path)

    def test_residue_exits_one_and_leaves_twin_untouched(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
            _slide("en", "a", "# ## A"),
        )
        cache.close()
        en_before = _text(en_path)
        code, out = _run("apply", str(de_path), "--use-watermark", "--cache-dir", str(tmp_path))
        assert code == 1, out
        assert "residue" in out.lower()
        assert _text(en_path) == en_before  # the add was NOT applied (no model)

    def test_residue_json_shape(self, tmp_path: Path):
        de_path, en_path, cache = _seed_then_edit(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
            _slide("en", "a", "# ## A"),
        )
        cache.close()
        code, out = _run(
            "apply", str(de_path), "--use-watermark", "--cache-dir", str(tmp_path), "--json"
        )
        assert code == 1, out
        payload = json.loads(out)
        assert payload["mode"] == "apply"
        assert payload["exit_code"] == 1
        assert payload["apply"]["applied"]["total"] == 0
        kinds = {it["kind"] for it in payload["residue"]}
        assert "add" in kinds

    def test_directory_json_without_yes_errors(self, tmp_path: Path):
        _pair(tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A"))
        code, out = _run("apply", str(tmp_path), "--json")
        assert code == 2, out
        assert "--yes" in out

    def test_directory_batch_applies_each_pair(self, tmp_path: Path):
        # Two pairs, each with a deterministic remove; seed both watermarks.
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        for stem in ("one", "two"):
            de_path, en_path = _pair(
                tmp_path,
                _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
                _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
                stem=stem,
            )
            _seed(cache, de_path, en_path)
            de_path.write_text(_slide("de", "a", "# ## A"), encoding="utf-8")  # remove b
        cache.close()
        code, out = _run(
            "apply", str(tmp_path), "--yes", "--use-watermark", "--cache-dir", str(tmp_path)
        )
        assert code == 0, out
        for stem in ("one", "two"):
            assert "b" not in _slide_ids((tmp_path / f"{stem}.en.py").resolve())
