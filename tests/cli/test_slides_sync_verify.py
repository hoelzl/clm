"""Tests for ``clm slides sync verify`` — the deterministic structural check.

``verify`` answers "did an edit corrupt the split pair?" (structural safety),
NOT "is it in sync?" (``report``) or "is the translation good?" (a semantic
call). It reuses :func:`unify_texts` for byte-identity / header / alignment,
adds an explicit ``de_id == en_id`` set-symmetry + duplicate-id check (which
unify does not enforce), warns on a cross-side tag-set mismatch between paired
cells (Issue #615 — tags are language-independent), and warns on an id'd cell
dropped vs git HEAD. Exit 0 = valid (warnings allowed), 2 = structural
corruption.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from clm.slides.sync_verify import (
    VerifyViolation,
    dropped_id_violations,
    structural_gate,
    structural_violations,
    tag_parity_violations,
    verify_pair,
)

# ---------------------------------------------------------------------------
# Deck builders
# ---------------------------------------------------------------------------


def _md(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _vo(lang: str, sid: str, body: str) -> str:
    """An inline voiceover companion — shares its slide's ``slide_id`` under role ``voiceover``."""
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{sid}"\n{body}\n'


def _md_tags(lang: str, sid: str, tags: list[str], body: str) -> str:
    """A localized markdown cell with an explicit tag list (tag-parity tests)."""
    block = ", ".join(f'"{t}"' for t in tags)
    return f'# %% [markdown] lang="{lang}" tags=[{block}] slide_id="{sid}"\n{body}\n'


def _idless_code(lang: str, tags: list[str], body: str) -> str:
    """An id-less localized code cell — pairs positionally, not by id."""
    block = ", ".join(f'"{t}"' for t in tags)
    return f'# %% lang="{lang}" tags=[{block}]\n{body}\n'


def _shared(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}\n'


def _half(*cells: str) -> str:
    return "\n".join(cells)


def _valid_de() -> str:
    return _half(_md("de", "s1", "Hallo"), _shared("print(1)"))


def _valid_en() -> str:
    return _half(_md("en", "s1", "Hello"), _shared("print(1)"))


def _write(tmp_path: Path, de: str, en: str, *, stem: str = "slides_a") -> tuple[Path, Path]:
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


@pytest.fixture
def cli_runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # Click < 8.2 has no mix_stderr kwarg
        return CliRunner()


# ---------------------------------------------------------------------------
# Unit tests — structural_violations (pure, no IO)
# ---------------------------------------------------------------------------


class TestStructuralViolations:
    def test_valid_pair_clean(self):
        assert structural_violations(_valid_de(), _valid_en(), "#") == []

    def test_divergent_shared_cell_is_unify_error(self):
        de = _half(_md("de", "s1", "Hallo"), _shared("print(1)"))
        en = _half(_md("en", "s1", "Hello"), _shared("print(2)"))  # shared diverges
        vs = structural_violations(de, en, "#")
        assert [v.kind for v in vs] == ["unify"]
        assert vs[0].severity == "error"
        assert "shared cell content diverges" in vs[0].message

    def test_mismatched_slide_id_is_asymmetry(self):
        de = _md("de", "s1", "Hallo")
        en = _md("en", "s2", "Hello")  # different id — unify would NOT catch this
        vs = structural_violations(de, en, "#")
        kinds = {v.kind for v in vs}
        assert kinds == {"id-asymmetry"}
        assert {v.slide_id for v in vs} == {"s1", "s2"}
        assert all(v.severity == "error" for v in vs)

    def test_dropped_twin_is_asymmetry(self):
        # EN keeps both slides; DE lost s2 → s2 in EN but not DE.
        de = _md("de", "s1", "Hallo")
        en = _half(_md("en", "s1", "Hello"), _md("en", "s2", "World"))
        vs = structural_violations(de, en, "#")
        assert [(v.kind, v.slide_id) for v in vs] == [("id-asymmetry", "s2")]

    def test_duplicate_id_within_half(self):
        # Two cells with the same id AND the same role are a true (slide_id, role) collision.
        de = _half(_md("de", "s1", "Hallo"), _md("de", "s1", "Wieder"))
        en = _half(_md("en", "s1", "Hello"), _md("en", "s1", "Again"))
        vs = structural_violations(de, en, "#")
        dups = [v for v in vs if v.kind == "duplicate-id"]
        assert {v.slide_id for v in dups} == {"s1"}
        assert any("DE" in v.message for v in dups)
        assert any("EN" in v.message for v in dups)

    def test_companion_roles_share_id_not_duplicate(self):
        # A slide and its inline voiceover companion legitimately share a slide_id under
        # different roles (the engine keys on (slide_id, role)); that is NOT a duplicate.
        # Regression: keying the check on the bare id wrongly flagged every voiceover deck.
        de = _half(_md("de", "s1", "Hallo"), _vo("de", "s1", "# Sprechtext"))
        en = _half(_md("en", "s1", "Hello"), _vo("en", "s1", "# Voiceover"))
        dups = [v for v in structural_violations(de, en, "#") if v.kind == "duplicate-id"]
        assert dups == []

    def test_idless_cells_excluded_from_symmetry(self):
        # An id-less localized code cell on each side: no id, so no asymmetry.
        de = _half(_md("de", "s1", "Hallo"), '# %% lang="de"\nx = 1\n')
        en = _half(_md("en", "s1", "Hello"), '# %% lang="en"\nx = 1\n')
        assert structural_violations(de, en, "#") == []


class TestTagParityViolations:
    """Issue #615 — the cross-side tag-parity warning (tags are language-independent)."""

    def test_idd_pair_with_mismatched_tags_warns(self):
        # Same (slide_id, role) on both halves, but DE carries an extra tag.
        de = _md_tags("de", "s1", ["slide", "alt"], "Hallo")
        en = _md_tags("en", "s1", ["slide"], "Hello")
        vs = structural_violations(de, en, "#")
        assert [(v.kind, v.severity, v.slide_id) for v in vs] == [("tag-parity", "warning", "s1")]
        msg = vs[0].message
        assert "['alt', 'slide']" in msg  # DE tags, sorted
        assert "['slide']" in msg  # EN tags, sorted
        assert "'s1'" in msg
        assert "language-independent" in msg

    def test_role_changing_tag_edit_is_caught(self):
        # The flagship #615 edit: the DE companion's notes → voiceover. The role is
        # DERIVED from the tags, so the (slide_id, role) keys no longer match; the
        # per-slide positional fallback must still pair and flag the twins.
        de = _half(_md("de", "s1", "Hallo"), _md_tags("de", "s1", ["voiceover"], "# Sprechtext"))
        en = _half(_md("en", "s1", "Hello"), _md_tags("en", "s1", ["notes"], "# Voiceover"))
        vs = structural_violations(de, en, "#")
        assert [(v.kind, v.severity, v.slide_id) for v in vs] == [("tag-parity", "warning", "s1")]
        assert "['voiceover']" in vs[0].message
        assert "['notes']" in vs[0].message

    def test_matching_tags_in_different_order_is_clean(self):
        # Tag parity compares SETS — serialization order is not doctrine.
        de = _md_tags("de", "s1", ["alt", "slide"], "Hallo")
        en = _md_tags("en", "s1", ["slide", "alt"], "Hello")
        assert structural_violations(de, en, "#") == []

    def test_idless_positional_mismatch_warns(self):
        # Id-less localized code pairs positionally within the id-less remainder.
        de = _half(_md("de", "s1", "Hallo"), _idless_code("de", ["keep"], "x = 1"))
        en = _half(_md("en", "s1", "Hello"), _idless_code("en", ["alt"], "x = 1"))
        vs = tag_parity_violations(de, en, "#")
        assert [(v.kind, v.severity, v.slide_id) for v in vs] == [("tag-parity", "warning", None)]
        assert "id-less cell #1" in vs[0].message
        assert "['keep']" in vs[0].message
        assert "['alt']" in vs[0].message

    def test_idless_remainder_length_mismatch_is_silent(self):
        # DE has two id-less cells, EN one: the positional part is skipped silently
        # (a count mismatch is a structural concern owned by the unify check).
        de = _half(
            _md("de", "s1", "Hallo"),
            _idless_code("de", ["keep"], "x = 1"),
            _idless_code("de", ["alt"], "y = 2"),
        )
        en = _half(_md("en", "s1", "Hello"), _idless_code("en", ["other"], "x = 1"))
        assert tag_parity_violations(de, en, "#") == []

    def test_missing_twin_is_not_a_tag_question(self):
        # s2 exists only in EN: no pair to compare — id-asymmetry owns that state.
        de = _md("de", "s1", "Hallo")
        en = _half(_md("en", "s1", "Hello"), _md_tags("en", "s2", ["slide", "alt"], "World"))
        assert tag_parity_violations(de, en, "#") == []

    def test_gate_is_neutral_to_tag_parity(self):
        # Warning severity by design: the write gate (error subset) must not refuse
        # to record a pair over a tag mismatch the apply pass is reconciling.
        de = _md_tags("de", "s1", ["slide", "alt"], "Hallo")
        en = _md_tags("en", "s1", ["slide"], "Hello")
        assert structural_gate(de, en, "#") == []
        assert structural_gate(de, en, "#", slide_id="s1") == []


class TestStructuralGate:
    """Issue #455 — the reusable structural write-gate (whole-deck + scoped)."""

    def test_valid_pair_is_safe_to_record(self):
        # Empty list == "safe to record".
        assert structural_gate(_valid_de(), _valid_en(), "#") == []

    def test_whole_deck_is_the_error_subset_of_verify(self, tmp_path):
        # The gate and the CLI verify must not drift: the gate over in-memory text
        # equals the error-severity violations verify_pair surfaces on the same pair.
        de = _md("de", "s1", "Hallo")
        en = _md("en", "s2", "Hello")  # id-asymmetry on both s1 and s2
        de_path, en_path = _write(tmp_path, de, en)
        gate = structural_gate(de, en, "#")
        verify_errors = verify_pair(de_path, en_path).errors
        assert [(v.kind, v.slide_id, v.message) for v in gate] == [
            (v.kind, v.slide_id, v.message) for v in verify_errors
        ]
        assert gate  # non-empty (it is a real corruption)

    def test_whole_deck_surfaces_unify_error(self):
        de = _half(_md("de", "s1", "Hallo"), _shared("print(1)"))
        en = _half(_md("en", "s1", "Hello"), _shared("print(2)"))  # shared diverges
        assert [v.kind for v in structural_gate(de, en, "#")] == ["unify"]

    def test_scoped_ignores_a_corruption_under_another_slide(self):
        # s2 is dropped from DE (id-asymmetry on s2). Recording s1 must stay safe.
        de = _md("de", "s1", "Hallo")
        en = _half(_md("en", "s1", "Hello"), _md("en", "s2", "World"))
        assert structural_gate(de, en, "#", slide_id="s1") == []
        scoped_s2 = structural_gate(de, en, "#", slide_id="s2")
        assert [(v.kind, v.slide_id) for v in scoped_s2] == [("id-asymmetry", "s2")]

    def test_scoped_asymmetry_is_role_agnostic(self):
        # id-asymmetry carries no role, so it applies to the slide under any role filter.
        de = _md("de", "s1", "Hallo")
        en = _half(_md("en", "s1", "Hello"), _md("en", "s2", "World"))
        scoped = structural_gate(de, en, "#", slide_id="s2", role="slide")
        assert [(v.kind, v.slide_id) for v in scoped] == [("id-asymmetry", "s2")]

    def test_scoped_duplicate_is_role_aware(self):
        # (s1, voiceover) is duplicated; (s1, slide) is unique. Recording the slide is
        # safe; recording the voiceover companion is not.
        de = _half(_md("de", "s1", "Hallo"), _vo("de", "s1", "# a"), _vo("de", "s1", "# b"))
        en = _half(_md("en", "s1", "Hello"), _vo("en", "s1", "# a"), _vo("en", "s1", "# b"))
        # sanity: the only errors are the (s1, voiceover) dups, one per half.
        whole = structural_gate(de, en, "#")
        assert {(v.kind, v.role) for v in whole} == {("duplicate-id", "voiceover")}
        assert structural_gate(de, en, "#", slide_id="s1", role="slide") == []
        vo_scoped = structural_gate(de, en, "#", slide_id="s1", role="voiceover")
        assert [v.kind for v in vo_scoped] == ["duplicate-id", "duplicate-id"]
        # role=None covers every role under the slide_id.
        any_role = structural_gate(de, en, "#", slide_id="s1")
        assert [v.kind for v in any_role] == ["duplicate-id", "duplicate-id"]

    def test_scoped_excludes_whole_deck_unify(self):
        # The byte-identity oracle (unify) has no slide_id, so it is whole-deck only:
        # a scoped call relies on the id symmetry/uniqueness checks (sufficient because
        # an id'd cell is localized — byte-identity governs only id-less neutral cells).
        de = _half(_md("de", "s1", "Hallo"), _shared("print(1)"))
        en = _half(_md("en", "s1", "Hello"), _shared("print(2)"))
        assert structural_gate(de, en, "#", slide_id="s1") == []
        assert [v.kind for v in structural_gate(de, en, "#")] == ["unify"]


class TestDroppedIdViolations:
    def test_flags_gone_id(self):
        head = _half(_md("de", "s1", "a"), _md("de", "s2", "b"))
        current = _md("de", "s1", "a")  # s2 removed
        vs = dropped_id_violations(head, current, "#", "DE")
        assert [(v.kind, v.slide_id, v.severity) for v in vs] == [("dropped-id", "s2", "warning")]

    def test_no_drop_is_clean(self):
        head = _md("de", "s1", "a")
        assert dropped_id_violations(head, head, "#", "DE") == []


# ---------------------------------------------------------------------------
# CLI tests — single pair (untracked → no-drop check skipped)
# ---------------------------------------------------------------------------


class TestVerifyCli:
    def test_valid_pair_passes(self, cli_runner, tmp_path):
        de_path, _en = _write(tmp_path, _valid_de(), _valid_en())
        res = cli_runner.invoke(slides_sync_group, ["verify", str(de_path)])
        assert res.exit_code == 0, res.output
        assert "PASS" in res.output

    def test_divergent_shared_fails(self, cli_runner, tmp_path):
        de = _half(_md("de", "s1", "Hallo"), _shared("print(1)"))
        en = _half(_md("en", "s1", "Hello"), _shared("print(2)"))
        de_path, _en = _write(tmp_path, de, en)
        res = cli_runner.invoke(slides_sync_group, ["verify", str(de_path)])
        assert res.exit_code == 2
        assert "FAIL" in res.output
        assert "shared cell content diverges" in res.output

    def test_mismatched_id_fails(self, cli_runner, tmp_path):
        de_path, _en = _write(tmp_path, _md("de", "s1", "Hallo"), _md("en", "s2", "Hello"))
        res = cli_runner.invoke(slides_sync_group, ["verify", str(de_path)])
        assert res.exit_code == 2
        assert "id-asymmetry" in res.output

    def test_json_output(self, cli_runner, tmp_path):
        de_path, _en = _write(tmp_path, _md("de", "s1", "Hallo"), _md("en", "s2", "Hello"))
        res = cli_runner.invoke(slides_sync_group, ["verify", "--json", str(de_path)])
        payload = json.loads(res.output[res.output.find("{") :])
        assert payload["mode"] == "verify"
        assert payload["exit_code"] == 2
        assert len(payload["pairs"]) == 1
        pair = payload["pairs"][0]
        assert pair["ok"] is False
        assert {v["kind"] for v in pair["violations"]} == {"id-asymmetry"}

    def test_tag_parity_warning_passes_and_renders(self, cli_runner, tmp_path):
        # A tag mismatch is a warning: surfaced in the output, exit code unaffected.
        de = _md_tags("de", "s1", ["slide", "alt"], "Hallo")
        en = _md_tags("en", "s1", ["slide"], "Hello")
        de_path, _en = _write(tmp_path, de, en)
        res = cli_runner.invoke(slides_sync_group, ["verify", str(de_path)])
        assert res.exit_code == 0, res.output
        assert "PASS" in res.output
        assert "1 warning" in res.output
        assert "warning [tag-parity]" in res.output
        assert "['alt', 'slide']" in res.output
        assert "['slide']" in res.output

    def test_tag_parity_warning_in_json(self, cli_runner, tmp_path):
        de = _md_tags("de", "s1", ["slide", "alt"], "Hallo")
        en = _md_tags("en", "s1", ["slide"], "Hello")
        de_path, _en = _write(tmp_path, de, en)
        res = cli_runner.invoke(slides_sync_group, ["verify", "--json", str(de_path)])
        payload = json.loads(res.output[res.output.find("{") :])
        assert payload["exit_code"] == 0
        pair = payload["pairs"][0]
        assert pair["ok"] is True  # a warning never fails the gate
        violations = pair["violations"]
        assert [(v["kind"], v["severity"], v["slide_id"]) for v in violations] == [
            ("tag-parity", "warning", "s1")
        ]
        assert "language-independent" in violations[0]["message"]

    def test_single_half_resolves_twin(self, cli_runner, tmp_path):
        # Passing only the .de half resolves the .en twin from disk.
        _write(tmp_path, _valid_de(), _valid_en())
        res = cli_runner.invoke(slides_sync_group, ["verify", str(tmp_path / "slides_a.de.py")])
        assert res.exit_code == 0, res.output


class TestVerifyBatch:
    def test_directory_sweep(self, cli_runner, tmp_path):
        _write(tmp_path, _valid_de(), _valid_en(), stem="slides_ok")
        _write(tmp_path, _md("de", "s1", "Hallo"), _md("en", "s2", "Hello"), stem="slides_bad")
        res = cli_runner.invoke(slides_sync_group, ["verify", str(tmp_path)])
        assert res.exit_code == 2  # worst over pairs
        assert "verified 2 pair(s)" in res.output
        assert "1 valid" in res.output

    def test_directory_all_valid(self, cli_runner, tmp_path):
        _write(tmp_path, _valid_de(), _valid_en(), stem="slides_x")
        _write(tmp_path, _valid_de(), _valid_en(), stem="slides_y")
        res = cli_runner.invoke(slides_sync_group, ["verify", str(tmp_path)])
        assert res.exit_code == 0, res.output


class TestVerifyJsonFlag:
    def test_json_is_allowed(self, cli_runner, tmp_path):
        de_path, _en = _write(tmp_path, _valid_de(), _valid_en())
        res = cli_runner.invoke(slides_sync_group, ["verify", "--json", str(de_path)])
        assert res.exit_code == 0, res.output


# ---------------------------------------------------------------------------
# git-backed: the no-drop warning vs HEAD (needs a real repo)
# ---------------------------------------------------------------------------


def _commit(tmp_path: Path, de: str, en: str, *, stem: str = "slides_a") -> tuple[Path, Path]:
    de_path, en_path = _write(tmp_path, de, en, stem=stem)

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(tmp_path), check=True, capture_output=True, text=True
        )

    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "Test")
    _git("add", "-A")
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    return de_path, en_path


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestNoDropVsGit:
    def test_symmetric_removal_warns_but_passes(self, cli_runner, tmp_path):
        # Commit a 2-slide pair, then remove s2 from BOTH halves (symmetric → still
        # structurally valid). The no-drop check warns, but a warning does not fail.
        de = _half(_md("de", "s1", "a"), _md("de", "s2", "b"))
        en = _half(_md("en", "s1", "A"), _md("en", "s2", "B"))
        de_path, en_path = _commit(tmp_path, de, en)
        de_path.write_text(_md("de", "s1", "a"), encoding="utf-8")
        en_path.write_text(_md("en", "s1", "A"), encoding="utf-8")

        res = cli_runner.invoke(slides_sync_group, ["verify", "--json", str(de_path)])
        payload = json.loads(res.output[res.output.find("{") :])
        pair = payload["pairs"][0]
        assert pair["git_baseline"] is True
        assert pair["ok"] is True  # warnings do not fail the gate
        assert res.exit_code == 0
        dropped = [v for v in pair["violations"] if v["kind"] == "dropped-id"]
        assert {v["slide_id"] for v in dropped} == {"s2"}

    def test_verify_pair_git_baseline_flag(self, tmp_path):
        de_path, en_path = _commit(tmp_path, _valid_de(), _valid_en())
        result = verify_pair(de_path, en_path)
        assert result.ok
        assert result.git_baseline is True
        assert result.violations == []


def test_violation_is_frozen():
    v = VerifyViolation(severity="error", kind="unify", message="x")
    with pytest.raises(Exception):
        v.severity = "warning"  # type: ignore[misc]
