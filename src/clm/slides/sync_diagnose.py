"""Classify a ``clm slides sync verify`` symptom into its root cause (the catalog).

A ``verify`` failure is a *symptom*, not a diagnosis: the same code — ``id-asymmetry``,
``duplicate-id`` — is produced by several unrelated root causes, each needing a
*different* fix. This module turns each finding into a labelled diagnosis with the
evidence behind it (content-language vs ``lang=`` tag, who carries the id, whether a
twin exists) and whether the fix is **mechanical** (auto-fixable, identity-preserving)
or **authoring** (needs a human / translation).

It is a **read-only superset of both** ``sync verify`` **and** ``reconcile-vo-ids``:
the canonical id-less-twin case (a narrative cell id'd on one half, id-less on the
other) produces *no* ``verify`` violation — the owning slide's id is still carried by
the slide cell in both halves — so the classifier runs ``reconcile``'s own
occurrence-pairing detection independently, not only off a ``VerifyViolation``.

**The anti-pattern guard.** The recommended fix is *never* "rename slide_id X to Y to
make verify pass" — that buries a real gap (the ``array-limitations`` trap). Auto-fix is
permitted *only* for identity-preserving narrative operations (strip a duplicated /
asymmetric narration id to the canonical id-less form). A mis-tag, mis-pairing, content
gap, or whole-deck gap is **advisory** — a worklist entry, never an auto-rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.content_lang import detect
from clm.slides.lang_coverage import CoverageStatus, classify_counts, count_languages
from clm.slides.raw_cells import RawCell, split_cells
from clm.slides.reconcile_vo_ids import (
    TO_IDLESS,
    collapse_intra_half_duplicates,
    reconcile_voiceover_ids,
)
from clm.slides.sync_verify import VerifyResult, structural_gate, verify_pair

_NARRATIVE_ROLES = frozenset({"voiceover", "notes"})

# Root-cause labels (the catalog).
DUPLICATE_NARRATION_OVERSTAMP = "DUPLICATE-NARRATION-OVERSTAMP"
DUPLICATE_ID_NON_NARRATIVE = "DUPLICATE-ID-NON-NARRATIVE"
NARRATIVE_ID_DISAGREEMENT = "NARRATIVE-ID-DISAGREEMENT"
MIS_TAG = "MIS-TAG"
ID_LESS_TWIN = "ID-LESS-TWIN"
CONTENT_GAP = "CONTENT-GAP"
WHOLE_DECK_GAP = "WHOLE-DECK-GAP"
UNIFY_ALIGNMENT = "UNIFY-ALIGNMENT"
DROPPED_ID = "DROPPED-ID"

MECHANICAL = "MECHANICAL"
AUTHORING = "AUTHORING"


@dataclass(frozen=True)
class Diagnosis:
    """One classified finding: its root cause, the prescribed fix, and the evidence."""

    root_cause: str
    fix_class: str  # MECHANICAL | AUTHORING
    severity: str  # "error" | "warning"
    prescribed_fix: str
    evidence: dict
    slide_id: str | None = None
    role: str | None = None


@dataclass
class DiagnoseResult:
    """The classified verdict for one pair."""

    de_path: Path
    en_path: Path
    diagnoses: list[Diagnosis] = field(default_factory=list)
    git_baseline: bool = True

    @property
    def ok(self) -> bool:
        """True iff no *error*-severity diagnosis — warnings do not fail the gate."""
        return not any(d.severity == "error" for d in self.diagnoses)

    @property
    def mechanical(self) -> list[Diagnosis]:
        return [d for d in self.diagnoses if d.fix_class == MECHANICAL]


@dataclass
class ApplyDiagnoseResult:
    """The outcome of ``diagnose --apply`` (the mechanical narrative fixes only)."""

    de_path: Path
    en_path: Path
    written: bool = False
    refused: bool = False
    reconcile_changes: int = 0
    collapsed_duplicates: int = 0
    new_errors: list[str] = field(default_factory=list)
    residual: DiagnoseResult | None = None


def _id_cells(cells: list[RawCell]) -> dict[str, RawCell]:
    """First cell per ``slide_id`` (document order); id-less cells excluded."""
    out: dict[str, RawCell] = {}
    for cell in cells:
        sid = cell.metadata.slide_id
        if sid is not None and sid not in out:
            out[sid] = cell
    return out


def _idless_slide_count(cells: list[RawCell]) -> int:
    """Count of slide-start cells in a half that carry no ``slide_id`` (twin candidates)."""
    return sum(1 for c in cells if c.metadata.is_slide_start and c.metadata.slide_id is None)


def _classify_id_asymmetry(
    sid: str,
    de_cells: list[RawCell],
    en_cells: list[RawCell],
    de_ids: set[str],
) -> Diagnosis:
    """Disambiguate an ``id-asymmetry`` for ``sid`` into mis-tag / id-less-twin / gap.

    The affected half is recomputed from the id sets (the side is NOT a field on the
    violation), then the orphan cell's *content language* is compared against its
    ``lang=`` tag. The result is **always advisory** (never an auto-rewrite) and never
    suggests renaming an id — a content gap stays a gap.
    """
    orphan_half = "de" if sid in de_ids else "en"
    orphan_cells = de_cells if orphan_half == "de" else en_cells
    other_cells = en_cells if orphan_half == "de" else de_cells
    orphan = next((c for c in orphan_cells if c.metadata.slide_id == sid), None)
    base_evidence: dict = {"orphan_half": orphan_half}

    if orphan is None:  # pragma: no cover - defensive; the id came from these cells
        return Diagnosis(
            CONTENT_GAP,
            AUTHORING,
            "error",
            f"slide_id {sid!r} has no twin in the other half — author the missing twin.",
            base_evidence,
            slide_id=sid,
        )

    lang_tag = orphan.metadata.lang
    guess = detect(orphan.body)
    base_evidence |= {
        "lang_tag": lang_tag,
        "content_lang": guess.label,
        "content_lang_confidence": guess.confidence,
    }

    if guess.confident and lang_tag is not None and guess.label != lang_tag:
        return Diagnosis(
            MIS_TAG,
            AUTHORING,
            "error",
            f"the cell's content reads as {guess.label!r} but it is tagged lang={lang_tag!r}, "
            f"so the split routed it into the wrong half. Move it to the {guess.label!r} "
            f"half with the twin's slide_id and remove the stray copy (a routing bug). "
            "Confirm with `clm slides language-view`.",
            base_evidence,
            slide_id=sid,
        )

    idless = _idless_slide_count(other_cells)
    if idless > 0:
        ev = base_evidence | {"idless_slide_cells_in_other_half": idless}
        return Diagnosis(
            ID_LESS_TWIN,
            AUTHORING,
            "error",
            f"slide_id {sid!r} is missing from the other half, which has {idless} id-less "
            "slide cell(s) — one may be the un-tagged twin. If so, ADD "
            f'tags=[...] slide_id="{sid}" to that cell (no translation needed). Verify it '
            "is the right cell first — do NOT rename an unrelated slide to match.",
            ev,
            slide_id=sid,
        )

    return Diagnosis(
        CONTENT_GAP,
        AUTHORING,
        "error",
        f"slide_id {sid!r} exists only in the {orphan_half!r} half (no id-less twin "
        f"candidate in the other) — author the missing twin in the other language with "
        f"the same slide_id. This is a translation gap, not a mechanical fix; never "
        "rename an unrelated slide to silence it.",
        base_evidence,
        slide_id=sid,
    )


def _classify(de_text: str, en_text: str, token: str, vresult: VerifyResult) -> list[Diagnosis]:
    """Classify every verify violation plus the verify-invisible narrative disagreements."""
    _de_pre, de_cells = split_cells(de_text, token)
    _en_pre, en_cells = split_cells(en_text, token)
    de_ids = set(_id_cells(de_cells))

    # Whole-deck gap: combine the DE count from the DE half with the EN count from the
    # EN half (as scan_coverage does) — counting one half alone always reads as N/0.
    de_count = count_languages(de_text, token)[0]
    en_count = count_languages(en_text, token)[1]
    coverage = classify_counts(de_count, en_count)
    whole_deck_gap = coverage in (CoverageStatus.DE_ONLY, CoverageStatus.EN_ONLY)

    diagnoses: list[Diagnosis] = []
    if whole_deck_gap:
        present = "de" if coverage is CoverageStatus.DE_ONLY else "en"
        diagnoses.append(
            Diagnosis(
                WHOLE_DECK_GAP,
                AUTHORING,
                "error",
                f"the {present!r} half has slides ({de_count} de / {en_count} en) but the "
                "other half is empty — this is a whole-deck translation project, not a "
                "mechanical fix. Track it (e.g. a course-planning entry); per-slide "
                "id-asymmetries are suppressed here so they do not masquerade as N gaps.",
                {"de_slides": de_count, "en_slides": en_count, "coverage": coverage.value},
            )
        )

    # The verify-invisible flagship case: a narrative id'd on one half, id-less on the
    # other. Run reconcile's own occurrence pairing INDEPENDENTLY of any violation.
    _de2, _en2, rec = reconcile_voiceover_ids(de_text, en_text, token, token, direction=TO_IDLESS)
    reconcile_keys = {(c.owning_slide_id, c.role) for c in rec.changes}
    for change in rec.changes:
        diagnoses.append(
            Diagnosis(
                NARRATIVE_ID_DISAGREEMENT,
                MECHANICAL,
                "error",
                "a narrative cell is id'd on one half and id-less on the other; strip the "
                "id to the canonical id-less narration form (`clm slides sync diagnose "
                "--apply`, or `clm slides reconcile-vo-ids`).",
                {
                    "edited_half": change.lang,
                    "owning_slide_id": change.owning_slide_id,
                    "occurrence": change.occurrence,
                    "current_id": change.old_id,
                },
                slide_id=change.owning_slide_id,
                role=change.role,
            )
        )

    for v in vresult.violations:
        if v.kind == "unify":
            diagnoses.append(
                Diagnosis(
                    UNIFY_ALIGNMENT,
                    AUTHORING,
                    "error",
                    "the halves do not unify back into one bilingual source (a byte-diff in "
                    "a shared cell, a header/preamble mismatch, or a broken alignment). Read "
                    "the message, fix the offending cell, re-run.",
                    {"message": v.message},
                )
            )
        elif v.kind == "duplicate-id":
            if v.role in _NARRATIVE_ROLES:
                if (v.slide_id, v.role) in reconcile_keys:
                    continue  # the asymmetric case — already reported as a disagreement
                diagnoses.append(
                    Diagnosis(
                        DUPLICATE_NARRATION_OVERSTAMP,
                        MECHANICAL,
                        "error",
                        f"a slide has several {v.role!r} cells all keyed (slide_id={v.slide_id!r}, "
                        f"role={v.role!r}) — assign-ids over-stamped each. Strip them to the "
                        "canonical id-less narration form (`clm slides sync diagnose --apply`).",
                        {"message": v.message},
                        slide_id=v.slide_id,
                        role=v.role,
                    )
                )
            else:
                diagnoses.append(
                    Diagnosis(
                        DUPLICATE_ID_NON_NARRATIVE,
                        AUTHORING,
                        "error",
                        f"a non-narrative (slide_id={v.slide_id!r}, role={v.role!r}) key is "
                        "duplicated within a half — a real structural problem. Resolve it by "
                        "hand (a stray copy, or a mis-keyed cell); never auto-strip a slide id.",
                        {"message": v.message},
                        slide_id=v.slide_id,
                        role=v.role,
                    )
                )
        elif v.kind == "id-asymmetry":
            if whole_deck_gap:
                continue  # one whole-deck finding, not N per-slide gaps
            diagnoses.append(_classify_id_asymmetry(v.slide_id or "", de_cells, en_cells, de_ids))
        elif v.kind == "dropped-id":
            diagnoses.append(
                Diagnosis(
                    DROPPED_ID,
                    AUTHORING,
                    "warning",
                    "an id'd cell present at git HEAD is gone now — confirm the removal was "
                    "intentional (a deliberate removal is fine; an accidental drop is not).",
                    {"message": v.message},
                    slide_id=v.slide_id,
                )
            )
    return diagnoses


def diagnose_pair(de_path: Path, en_path: Path) -> DiagnoseResult:
    """Diagnose a resolved DE/EN split pair (reads the files + git HEAD, no model)."""
    token = comment_token_for_path(de_path)
    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    vresult = verify_pair(de_path, en_path)
    diagnoses = _classify(de_text, en_text, token, vresult)
    return DiagnoseResult(de_path, en_path, diagnoses, git_baseline=vresult.git_baseline)


def apply_mechanical_fixes(de_path: Path, en_path: Path) -> ApplyDiagnoseResult:
    """Apply ONLY the identity-preserving narrative fixes, re-gated by structure (#455).

    Two passes, both narrative-only and collision-proof: (1) ``reconcile_voiceover_ids``
    (``TO_IDLESS``) strips an *asymmetric* narration id so the halves agree id-less;
    (2) ``collapse_intra_half_duplicates`` strips a *symmetric* over-stamp. The write is
    refused only if it would introduce a NEW structural error (it never should — strip
    is identity-preserving — but the gate is the fail-safe). Unrelated authoring findings
    (a content gap, a slide mis-tag) are left untouched and surface in ``residual``; the
    fix is best-effort, not a guarantee the whole pair becomes clean.
    """
    token = comment_token_for_path(de_path)
    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")

    before = {(v.kind, v.slide_id, v.role) for v in structural_gate(de_text, en_text, token)}

    de1, en1, rec = reconcile_voiceover_ids(de_text, en_text, token, token, direction=TO_IDLESS)
    de2, en2, collapsed = collapse_intra_half_duplicates(de1, en1, token, token)

    after = {(v.kind, v.slide_id, v.role) for v in structural_gate(de2, en2, token)}
    new_errors = sorted(f"{kind} {sid}/{role}" for (kind, sid, role) in (after - before))
    if new_errors:
        return ApplyDiagnoseResult(
            de_path,
            en_path,
            written=False,
            refused=True,
            reconcile_changes=len(rec.changes),
            collapsed_duplicates=collapsed,
            new_errors=new_errors,
        )

    written = False
    if de2 != de_text:
        de_path.write_text(de2, encoding="utf-8")
        written = True
    if en2 != en_text:
        en_path.write_text(en2, encoding="utf-8")
        written = True

    return ApplyDiagnoseResult(
        de_path,
        en_path,
        written=written,
        refused=False,
        reconcile_changes=len(rec.changes),
        collapsed_duplicates=collapsed,
        residual=diagnose_pair(de_path, en_path),
    )
