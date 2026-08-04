"""Base-diff recovery for framed translation rows — #773 phase 1.

A ``verify_translation`` row asserts *both halves moved off base*, and the
reader's measured cost is re-deriving what changed by comparing two full cells
by eye (68.4% of all framed rows on the reference repo). The ledger stores
per-side fingerprints — it can *recognize* the base anywhere but cannot
*reproduce* it — so ``base_recovery`` walks the deck's recent change-commits,
finds the newest one whose bytes the fingerprints recognize, and ships
per-side unified diffs (``base_ref`` / ``de_diff`` / ``en_diff``) in the item
payload, plus one ``verify_translation_batch`` observation when every such row
diverges from the same recovered base.

Pinned here: exact recovery (the match is the recorded state, not a guess),
every degrade path (no git, base never committed, cap exhausted, broken
intermediate commits), the newest-match semantics the batch grouping rides on,
the no-overclaim rules of the batch observation, and that both surfaces (JSON
payload, human report) carry the result. Design note:
``docs/claude/design/sync-verify-translation-ceremony.md``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import attrs
import pytest

from clm.slides.base_recovery import (
    batch_observation,
    recover_base_diffs,
)
from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import LoadedBundle, load_bundle, parse_bundle
from clm.slides.git_text import recent_change_refs
from clm.slides.sync_diff import DeckBaseline, DeckDiff, baseline_from_deck, diff_outcome
from clm.slides.sync_wire import WIRE_SCHEMA

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


DE0 = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _localized("m1", "de", "DE eins"),
    _localized("m2", "de", "DE zwei"),
    _localized("m3", "de", "DE drei"),
    _localized("m4", "de", "DE vier"),
)
EN0 = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _localized("m1", "en", "EN one"),
    _localized("m2", "en", "EN two"),
    _localized("m3", "en", "EN three"),
    _localized("m4", "en", "EN four"),
)


def _edit(text: str, *fragments: str) -> str:
    """Drift the named member bodies (``"DE eins"`` → ``"DE eins, neu"``)."""
    for fragment in fragments:
        text = text.replace(fragment, f"{fragment}, neu")
    return text


class _Repo:
    """A split pair in a throwaway git repo, with committed states."""

    def __init__(self, root: Path, *, git: bool = True):
        self.root = root
        root.mkdir(exist_ok=True)
        if git:
            _git(root, "init", "-q")
        self.folder = root / "t"
        self.folder.mkdir(exist_ok=True)
        self.de = self.folder / "slides_t.de.py"
        self.en = self.folder / "slides_t.en.py"
        self.git = git

    def write(self, de: str, en: str) -> None:
        self.de.write_text(de, encoding="utf-8")
        self.en.write_text(en, encoding="utf-8")

    def commit(self, msg: str = "c") -> str:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", msg)
        return _git(self.root, "rev-parse", "HEAD")

    def bundle(self) -> LoadedBundle:
        return load_bundle(self.de, self.en)


def _parse(de: str, en: str) -> BilingualDeck:
    outcome = parse_bundle(de, en)
    assert outcome.deck is not None, outcome.refusal.render() if outcome.refusal else "parse failed"
    return outcome.deck


def _snapshot(de: str, en: str) -> DeckBaseline:
    return baseline_from_deck(_parse(de, en))


def _diff(repo: _Repo, base: DeckBaseline) -> tuple[LoadedBundle, DeckDiff]:
    bundle = repo.bundle()
    return bundle, diff_outcome(bundle.outcome, base)


def _repo_at_base(tmp_path: Path) -> tuple[_Repo, str, DeckBaseline]:
    repo = _Repo(tmp_path / "repo")
    repo.write(DE0, EN0)
    sha = repo.commit("base")
    return repo, sha, _snapshot(DE0, EN0)


class TestRecovery:
    def test_recovers_the_base_and_renders_per_side_hunks(self, tmp_path: Path):
        """The core promise: both halves moved, and the reader gets two hunks
        against exactly the recorded state instead of two full cells."""
        repo, sha, base = _repo_at_base(tmp_path)
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one"))
        bundle, diff = _diff(repo, base)

        assert [i.action for i in diff.items] == ["verify_translation"]
        recovered = recover_base_diffs(bundle, diff)

        assert set(recovered) == {"id:m1"}
        entry = recovered["id:m1"]
        assert entry.base_ref == sha
        assert "-# DE eins" in entry.de_diff and "+# DE eins, neu" in entry.de_diff
        assert "-# EN one" in entry.en_diff and "+# EN one, neu" in entry.en_diff
        # Hunks, not files: the ---/+++ header lines are dropped.
        assert not entry.de_diff.startswith("---")

    def test_translate_edit_rides_along_and_the_unmoved_side_is_empty(self, tmp_path: Path):
        """§7 decision: the same walk serves ``translate_edit``. The unmoved
        side's diff is ``""`` — a positive statement of byte-identity to base,
        not an absence."""
        repo, sha, base = _repo_at_base(tmp_path)
        repo.write(DE0, _edit(EN0, "EN two"))
        bundle, diff = _diff(repo, base)

        assert [i.action for i in diff.items] == ["translate_edit"]
        entry = recover_base_diffs(bundle, diff)["id:m2"]
        assert entry.base_ref == sha
        assert entry.de_diff == ""
        assert "+# EN two, neu" in entry.en_diff

    def test_a_one_sided_fingerprint_match_is_not_a_match(self, tmp_path: Path):
        """Review M1: the exact-on-BOTH-sides invariant is the feature's central
        safety property. A ref where only one side sits at base (the other
        already edited there) must be skipped, not half-matched."""
        repo, sha_a, base = _repo_at_base(tmp_path)
        repo.write(DE0, _edit(EN0, "EN one"))  # en moved, de still at base
        sha_b = repo.commit("en-only edit")
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one", "EN one, neu"))
        bundle, diff = _diff(repo, base)

        assert [i.action for i in diff.items] == ["verify_translation"]
        entry = recover_base_diffs(bundle, diff)["id:m1"]
        assert entry.base_ref == sha_a, f"must skip {sha_b[:12]} (en already moved there)"
        assert "-# EN one\n" in entry.en_diff + "\n"
        assert "+# EN one, neu, neu" in entry.en_diff

    def test_a_fingerprint_lookalike_under_another_id_cannot_steal_the_base(self, tmp_path: Path):
        """Review F1: fingerprints are modulo ``slide_id``, so a member whose
        bytes are another member's old bytes under its own id (copy-pasted
        boilerplate) matches the fingerprint pair. Taking it would recover at
        the wrong (newer) ref and fabricate an id-rename hunk that never
        happened — the exact unread-divergence noise this feature removes."""
        repo, sha_s, base = _repo_at_base(tmp_path)
        lookalike = _build(
            _edit(DE0, "DE eins"),
            _localized("m9", "de", "DE eins"),  # m1's ORIGINAL bytes, id m9
        )
        lookalike_en = _build(
            _edit(EN0, "EN one"),
            _localized("m9", "en", "EN one"),
        )
        repo.write(lookalike, lookalike_en)
        sha_t = repo.commit("m1 edited; m9 added with m1's old bytes")
        repo.write(
            _build(_edit(DE0, "DE eins", "DE eins, neu"), _localized("m9", "de", "DE eins")),
            _build(_edit(EN0, "EN one", "EN one, neu"), _localized("m9", "en", "EN one")),
        )
        bundle, diff = _diff(repo, base)

        entry = recover_base_diffs(bundle, diff)["id:m1"]
        assert entry.base_ref == sha_s, f"the lookalike at {sha_t[:12]} must not steal the match"
        assert "m9" not in entry.de_diff and "m9" not in entry.en_diff

    def test_the_newest_matching_ref_wins(self, tmp_path: Path):
        """A sibling's later commit keeps the target member at base, so the
        member recovers at the *newest* such commit — the semantics the batch
        observation's same-ref grouping relies on."""
        repo, sha_a, base = _repo_at_base(tmp_path)
        repo.write(_edit(DE0, "DE vier"), _edit(EN0, "EN four"))
        sha_b = repo.commit("sibling edit")
        repo.write(_edit(DE0, "DE vier", "DE eins"), _edit(EN0, "EN four", "EN one"))
        bundle, diff = _diff(repo, base)

        recovered = recover_base_diffs(bundle, diff)
        assert recovered["id:m1"].base_ref == sha_b  # still at base there
        assert recovered["id:m4"].base_ref == sha_a  # already moved in sha_b

    def test_explicit_candidates_replace_the_walk(self, tmp_path: Path):
        """The ``--since REF`` path: the caller names the base commit, and the
        walk must not second-guess it."""
        repo, sha_a, base = _repo_at_base(tmp_path)
        repo.write(_edit(DE0, "DE vier"), _edit(EN0, "EN four"))
        repo.commit("newer commit where m1 is also at base")
        repo.write(_edit(DE0, "DE vier", "DE eins"), _edit(EN0, "EN four", "EN one"))
        bundle, diff = _diff(repo, base)

        recovered = recover_base_diffs(bundle, diff, candidates=[sha_a])
        assert recovered["id:m1"].base_ref == sha_a
        # m4's base state also exists at sha_a, so the named commit serves it too.
        assert recovered["id:m4"].base_ref == sha_a

    def test_a_clean_or_cold_diff_runs_no_git_at_all(self, tmp_path: Path):
        """No target rows → no walk. Pinned via a repo-less directory: if
        recovery consulted git anyway, this would still be empty, so assert on
        the diff shape too — a cold deck (no base) frames nothing recoverable."""
        repo = _Repo(tmp_path / "plain", git=False)
        repo.write(DE0, EN0)
        bundle = repo.bundle()
        diff = diff_outcome(bundle.outcome, None)
        assert diff.items, "a cold deck must frame items"
        assert all(i.base is None for i in diff.items)
        assert recover_base_diffs(bundle, diff) == {}


class TestDegrade:
    def test_no_git_repo_degrades_to_absence(self, tmp_path: Path):
        repo = _Repo(tmp_path / "plain", git=False)
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one"))
        bundle, diff = _diff(repo, _snapshot(DE0, EN0))
        assert [i.action for i in diff.items] == ["verify_translation"]
        assert recover_base_diffs(bundle, diff) == {}

    def test_a_base_that_was_never_committed_degrades(self, tmp_path: Path):
        """`record` runs pre-commit, so a recorded state can exist in no commit
        at all (the `confirmed_commit` docstring's warning). Recovery must
        return nothing rather than the nearest look-alike."""
        repo, _sha, _ = _repo_at_base(tmp_path)
        intermediate_de, intermediate_en = _edit(DE0, "DE eins"), _edit(EN0, "EN one")
        base = _snapshot(intermediate_de, intermediate_en)  # never committed
        repo.write(_edit(intermediate_de, "DE eins, neu"), _edit(intermediate_en, "EN one, neu"))
        bundle, diff = _diff(repo, base)

        assert [i.action for i in diff.items] == ["verify_translation"]
        assert recover_base_diffs(bundle, diff) == {}

    def test_the_cap_bounds_the_walk(self, tmp_path: Path):
        """A base pushed past the cap degrades; a wider cap finds it."""
        repo, sha, base = _repo_at_base(tmp_path)
        de, en = DE0, EN0
        for i in range(4):
            de, en = _edit(de, "DE eins"), _edit(en, "EN one")
            repo.write(de, en)
            repo.commit(f"churn {i}")
        repo.write(_edit(de, "DE eins"), _edit(en, "EN one"))
        bundle, diff = _diff(repo, base)
        assert [i.action for i in diff.items] == ["verify_translation"]

        assert recover_base_diffs(bundle, diff, cap=3) == {}
        assert recover_base_diffs(bundle, diff, cap=10)["id:m1"].base_ref == sha

    def test_a_broken_intermediate_commit_is_skipped_not_fatal(self, tmp_path: Path):
        """A commit whose bundle refuses to parse (here: duplicate slide_id)
        must not end the walk — older valid bases are still recoverable."""
        repo, sha, base = _repo_at_base(tmp_path)
        broken = DE0 + _localized("m2", "de", "duplicate id")
        repo.write(broken, EN0)
        repo.commit("broken state")
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one"))
        bundle, diff = _diff(repo, base)

        assert [i.action for i in diff.items] == ["verify_translation"]
        assert recover_base_diffs(bundle, diff)["id:m1"].base_ref == sha

    def test_recent_change_refs_is_empty_outside_a_repo(self, tmp_path: Path):
        plain = tmp_path / "nowhere"
        plain.mkdir()
        de, en = plain / "slides_t.de.py", plain / "slides_t.en.py"
        assert recent_change_refs(de, en, cap=30) == []

    def test_a_non_utf8_commit_in_the_window_is_skipped_not_fatal(self, tmp_path: Path):
        """Review F2: a historical latin-1 blob (German umlauts, legacy commit)
        must degrade — mis-decoded text fails the match and the walk moves on.
        Strict decoding used to raise ``UnicodeDecodeError`` out of the report
        verb on POSIX (subprocess decodes in the main thread there)."""
        repo, sha, base = _repo_at_base(tmp_path)
        repo.de.write_bytes("# %% [markdown]\n# über\n".encode("latin-1"))
        repo.commit("legacy latin-1 state")
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one"))
        bundle, diff = _diff(repo, base)

        assert [i.action for i in diff.items] == ["verify_translation"]
        recovered = recover_base_diffs(bundle, diff)  # must not raise
        assert recovered["id:m1"].base_ref == sha


class TestBatchObservation:
    def test_fires_when_every_row_shares_one_recovered_base(self, tmp_path: Path):
        repo, sha, base = _repo_at_base(tmp_path)
        repo.write(
            _edit(DE0, "DE eins", "DE zwei", "DE drei"),
            _edit(EN0, "EN one", "EN two", "EN three"),
        )
        bundle, diff = _diff(repo, base)
        assert [i.action for i in diff.items] == ["verify_translation"] * 3

        obs = batch_observation(diff, recover_base_diffs(bundle, diff))
        assert obs is not None
        assert obs.kind == "verify_translation_batch"
        assert "all 3 verify_translation rows" in obs.detail
        assert sha[:12] in obs.detail
        # §4 of the design note, verbatim intent: aggregation must never read
        # as a resolution path.
        assert "no batch answer" in obs.detail

    def test_below_the_floor_stays_quiet(self, tmp_path: Path):
        repo, _sha, base = _repo_at_base(tmp_path)
        repo.write(_edit(DE0, "DE eins", "DE zwei"), _edit(EN0, "EN one", "EN two"))
        bundle, diff = _diff(repo, base)
        assert len(diff.items) == 2
        assert batch_observation(diff, recover_base_diffs(bundle, diff)) is None

    def test_translate_edit_rows_do_not_count_toward_the_floor(self, tmp_path: Path):
        """The observation is a claim about ``verify_translation`` rows; a
        riding ``translate_edit`` must neither count nor block."""
        repo, _sha, base = _repo_at_base(tmp_path)
        repo.write(_edit(DE0, "DE eins", "DE zwei"), _edit(EN0, "EN one", "EN two", "EN three"))
        bundle, diff = _diff(repo, base)
        actions = sorted(i.action for i in diff.items)
        assert actions == ["translate_edit", "verify_translation", "verify_translation"]
        assert batch_observation(diff, recover_base_diffs(bundle, diff)) is None

    def test_a_row_that_did_not_recover_suppresses_the_summary(self, tmp_path: Path):
        """A claim about "all N" that covers fewer than N is a false summary —
        the ``uniform_drift_side`` doctrine, applied here."""
        repo, _sha, base = _repo_at_base(tmp_path)
        # m3's recorded state is one no commit contains ("record" runs
        # pre-commit); body fps go bogus too, or the tags-only short-circuit
        # would reclassify the row.
        bogus = "0" * 64
        entry = base.members["id:m3"]
        base = attrs.evolve(
            base,
            members={
                **base.members,
                "id:m3": attrs.evolve(
                    entry, de_fp=bogus, en_fp=bogus, de_body_fp=bogus, en_body_fp=bogus
                ),
            },
        )
        repo.write(
            _edit(DE0, "DE eins", "DE zwei", "DE drei"),
            _edit(EN0, "EN one", "EN two", "EN three"),
        )
        bundle, diff = _diff(repo, base)
        assert [i.action for i in diff.items] == ["verify_translation"] * 3

        recovered = recover_base_diffs(bundle, diff)
        assert set(recovered) == {"id:m1", "id:m2"}  # m3's "base" exists nowhere
        assert batch_observation(diff, recovered) is None

    def test_rows_from_different_sync_points_produce_no_summary(self, tmp_path: Path):
        """Members recorded at different times diverge from different bases —
        there is no single editing session to report."""
        repo, _sha_a, base_a = _repo_at_base(tmp_path)
        state_b_de = _edit(DE0, "DE eins", "DE zwei", "DE drei")
        state_b_en = _edit(EN0, "EN one", "EN two", "EN three")
        repo.write(state_b_de, state_b_en)
        repo.commit("state b")
        base_b = _snapshot(state_b_de, state_b_en)
        # m1's entry from state A, m2/m3's entries from state B.
        base = attrs.evolve(base_b, members={**base_b.members, "id:m1": base_a.members["id:m1"]})
        repo.write(
            _edit(state_b_de, "DE eins, neu", "DE zwei, neu", "DE drei, neu"),
            _edit(state_b_en, "EN one, neu", "EN two, neu", "EN three, neu"),
        )
        bundle, diff = _diff(repo, base)
        assert [i.action for i in diff.items] == ["verify_translation"] * 3

        recovered = recover_base_diffs(bundle, diff)
        assert len(recovered) == 3
        assert len({entry.base_ref for entry in recovered.values()}) == 2
        assert batch_observation(diff, recovered) is None


class TestSurfaces:
    def test_the_json_payload_carries_the_fields_and_the_observation(self, tmp_path: Path):
        from clm.slides.doc_report import pair_payload

        repo, sha, base = _repo_at_base(tmp_path)
        repo.write(
            _edit(DE0, "DE eins", "DE zwei", "DE drei"),
            _edit(EN0, "EN one", "EN two", "EN three"),
        )
        bundle, diff = _diff(repo, base)
        payload = pair_payload(bundle, diff, base_diffs=recover_base_diffs(bundle, diff))

        assert payload["schema"] == WIRE_SCHEMA == 5
        rows = [i for i in payload["items"] if i["action"] == "verify_translation"]
        assert len(rows) == 3
        for row in rows:
            assert row["base_ref"] == sha
            assert "@@" in row["de_diff"] and "@@" in row["en_diff"]
        batches = [o for o in payload["observations"] if o["kind"] == "verify_translation_batch"]
        assert len(batches) == 1
        assert batches[0]["member"] is None and batches[0]["side"] is None

    def test_an_unrecovered_row_ships_exactly_as_before(self, tmp_path: Path):
        """Absence is the degrade: no ``base_ref``/``*_diff`` keys, full cells
        untouched — a consumer of the old shape sees the old shape."""
        from clm.slides.doc_report import pair_payload

        repo = _Repo(tmp_path / "plain", git=False)
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one"))
        bundle, diff = _diff(repo, _snapshot(DE0, EN0))
        payload = pair_payload(bundle, diff, base_diffs=recover_base_diffs(bundle, diff))

        (row,) = [i for i in payload["items"] if i["action"] == "verify_translation"]
        assert "base_ref" not in row and "de_diff" not in row and "en_diff" not in row
        assert row["de"] and row["en"]  # the full cells still ship
        assert not any(o["kind"] == "verify_translation_batch" for o in payload["observations"])

    def test_the_human_report_renders_hunks_inline_and_the_batch_line(self, tmp_path: Path):
        """§7's rendering decision: inline hunks, no flag — reading two full
        cells is the measured cost, and a hidden diff would not collapse it."""
        from clm.cli.commands.slides import sync_v3

        repo, sha, base = _repo_at_base(tmp_path)
        repo.write(
            _edit(DE0, "DE eins", "DE zwei", "DE drei"),
            _edit(EN0, "EN one", "EN two", "EN three"),
        )
        bundle, diff = _diff(repo, base)
        recovered = recover_base_diffs(bundle, diff)

        text = sync_v3._render_pair(bundle, diff, recovered)
        assert f"de vs base {sha[:12]}:" in text
        assert "-# DE eins" in text and "+# DE eins, neu" in text
        assert "observation/verify_translation_batch" in text
        # The summary prints after the items it summarizes.
        lines = text.splitlines()
        batch_at = next(i for i, ln in enumerate(lines) if "verify_translation_batch" in ln)
        last_item = max(
            i for i, ln in enumerate(lines) if ln.startswith("  conflict/verify_translation")
        )
        assert batch_at > last_item

        # Backward compatible: no recovery argument, no new output.
        bare = sync_v3._render_pair(bundle, diff)
        assert "vs base" not in bare and "verify_translation_batch" not in bare

    def test_aspect_rows_sharing_the_key_are_not_enriched(self, tmp_path: Path):
        """Review F3/M4: a member's mechanical aspect row (here ``mirror_tags``)
        shares the item key with its ``verify_translation`` row. Enrichment is
        a claim about the recovered actions only — the aspect row must carry no
        base fields, and the text report must print the hunks exactly once."""
        from clm.cli.commands.slides import sync_v3
        from clm.slides.doc_report import pair_payload

        repo, sha, base = _repo_at_base(tmp_path)
        de = _edit(DE0, "DE eins").replace(
            'lang="de" slide_id="m1"', 'lang="de" tags=["alert"] slide_id="m1"'
        )
        repo.write(de, _edit(EN0, "EN one"))
        bundle, diff = _diff(repo, base)

        m1_actions = sorted(i.action for i in diff.items if i.key == "id:m1")
        assert m1_actions == ["mirror_tags", "verify_translation"], (
            "fixture must co-frame an aspect row under the same key"
        )
        recovered = recover_base_diffs(bundle, diff)
        payload = pair_payload(bundle, diff, base_diffs=recovered)
        by_action = {i["action"]: i for i in payload["items"] if i["key"] == "id:m1"}
        assert "base_ref" not in by_action["mirror_tags"]
        assert "de_diff" not in by_action["mirror_tags"]
        assert by_action["verify_translation"]["base_ref"] == sha

        text = sync_v3._render_pair(bundle, diff, recovered)
        assert text.count(f"de vs base {sha[:12]}:") == 1
        assert text.count(f"en vs base {sha[:12]}:") == 1

    def test_since_mode_resolves_the_ref_and_emits_no_batch(self, tmp_path: Path, capsys):
        """Review F4/F5: in ``--since`` mode every changed row trivially
        "recovers" at the named ref (its base fps were computed there), so the
        batch observation would always fire and overclaim "one editing
        session" — it is suppressed. And ``base_ref`` must be the resolved
        full sha, not the user's relative spelling, which names a different
        commit as soon as the next commit lands."""
        from clm.cli.commands.slides.sync_v3 import run_report_v3

        repo, sha_a, _base = _repo_at_base(tmp_path)
        repo.write(
            _edit(DE0, "DE eins", "DE zwei", "DE drei"),
            _edit(EN0, "EN one", "EN two", "EN three"),
        )
        repo.commit("three members edited")

        # Relative refs resolve against the DECK's repo (git runs in its
        # root), not the process cwd — no chdir needed.
        exit_code = run_report_v3(repo.de, repo.en, as_json=True, since_ref="HEAD~1")
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 1
        rows = [i for i in payload["items"] if i["action"] == "verify_translation"]
        assert len(rows) == 3
        for row in rows:
            assert row["base_ref"] == sha_a, "the relative spelling must be resolved"
        assert not any(o["kind"] == "verify_translation_batch" for o in payload["observations"])

    def test_the_report_verb_wires_recovery_end_to_end(self, tmp_path: Path, capsys):
        """`run_report_v3` against a real committed ledger: the true read-verb
        path (ledger baseline → walk → payload), not a hand-assembled diff."""
        from clm.cli.commands.slides.sync_v3 import run_report_v3
        from clm.slides import doc_ledger

        repo, sha, _base = _repo_at_base(tmp_path)
        bundle = repo.bundle()
        assert bundle.outcome.deck is not None
        ledger_path = doc_ledger.ledger_path_for(repo.de)
        ledger = doc_ledger.load(ledger_path)
        doc_ledger.record_deck_snapshot(
            ledger,
            doc_ledger.deck_key_for(repo.de),
            bundle.outcome.deck,
            provenance="record",
            commit=None,
        )
        doc_ledger.save(ledger, ledger_path)
        repo.commit("ledger")  # touches only .clm/, so the walk recovers `sha`
        repo.write(_edit(DE0, "DE eins"), _edit(EN0, "EN one"))

        exit_code = run_report_v3(repo.de, repo.en, as_json=True)
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 1
        assert payload["schema"] == 5
        (row,) = [i for i in payload["items"] if i["action"] == "verify_translation"]
        assert row["base_ref"] == sha
        assert "+# DE eins, neu" in row["de_diff"]
        assert "+# EN one, neu" in row["en_diff"]
