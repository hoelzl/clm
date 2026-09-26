"""Tests for the re-recording backlog engine (#965, design §4.2–4.3).

Severity is a pure function of (ledger members, working-tree deck); these
tests drive it through real split pairs in a throwaway git repo so the
commits-since-anchor and hash-version paths are exercised too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clm.recordings import ledger as rl
from clm.recordings import report as rr
from tests.recordings.test_ledger import DE0, EN0, HEADER_DE, HEADER_EN, _code, _localized, _slide

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


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


def _notes(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["notes"] slide_id="{slug}"\n# {text}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


DE_FULL = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _localized("m1", "de", "DE eins"),
    _notes("n1", "de", "Notiz"),
    _code("c1", "x = 1"),
)
EN_FULL = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _localized("m1", "en", "EN one"),
    _notes("n1", "en", "Note"),
    _code("c1", "x = 1"),
)
COMP_DE = '# %% [markdown] lang="de" tags=["voiceover"] slide_id="v1" for_slide="s0"\n# Erzählung\n'
COMP_EN = '# %% [markdown] lang="en" tags=["voiceover"] slide_id="v1" for_slide="s0"\n# Narration\n'


class _Course:
    """A course root in a git repo with one topic and one split deck + companions."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir()
        _git(root, "init", "-q")
        self.topic = root / "slides" / "module_100" / "topic_010_t"
        (self.topic / "voiceover").mkdir(parents=True)
        self.de = self.topic / "slides_t.de.py"
        self.en = self.topic / "slides_t.en.py"
        self.write(DE_FULL, EN_FULL, COMP_DE, COMP_EN)

    def write(self, de: str, en: str, comp_de: str | None = None, comp_en: str | None = None):
        self.de.write_text(de, encoding="utf-8")
        self.en.write_text(en, encoding="utf-8")
        if comp_de is not None:
            (self.topic / "voiceover" / "voiceover_t.de.py").write_text(comp_de, encoding="utf-8")
        if comp_en is not None:
            (self.topic / "voiceover" / "voiceover_t.en.py").write_text(comp_en, encoding="utf-8")

    def commit(self, msg: str = "c") -> str:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", msg)
        return _git(self.root, "rev-parse", "HEAD")

    def record(self, *, part: int = 1, lang: str = "de", course_id: str = "c-de", dirty=False):
        commit = _git(self.root, "rev-parse", "HEAD")
        members = rl.deck_members(self.de, lang)
        entry = rl.LedgerPart(
            part=part,
            recorded_at=f"2026-09-25T10:00:0{part}",
            course_id=course_id,
            lang=lang,
            anchor=rl.anchor_for(commit, dirty),
            members=members,
            order=rl.id_order(members),
        )
        rl.record_part(rl.ledger_path_for(self.de), rl.deck_key_for(self.de), entry)
        return entry

    def report(self, **kw) -> rr.Report:
        return rr.build_report(self.root, **kw)

    def deck(self, **kw) -> rr.DeckReport:
        [deck] = self.report(**kw).decks
        return deck


@pytest.fixture
def course(tmp_path: Path) -> _Course:
    c = _Course(tmp_path / "course")
    c.commit("initial")
    return c


# ---------------------------------------------------------------------------
# Severity classes
# ---------------------------------------------------------------------------


def test_unchanged_deck_is_none_and_clean(course: _Course):
    course.record()
    report = course.report()
    [deck] = report.decks
    assert deck.status == "recorded"
    assert deck.severity == "none"
    assert deck.ack_state == "unacknowledged"
    assert deck.parts[0].members_status == "recorded"
    assert deck.parts[0].changed == 0
    assert deck.parts[0].commits_since_anchor == []
    assert not deck.needs_attention
    assert report.is_clean


def test_markdown_edit_is_visible(course: _Course):
    course.record()
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)
    deck = course.deck()
    assert deck.severity == "visible"
    assert deck.parts[0].changed_members == {"id:m1": "visible"}
    assert (deck.parts[0].changed, deck.parts[0].total) == (
        1,
        len(rl.deck_members(course.de, "de")),
    )
    assert deck.needs_attention


def test_code_edit_is_structural(course: _Course):
    course.record()
    course.write(DE_FULL.replace("x = 1", "x = 2"), EN_FULL.replace("x = 1", "x = 2"))
    deck = course.deck()
    assert deck.severity == "structural"
    assert deck.parts[0].changed_members == {"id:c1": "structural"}


def test_added_and_removed_id_members_are_structural(course: _Course):
    course.record()
    c1_header = '# %% slide_id="c1"'
    course.write(
        DE_FULL.replace(c1_header, _localized("m2", "de", "DE zwei") + c1_header),
        EN_FULL.replace(c1_header, _localized("m2", "en", "EN two") + c1_header),
    )
    deck = course.deck()
    assert deck.severity == "structural"
    assert deck.parts[0].changed_members == {"id:m2": "structural"}

    course.write(
        DE_FULL.replace(_localized("m1", "de", "DE eins"), ""),
        EN_FULL.replace(_localized("m1", "en", "EN one"), ""),
    )
    deck = course.deck()
    assert deck.severity == "structural"
    assert "id:m1" in deck.parts[0].changed_members


def test_reordered_members_are_structural(course: _Course):
    course.record()
    de = DE_FULL.replace(
        _localized("m1", "de", "DE eins") + _notes("n1", "de", "Notiz"),
        _notes("n1", "de", "Notiz") + _localized("m1", "de", "DE eins"),
    )
    en = EN_FULL.replace(
        _localized("m1", "en", "EN one") + _notes("n1", "en", "Note"),
        _notes("n1", "en", "Note") + _localized("m1", "en", "EN one"),
    )
    assert de != DE_FULL and en != EN_FULL
    course.write(de, en)
    deck = course.deck()
    assert deck.severity == "structural"
    assert deck.parts[0].changed == 0  # nothing changed byte-wise, only the order


def test_notes_only_edit_is_notes(course: _Course):
    course.record()
    course.write(DE_FULL.replace("Notiz", "Notiz, neu"), EN_FULL)
    deck = course.deck()
    assert deck.severity == "notes"
    assert deck.parts[0].changed_members == {"id:n1": "notes"}


def test_companion_only_edit_is_narration(course: _Course):
    course.record()
    course.write(DE_FULL, EN_FULL, COMP_DE.replace("Erzählung", "Erzählung, neu"))
    deck = course.deck()
    assert deck.severity == "narration"
    assert deck.parts[0].changed_members == {"id:v1": "narration"}


def test_highest_class_wins_and_other_language_is_untouched(course: _Course):
    course.record(lang="de")
    # EN-only edit: the DE recording did not show it.
    course.write(DE_FULL, EN_FULL.replace("EN one", "EN one, new"))
    assert course.deck().severity == "none"
    # DE notes + DE code: structural wins.
    course.write(DE_FULL.replace("Notiz", "N2").replace("x = 1", "x = 3"), EN_FULL)
    deck = course.deck()
    assert deck.severity == "structural"
    assert deck.parts[0].changed_members == {"id:n1": "notes", "id:c1": "structural"}


# ---------------------------------------------------------------------------
# Anchors, commits, hash version
# ---------------------------------------------------------------------------


def test_commits_since_anchor_count_only_the_deck_bundle(course: _Course):
    course.record()
    (course.root / "README.md").write_text("unrelated\n", encoding="utf-8")
    course.commit("unrelated")
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)
    c1 = course.commit("edit deck")
    # A companion-only commit (the companion lives in voiceover/, not beside the deck).
    (course.topic / "voiceover" / "voiceover_t.de.py").write_text(
        COMP_DE.replace("Erzählung", "E2"), encoding="utf-8"
    )
    c2 = course.commit("edit companion only")

    deck = course.deck()
    assert deck.parts[0].commits_since_anchor == [c2, c1]
    assert deck.parts[0].anchor["kind"] == "commit"


def test_commits_since_anchor_follow_a_topic_renumber(course: _Course):
    """Edits made under the topic's old directory count after a renumber."""
    course.record()
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)
    c1 = course.commit("edit before the renumber")
    new_topic = course.topic.parent / "topic_020_t"
    _git(course.root, "mv", str(course.topic), str(new_topic))
    c2 = course.commit("renumber")
    # The ledger moved with the topic directory; the deck is found under its new home.
    deck = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_t")
    assert deck.parts[0].commits_since_anchor == [c2, c1]


def test_state_lookup_is_only_consulted_with_a_manifest(course: _Course):
    course.record()
    calls: list[str] = []

    def lookup(course_id: str):
        calls.append(course_id)
        raise ValueError("malformed state file")

    assert course.deck(state_lookup=lookup).parts[0].built_output_changed is None
    assert calls == []  # no manifest: the state file is never read
    deck = course.deck(manifest={"files": []}, state_lookup=lookup)
    assert calls == ["c-de"]
    assert (
        deck.parts[0].built_output_changed is None
    )  # a broken state file is a hint lost, not a crash


def test_foreign_lang_entry_is_unverifiable_not_fatal(course: _Course):
    course.record()
    path = rl.ledger_path_for(course.de)
    ledger = rl.load(path)
    ledger.decks["slides_t"].parts.append(
        rl.LedgerPart(
            part=9,
            recorded_at="t",
            course_id="c-xx",
            lang="xx",
            anchor=rl.anchor_for(None, False),
            hash_version=rl.HASH_VERSION - 1,
        )
    )
    rl.save(ledger, path)
    deck = course.deck()
    assert [(p.lang, p.severity) for p in deck.parts] == [("de", "none"), ("xx", "unverifiable")]


def test_dirty_anchor_recompute_after_hash_bump_is_approximate(course: _Course, monkeypatch):
    entry = course.record(dirty=True)
    # Simulate an entry written under an older fingerprint version.
    path = rl.ledger_path_for(course.de)
    ledger = rl.load(path)
    ledger.decks["slides_t"].parts[0].hash_version = rl.HASH_VERSION - 1
    rl.save(ledger, path)
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)

    deck = course.deck()
    assert deck.parts[0].members_status == "approximate"
    assert deck.severity == "visible"
    assert entry.anchor.kind == "commit-dirty"


def test_unanchored_stale_entry_is_unverifiable(course: _Course):
    course.record()
    path = rl.ledger_path_for(course.de)
    ledger = rl.load(path)
    part = ledger.decks["slides_t"].parts[0]
    part.hash_version = rl.HASH_VERSION - 1
    part.anchor = rl.anchor_for(None, False)
    rl.save(ledger, path)

    deck = course.deck()
    assert deck.severity == "unverifiable"
    assert deck.parts[0].members_status == "unverifiable"
    assert deck.needs_attention


# ---------------------------------------------------------------------------
# Orphaned, unrecorded, several parts and cohorts
# ---------------------------------------------------------------------------


def test_deleted_deck_is_orphaned(course: _Course):
    entry = course.record()
    course.de.unlink()
    course.en.unlink()
    deck = course.deck()
    assert deck.status == "orphaned"
    assert deck.needs_attention
    assert deck.parts[0].total == len(entry.members)


def test_unrecorded_decks_only_with_all(course: _Course):
    other = course.topic.parent / "topic_020_u"
    other.mkdir()
    (other / "slides_u.de.py").write_text(DE0, encoding="utf-8")
    (other / "slides_u.en.py").write_text(EN0, encoding="utf-8")
    course.record()

    assert [d.deck for d in course.report().decks] == ["slides_t"]
    report = course.report(include_unrecorded=True)
    assert [(d.deck, d.status) for d in report.decks] == [
        ("slides_t", "recorded"),
        ("slides_u", "unrecorded"),
    ]
    assert report.is_clean  # an unrecorded deck is information, not work


def test_one_row_per_deck_with_every_part_and_cohort(course: _Course):
    course.record(part=1, course_id="c-2026-04-de")
    course.write(DE_FULL.replace("DE eins", "v2"), EN_FULL)
    course.commit("v2")
    course.record(part=2, course_id="c-2026-04-de")
    course.record(part=1, course_id="c-2026-08-de")
    course.write(DE_FULL.replace("DE eins", "v3"), EN_FULL)

    deck = course.deck()
    assert [(p.course_id, p.part) for p in deck.parts] == [
        ("c-2026-04-de", 1),
        ("c-2026-04-de", 2),
        ("c-2026-08-de", 1),
    ]
    assert [p.severity for p in deck.parts] == ["visible", "visible", "visible"]
    assert deck.severity == "visible"


def test_deck_file_scope_restricts_to_one_deck(course: _Course):
    other_de = course.topic / "slides_o.de.py"
    other_de.write_text(DE0, encoding="utf-8")
    (course.topic / "slides_o.en.py").write_text(EN0, encoding="utf-8")
    course.record()
    rl.record_part(
        rl.ledger_path_for(other_de),
        "slides_o",
        rl.LedgerPart(
            part=1,
            recorded_at="t",
            course_id="c-de",
            lang="de",
            anchor=rl.anchor_for(None, False),
            members=rl.deck_members(other_de, "de"),
        ),
    )
    assert [d.deck for d in rr.build_report(course.root).decks] == ["slides_o", "slides_t"]
    assert [d.deck for d in rr.build_report(other_de).decks] == ["slides_o"]
    assert [d.deck for d in rr.build_report(course.topic).decks] == ["slides_o", "slides_t"]


def test_malformed_ledger_is_reported_not_raised(course: _Course):
    path = rl.ledger_path_for(course.de)
    path.parent.mkdir(exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    report = course.report()
    assert report.decks == []
    assert len(report.ledger_errors) == 1
    assert not report.is_clean


def test_unsplit_deck_is_reported_from_its_cells(course: _Course):
    from tests.recordings.test_ledger import UNSPLIT

    folder = course.topic.parent / "topic_020_u"
    folder.mkdir()
    deck = folder / "slides_u.py"
    deck.write_text(UNSPLIT, encoding="utf-8")
    _git(course.root, "add", "-A")
    commit = course.commit("unsplit")
    members = rl.deck_members(deck, "de")
    rl.record_part(
        rl.ledger_path_for(deck),
        "slides_u",
        rl.LedgerPart(
            part=1,
            recorded_at="t",
            course_id="c-de",
            lang="de",
            anchor=rl.anchor_for(commit, False),
            members=members,
            order=rl.id_order(members),
        ),
    )
    row = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_u")
    assert row.severity == "none"
    assert set(row.deck_files) == {"de", "en"}

    deck.write_text(UNSPLIT.replace("Notiz", "Notiz, neu"), encoding="utf-8")
    row = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_u")
    assert row.severity == "notes"
    deck.write_text(UNSPLIT.replace("x = 1", "x = 2"), encoding="utf-8")
    row = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_u")
    assert row.severity == "structural"
    # An EN-only edit does not touch the DE recording.
    deck.write_text(UNSPLIT.replace("# # Title", "# # Title, new"), encoding="utf-8")
    row = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_u")
    assert row.severity == "none"


def test_ids_stamped_after_the_recording_do_not_read_as_drift(course: _Course):
    """An id-less recording (cell scheme) against the same deck with ids stamped
    and split since: every fingerprint still exists, so the deck is `none`."""
    from tests.recordings.test_ledger import UNSPLIT

    folder = course.topic.parent / "topic_020_u"
    folder.mkdir()
    deck = folder / "slides_u.py"
    idless = UNSPLIT.replace(' slide_id="s0"', "").replace(' slide_id="c1"', "")
    deck.write_text(idless, encoding="utf-8")
    _git(course.root, "add", "-A")
    commit = course.commit("id-less unsplit")
    members = rl.deck_members(deck, "de")
    assert all(k.startswith("cell:") for k in members)
    rl.record_part(
        rl.ledger_path_for(deck),
        "slides_u",
        rl.LedgerPart(
            part=1,
            recorded_at="t",
            course_id="c-de",
            lang="de",
            anchor=rl.anchor_for(commit, False),
            members=members,
            order=[],
            evidence="anchor",
        ),
    )
    # Ids stamped, same bytes otherwise.
    deck.write_text(UNSPLIT, encoding="utf-8")
    row = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_u")
    assert row.severity == "none", row.parts[0].changed_members
    # A real edit still surfaces.
    deck.write_text(UNSPLIT.replace("x = 1", "x = 2"), encoding="utf-8")
    row = next(d for d in rr.build_report(course.root).decks if d.deck == "slides_u")
    assert row.severity == "structural"
    assert row.parts[0].changed == 1


def test_positional_member_moved_by_an_insert_is_not_drift(course: _Course):
    """pos: handles renumber on an insert; the moved members match by fingerprint."""
    course.record()
    inserted = "# %% [markdown]\n# neu\n\n"  # a shared id-less cell, on both sides
    de = DE_FULL.replace(_notes("n1", "de", "Notiz"), inserted + _notes("n1", "de", "Notiz"))
    en = EN_FULL.replace(_notes("n1", "en", "Note"), inserted + _notes("n1", "en", "Note"))
    course.write(de, en)
    deck = course.deck()
    assert deck.parts[0].changed == 1  # only the inserted cell
    assert all(v == "visible" for v in deck.parts[0].changed_members.values())


# ---------------------------------------------------------------------------
# Acknowledgement
# ---------------------------------------------------------------------------


def test_ack_then_drift_since_ack(course: _Course):
    course.record()
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)
    assert course.deck().severity == "visible"

    result = rr.acknowledge(course.de, note="reword only")
    assert result.langs == ["de"]
    assert result.written is True
    deck = course.deck()
    assert deck.ack_state == "acknowledged"
    assert deck.ack_note == "reword only"
    assert deck.severity == "visible"  # the recording still differs...
    assert not deck.needs_attention  # ...but the author decided

    # A later edit of a DIFFERENT member re-surfaces the deck with the drift since the ack.
    course.write(DE_FULL.replace("DE eins", "DE eins, neu").replace("x = 1", "x = 9"), EN_FULL)
    deck = course.deck()
    assert deck.ack_state == "drifted-since-ack"
    assert deck.severity_since_ack == "structural"
    assert deck.needs_attention

    # Reverting that edit returns the deck to acknowledged.
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)
    assert course.deck().ack_state == "acknowledged"


def test_ack_covers_every_recorded_language(course: _Course):
    course.record(lang="de")
    course.record(part=2, lang="en", course_id="c-en")
    result = rr.acknowledge(course.de)
    assert result.langs == ["de", "en"]
    ack = rl.load(rl.ledger_path_for(course.de)).decks["slides_t"].ack
    assert ack is not None
    assert {k.split(":", 1)[0] for k in ack.members} == {"de", "en"}
    assert course.deck().ack_state == "acknowledged"


def test_language_recorded_after_the_ack_is_unacknowledged_not_drifted(course: _Course):
    course.record(lang="de")
    rr.acknowledge(course.de, note="de only")
    assert course.deck().ack_state == "acknowledged"

    course.record(part=2, lang="en", course_id="c-en")
    deck = course.deck()
    assert deck.ack_state == "unacknowledged"
    assert deck.severity_since_ack is None
    assert deck.ack_note == "de only"  # the old ack is still visible…
    assert not deck.needs_attention  # …and nothing changed on either side

    rr.acknowledge(course.de)
    assert course.deck().ack_state == "acknowledged"


def test_ack_without_recording_is_refused(course: _Course):
    with pytest.raises(LookupError):
        rr.acknowledge(course.de)


def test_stale_ack_hash_version_is_not_trusted(course: _Course):
    course.record()
    rr.acknowledge(course.de)
    path = rl.ledger_path_for(course.de)
    ledger = rl.load(path)
    assert ledger.decks["slides_t"].ack is not None
    ledger.decks["slides_t"].ack.hash_version = rl.HASH_VERSION - 1
    rl.save(ledger, path)
    assert course.deck().ack_state == "stale-ack"


# ---------------------------------------------------------------------------
# JSON shape and the secondary flag
# ---------------------------------------------------------------------------


def test_report_to_dict_envelope(course: _Course):
    course.record()
    course.write(DE_FULL.replace("DE eins", "DE eins, neu"), EN_FULL)
    payload = rr.report_to_dict(course.report())
    assert list(payload)[:3] == ["schema", "tool", "verb"]
    assert (payload["schema"], payload["tool"], payload["verb"]) == (1, "recordings", "report")
    assert payload["is_clean"] is False
    assert payload["needs_attention"] == ["slides/module_100/topic_010_t/slides_t"]
    assert payload["counts"] == {"visible": 1}
    [deck] = payload["decks"]
    assert deck["needs_attention"] is True
    assert deck["parts"][0]["changed_members"] == {"id:m1": "visible"}
    assert deck["parts"][0]["anchor"]["kind"] == "commit"


def test_built_output_changed_joins_state_by_stamp(course: _Course):
    from clm.core.provenance_manifest import topic_digest_from_files
    from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart

    entry = course.record()
    files = [{"path": "a", "topic_id": "t", "content_hash": "sha256:a"}]
    manifest = {"files": files}
    state = CourseRecordingState(
        course_id="c-de",
        lectures=[
            LectureState(
                lecture_id="S::D",
                display_name="D",
                parts=[
                    RecordingPart(
                        part=1,
                        raw_file="r.mkv",
                        recorded_at=entry.recorded_at,
                        topic_id="t",
                        slide_digest="stale",
                    )
                ],
            )
        ],
    )
    lookup = {"c-de": state}.get
    deck = course.deck(manifest=manifest, state_lookup=lookup)
    assert deck.parts[0].built_output_changed is True

    state.lectures[0].parts[0].slide_digest = topic_digest_from_files(files)
    deck = course.deck(manifest=manifest, state_lookup=lookup)
    assert deck.parts[0].built_output_changed is False

    # No manifest, or no matching state part: no flag — never a prerequisite.
    assert course.deck(state_lookup=lookup).parts[0].built_output_changed is None
    assert (
        course.deck(manifest=manifest, state_lookup=lambda _c: None).parts[0].built_output_changed
        is None
    )
