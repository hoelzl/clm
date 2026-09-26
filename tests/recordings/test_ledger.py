"""Tests for the committed per-topic recordings ledger (#1004).

Design: ``docs/claude/design/recordings-rerecord-backlog.md`` §4.1. The
ledger stores, per recorded part, a source anchor (commit + confidence) and
the recorded language's member fingerprints — the same fingerprints the sync
ledger records, under the same ``hash_version`` rule.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clm.recordings import ledger as rl
from clm.slides.doc_ledger import LEDGER_HASH_VERSION

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _code(slug: str, text: str) -> str:
    return f'# %% slide_id="{slug}"\n{text}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


DE0 = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _localized("m1", "de", "DE eins"),
    _code("c1", "x = 1"),
)
EN0 = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _localized("m1", "en", "EN one"),
    _code("c1", "x = 1"),
)


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


def _write_pair(folder: Path, de: str = DE0, en: str = EN0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "slides_t.de.py").write_text(de, encoding="utf-8")
    (folder / "slides_t.en.py").write_text(en, encoding="utf-8")
    return folder / "slides_t.de.py"


def _part(**overrides) -> rl.LedgerPart:
    base: dict = {
        "part": 1,
        "recorded_at": "2026-09-25T04:44:38",
        "course_id": "c-de",
        "lang": "de",
        "anchor": rl.anchor_for("abc123", False),
        "members": {"id:s0": "f0"},
    }
    base.update(overrides)
    return rl.LedgerPart(**base)


# ---------------------------------------------------------------------------
# Keys, paths, anchors
# ---------------------------------------------------------------------------


def test_ledger_path_sits_in_topic_dot_clm(tmp_path: Path):
    de = tmp_path / "topic_x" / "slides_t.de.py"
    assert rl.ledger_path_for(de) == tmp_path / "topic_x" / ".clm" / "recordings-ledger.json"
    assert rl.deck_key_for(de) == "slides_t"


@pytest.mark.parametrize("name", ["slides_t.de.py", "slides_t.en.py", "slides_t.py"])
def test_split_halves_by_name_only(tmp_path: Path, name: str):
    halves = rl.split_halves(tmp_path / name)
    assert halves == (tmp_path / "slides_t.de.py", tmp_path / "slides_t.en.py")


def test_split_halves_rejects_companion_and_extensionless(tmp_path: Path):
    assert rl.split_halves(tmp_path / "voiceover_t.de.py") is None
    assert rl.split_halves(tmp_path / "README") is None


def test_anchor_kinds_from_record_time_git_info():
    assert rl.anchor_for("abc", False) == rl.RecordingAnchor(kind="commit", commit="abc")
    assert rl.anchor_for("abc", True) == rl.RecordingAnchor(
        kind="commit-dirty", commit="abc", dirty=True
    )
    assert rl.anchor_for(None, True) == rl.RecordingAnchor(kind="unanchored")


# ---------------------------------------------------------------------------
# Member fingerprints
# ---------------------------------------------------------------------------


def test_deck_members_selects_recorded_language(tmp_path: Path):
    de = _write_pair(tmp_path / "t")
    de_members = rl.deck_members(de, "de")
    en_members = rl.deck_members(de, "en")

    assert {"id:s0", "id:m1", "id:c1"} <= set(de_members)
    # A localized member differs per side; a shared code member matches.
    assert de_members["id:m1"] != en_members["id:m1"]
    assert de_members["id:c1"] == en_members["id:c1"]
    # The same fingerprint function as the sync ledger.
    from clm.slides.doc_identity import content_fingerprint
    from clm.slides.doc_lenses import load_bundle

    deck = load_bundle(de).outcome.deck
    assert deck is not None
    m1 = next(m for m in deck.members() if m.key.render() == "id:m1")
    assert de_members["id:m1"] == content_fingerprint(m1.de)


UNSPLIT = _build(
    '# j2 from \'macros.j2\' import header\n# {{ header("Titel", "Title") }}\n\n',
    '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n\n',
    '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n\n',
    '# %% [markdown] lang="de" tags=["notes"]\n# Notiz\n\n',
    _code("c1", "x = 1"),
)


def test_unsplit_deck_members_fall_back_to_cells(tmp_path: Path):
    """A single-file bilingual deck has no split pair: its cells are the members."""
    folder = tmp_path / "t"
    folder.mkdir()
    deck = folder / "slides_u.py"
    deck.write_text(UNSPLIT, encoding="utf-8")
    assert rl.is_unsplit_deck(deck)

    de = rl.deck_members(deck, "de")
    en = rl.deck_members(deck, "en")
    assert set(de) == {
        "cell:header/j2/0",
        "cell:header/j2/1",
        "id:s0",
        "cell:s0/markdown/0",
        "id:c1",
    }
    assert set(en) == {"cell:header/j2/0", "cell:header/j2/1", "id:s0", "id:c1"}  # DE-only note
    assert de["id:s0"] != en["id:s0"]  # each side's own title cell
    assert de["id:c1"] == en["id:c1"]  # the shared code cell

    # The fingerprint ignores the slide_id attribute, like a split member's.
    deck.write_text(UNSPLIT.replace('slide_id="c1"', 'slide_id="c9"'), encoding="utf-8")
    assert rl.deck_members(deck, "de")["id:c9"] == de["id:c1"]


def test_unsplit_cell_ordinals_are_scoped_to_their_slide(tmp_path: Path):
    """Inserting an id-less cell renumbers only its own group, like the split model."""
    deck = (
        '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n# # A\n\n'
        '# %% [markdown] lang="de"\n# a1\n\n'
        '# %% [markdown] lang="de" tags=["slide"] slide_id="b"\n# # B\n\n'
        '# %% [markdown] lang="de"\n# b1\n\n'
    )
    before = rl.unsplit_members(deck, "de")
    inserted = deck.replace("# a1\n\n", '# a1\n\n# %% [markdown] lang="de"\n# a2\n\n')
    after = rl.unsplit_members(inserted, "de")
    assert before["cell:b/markdown/0"] == after["cell:b/markdown/0"]  # untouched group
    assert set(after) - set(before) == {"cell:a/markdown/1"}


def test_stale_single_file_beside_a_split_pair_is_not_an_unsplit_deck(tmp_path: Path):
    de = _write_pair(tmp_path / "t")
    stale = de.with_name("slides_t.py")
    stale.write_text(UNSPLIT, encoding="utf-8")
    assert not rl.is_unsplit_deck(stale)
    assert rl.is_unsplit_deck(tmp_path / "t" / "slides_other.py")


def test_seeded_evidence_is_never_reported_as_exact(tmp_path: Path):
    for kind, dirty, expected in (
        ("commit", False, "recomputed"),
        ("commit-dirty", True, "approximate"),
        ("time", False, "approximate"),
    ):
        part = _part(
            anchor=rl.RecordingAnchor(kind=kind, commit="abc", dirty=dirty), evidence="anchor"
        )
        assert rl.resolve_part_members(part, tmp_path / "slides_t.de.py")[1] == expected
    # Record-time evidence is exact even on a dirty tree.
    part = _part(anchor=rl.anchor_for("abc", True))
    assert rl.resolve_part_members(part, tmp_path / "slides_t.de.py")[1] == "recorded"


def test_deck_members_is_empty_without_a_twin(tmp_path: Path):
    folder = tmp_path / "t"
    folder.mkdir()
    lone = folder / "slides_t.de.py"
    lone.write_text(DE0, encoding="utf-8")
    assert rl.deck_members(lone, "de") == {}


def test_deck_members_is_empty_for_missing_file(tmp_path: Path):
    assert rl.deck_members(tmp_path / "nope" / "slides_t.de.py", "de") == {}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip_is_canonical(tmp_path: Path):
    path = tmp_path / "t" / ".clm" / "recordings-ledger.json"
    ledger = rl.RecordingsLedger()
    ledger.deck("slides_t").parts.append(_part())

    assert rl.save(ledger, path) is True
    assert rl.save(ledger, path) is False  # byte-identical: write-free

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == rl.SCHEMA_VERSION
    assert raw["hash_version"] == LEDGER_HASH_VERSION
    assert raw["decks"]["slides_t"]["ack"] is None
    entry = raw["decks"]["slides_t"]["parts"][0]
    assert entry["anchor"] == {"kind": "commit", "commit": "abc123", "dirty": False}
    assert entry["members"] == {"id:s0": "f0"}
    assert entry["hash_version"] == LEDGER_HASH_VERSION
    assert path.read_text(encoding="utf-8").endswith("}\n")

    loaded = rl.load(path)
    assert loaded.decks["slides_t"].parts == [_part()]


def test_load_absent_is_empty(tmp_path: Path):
    assert rl.load(tmp_path / "missing.json") == rl.RecordingsLedger()


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"schema": 99, "decks": {}}',
        '{"schema": 1, "decks": {"x": 5}}',
        '{"schema": 1, "decks": []}',
        '{"schema": 1, "hash_version": "x", "decks": {}}',
        '{"schema": 1, "decks": {"x": {"parts": 5}}}',
        '{"schema": 1, "decks": {"x": {"parts": [{"part": "one"}]}}}',
    ],
)
def test_load_refuses_unreadable_ledgers(tmp_path: Path, content: str):
    path = tmp_path / "recordings-ledger.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(rl.LedgerError):
        rl.load(path)


def test_load_backfills_part_hash_version_from_envelope(tmp_path: Path):
    """An older writer that stamped only the envelope: the part inherits it."""
    path = tmp_path / "recordings-ledger.json"
    part = _part().model_dump()
    del part["hash_version"]
    path.write_text(
        json.dumps({"schema": 1, "hash_version": 0, "decks": {"slides_t": {"parts": [part]}}}),
        encoding="utf-8",
    )
    loaded = rl.load(path)
    assert loaded.decks["slides_t"].parts[0].hash_version == 0


def test_record_part_upserts_same_course_part_and_language(tmp_path: Path):
    path = tmp_path / ".clm" / "recordings-ledger.json"
    rl.record_part(path, "slides_t", _part(part=1, lang="de", members={"id:s0": "old"}))
    rl.record_part(path, "slides_t", _part(part=2, lang="de"))
    rl.record_part(path, "slides_t", _part(part=1, lang="en"))
    # A second cohort's recording of the same part never erases the first's.
    rl.record_part(path, "slides_t", _part(part=1, lang="de", course_id="c-2026-08-de"))
    rl.record_part(path, "slides_t", _part(part=1, lang="de", members={"id:s0": "new"}))

    parts = rl.load(path).decks["slides_t"].parts
    assert [rl.part_identity(p) for p in parts] == [
        ("c-2026-08-de", 1, "de"),
        ("c-de", 1, "de"),
        ("c-de", 1, "en"),
        ("c-de", 2, "de"),
    ]
    assert next(p for p in parts if rl.part_identity(p) == ("c-de", 1, "de")).members == {
        "id:s0": "new"
    }


@pytest.mark.parametrize("lang", ["EN", "en-US", "fr", ""])
def test_deck_members_rejects_a_lang_that_names_no_side(tmp_path: Path, lang: str):
    de = _write_pair(tmp_path / "t")
    with pytest.raises(ValueError, match="recordable language"):
        rl.deck_members(de, lang)
    with pytest.raises(ValueError, match="recordable language"):
        rl.deck_members_at_ref(de, "HEAD", lang)


def test_record_part_keeps_other_decks_and_ack(tmp_path: Path):
    path = tmp_path / ".clm" / "recordings-ledger.json"
    ledger = rl.RecordingsLedger()
    ledger.deck("slides_a").parts.append(_part())
    ledger.deck("slides_a").ack = rl.DeckAck(at="2026-09-25", note="kept", members={"id:s0": "f"})
    rl.save(ledger, path)

    rl.record_part(path, "slides_b", _part())

    loaded = rl.load(path)
    assert set(loaded.decks) == {"slides_a", "slides_b"}
    assert loaded.decks["slides_a"].ack is not None
    assert loaded.decks["slides_a"].ack.note == "kept"


# ---------------------------------------------------------------------------
# The hash_version rule
# ---------------------------------------------------------------------------


def test_current_hash_version_members_are_trusted(tmp_path: Path):
    members, status = rl.resolve_part_members(_part(), tmp_path / "slides_t.de.py")
    assert (members, status) == ({"id:s0": "f0"}, "recorded")


def test_stale_hash_version_without_commit_is_unverifiable(tmp_path: Path):
    part = _part(hash_version=LEDGER_HASH_VERSION - 1, anchor=rl.anchor_for(None, False))
    assert rl.resolve_part_members(part, tmp_path / "slides_t.de.py") == (None, "unverifiable")


def test_empty_members_are_unverifiable_even_at_current_version(tmp_path: Path):
    part = _part(members={}, anchor=rl.anchor_for(None, False))
    assert rl.resolve_part_members(part, tmp_path / "slides_t.de.py") == (None, "unverifiable")


# ---------------------------------------------------------------------------
# git-backed: recompute at the anchor commit, the gitignore warning
# ---------------------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    root.mkdir()
    _git(root, "init", "-q")
    return root


@needs_git
def test_stale_hash_version_recomputes_from_anchor_commit(repo: Path):
    de = _write_pair(repo / "slides" / "topic_x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "recorded state")
    commit = _git(repo, "rev-parse", "HEAD")
    recorded = rl.deck_members(de, "de")

    # The deck moves on after the recording...
    _write_pair(repo / "slides" / "topic_x", de=DE0.replace("DE eins", "DE eins, neu"))
    assert rl.deck_members(de, "de") != recorded

    # ...but an entry under an older fingerprint version recomputes at the anchor.
    part = _part(
        hash_version=LEDGER_HASH_VERSION - 1,
        anchor=rl.anchor_for(commit, False),
        members={"id:s0": "incomparable"},
    )
    members, status = rl.resolve_part_members(part, de)
    assert status == "recomputed"
    assert members == recorded


@needs_git
def test_dirty_anchor_recompute_is_only_approximate(repo: Path):
    de = _write_pair(repo / "slides" / "topic_x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "recorded state")
    commit = _git(repo, "rev-parse", "HEAD")
    part = _part(hash_version=LEDGER_HASH_VERSION - 1, anchor=rl.anchor_for(commit, True))

    members, status = rl.resolve_part_members(part, de)

    assert status == "approximate"
    assert members == rl.deck_members(de, "de")


@needs_git
def test_deck_members_at_ref_reads_the_committed_pair(repo: Path):
    de = _write_pair(repo / "slides" / "topic_x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v1")
    v1 = _git(repo, "rev-parse", "HEAD")
    at_v1 = rl.deck_members(de, "de")

    _write_pair(repo / "slides" / "topic_x", de=DE0.replace("DE eins", "DE zwei"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v2")

    assert rl.deck_members_at_ref(de, v1, "de") == at_v1
    assert rl.deck_members_at_ref(de, v1, "de") != rl.deck_members(de, "de")
    assert rl.deck_members_at_ref(de, "0" * 40, "de") is None


@needs_git
def test_unsplit_deck_members_at_ref(repo: Path):
    folder = repo / "slides" / "topic_u"
    folder.mkdir(parents=True)
    deck = folder / "slides_u.py"
    deck.write_text(UNSPLIT, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v1")
    v1 = _git(repo, "rev-parse", "HEAD")
    at_v1 = rl.deck_members(deck, "de")
    deck.write_text(UNSPLIT.replace("Notiz", "Notiz, neu"), encoding="utf-8")

    assert rl.deck_members_at_ref(deck, v1, "de") == at_v1
    assert rl.deck_members_at_ref(deck, v1, "de") != rl.deck_members(deck, "de")


@needs_git
def test_unresolvable_anchor_commit_is_unverifiable(repo: Path):
    de = _write_pair(repo / "slides" / "topic_x")
    part = _part(hash_version=LEDGER_HASH_VERSION - 1, anchor=rl.anchor_for("0" * 40, False))
    assert rl.resolve_part_members(part, de) == (None, "unverifiable")


@needs_git
def test_ignored_ledger_warns_until_the_exception_line_exists(repo: Path):
    de = _write_pair(repo / "slides" / "topic_x")
    ledger_path = rl.ledger_path_for(de)
    rl.record_part(ledger_path, "slides_t", _part())

    (repo / ".gitignore").write_text("**/.clm/*\n!**/.clm/sync-ledger.json\n", encoding="utf-8")
    warning = rl.ignored_ledger_warning(ledger_path)
    assert warning is not None
    assert "!**/.clm/recordings-ledger.json" in warning
    assert rl.is_git_ignored(ledger_path) is True

    (repo / ".gitignore").write_text(
        "**/.clm/*\n!**/.clm/sync-ledger.json\n!**/.clm/recordings-ledger.json\n",
        encoding="utf-8",
    )
    assert rl.ignored_ledger_warning(ledger_path) is None
    assert rl.is_git_ignored(ledger_path) is False


def test_ignored_check_outside_git_is_unknown_and_silent(tmp_path: Path):
    path = tmp_path / ".clm" / "recordings-ledger.json"
    path.parent.mkdir()
    path.write_text("{}", encoding="utf-8")
    # Not a repository: no verdict, no warning — never a false alarm.
    assert rl.is_git_ignored(path) in (None, False)
    assert rl.ignored_ledger_warning(path) is None


def test_stored_order_is_used_for_seeded_entries(tmp_path: Path):
    """A seeded entry's members come back from sorted JSON; the order rule must
    read the stored ``order``, never the map's (alphabetical) key order."""
    path = tmp_path / ".clm" / "recordings-ledger.json"
    part = _part(
        members={"id:z": "1", "id:a": "2"},
        order=["id:z", "id:a"],
        anchor=rl.anchor_for("abc", True),
        evidence="anchor",
    )
    rl.record_part(path, "slides_t", part)
    loaded = rl.load(path).decks["slides_t"].parts[0]
    assert list(loaded.members) == ["id:a", "id:z"]  # sorted on disk
    members, status = rl.resolve_part_members(loaded, tmp_path / "slides_t.de.py")
    assert status == "approximate"
    assert rl.resolve_part_order(loaded, members, status) == ["id:z", "id:a"]
