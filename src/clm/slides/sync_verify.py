"""Deterministic structural verification of a split DE/EN deck pair (no LLM).

``clm slides sync --verify`` answers exactly one question: *did an edit corrupt
the structural invariants of a split pair?* It is deliberately narrower than the
two neighbouring questions:

* *Is the pair in sync?* — that is ``--dry-run`` (it classifies pending drift).
* *Is the translation good?* — that is a **semantic** judgement, an LLM call CI
  forbids. ``--verify`` is **structural safety, not semantic correctness**: a
  green verify proves the edit did not *corrupt* the deck, not that a translation
  is *faithful*.

This is the deterministic safety net that makes handing the hard cases of sync to
an agent trustworthy — the agent reconciles the ambiguous residue, then
``--verify`` confirms the result is a structurally valid pair before it is
trusted. It needs no watermark and no model, so it is also the gate that keeps
sync-correctness out of CI's LLM budget (run ``--verify`` in CI; never ``sync``).

**Core check — pure reuse of** :func:`~clm.slides.split.unify_texts`. A validly
split pair unifies back into one bilingual source, so "does it unify?" catches
the bulk of the structural-invariant set: byte-identical shared (language-neutral)
cells, a matching preamble/header, and a clean cell alignment with nothing left
over. A :class:`~clm.slides.split.UnifyError` is exactly one such violation, and
its message names the offending DE/EN line.

**Explicit id checks — what unify does *not* enforce.** ``unify_texts`` only
*uses* ``slide_id`` to decide pairing: a mismatched ``de_id`` / ``en_id`` quietly
degrades to two separate cells rather than erroring. So the ``de_id == en_id``
invariant is enforced here directly — the set of slide_ids in the DE half must
equal the set in the EN half (a localized id'd cell appears once in each half; a
neutral id'd cell appears byte-identically in both — both are symmetric), and an
id must not be *duplicated* within a half (sync keys on it). An asymmetric or
duplicated id is a corruption unify cannot see.

**Secondary check — no accidental drop vs the git pre-edit version.** A
``slide_id`` present in the committed (HEAD) half but gone from the working tree
is a candidate *accidental* drop. Because a deliberate slide removal is
legitimate and verify cannot read intent, this is a **warning** — it never fails
the gate. It is the only check that touches git and degrades silently to
"skipped" when the pair is untracked. It is id-based: an id-*less* cell drop is
invisible here (a known limitation — and, per the design note, the concrete
trigger for *selectively* adding ids where matching proves fragile).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.raw_cells import split_cells
from clm.slides.split import UnifyError, unify_texts
from clm.slides.sync_plan import _git_ref_text


@dataclass(frozen=True)
class VerifyViolation:
    """One structural finding. ``severity`` gates the exit code.

    ``error`` — a corruption the pair cannot be trusted with (a ``UnifyError``);
    fails the gate (exit 2). ``warning`` — flagged but not fatal (a candidate
    accidental drop); informational, the exit code is unaffected.
    """

    severity: str  # "error" | "warning"
    kind: str  # "unify" | "dropped-id"
    message: str
    slide_id: str | None = None


@dataclass
class VerifyResult:
    """The structural verdict for one pair."""

    de_path: Path
    en_path: Path
    violations: list[VerifyViolation] = field(default_factory=list)
    # False when the no-drop check could not run (pair untracked at HEAD / git
    # absent), so a consumer can distinguish "no drops" from "not checked".
    git_baseline: bool = True

    @property
    def errors(self) -> list[VerifyViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[VerifyViolation]:
        return [v for v in self.violations if v.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True iff no *error*-severity violation — warnings do not fail the gate."""
        return not self.errors


def structural_violations(de_text: str, en_text: str, comment_token: str) -> list[VerifyViolation]:
    """All structural-corruption findings for a pair (every check, enumerated).

    Combines three deterministic checks: (1) :func:`unify_texts` as the
    byte-identity / header / alignment oracle (its first ``UnifyError`` is surfaced
    verbatim — the message names the offending DE/EN line); (2) slide_id **set
    symmetry** between the halves (the ``de_id == en_id`` invariant unify does not
    enforce); (3) **no duplicate** slide_id within a half. Unlike unify (which stops
    at the first mismatch), the id checks enumerate every offender so the caller can
    fix them in one pass.
    """
    violations: list[VerifyViolation] = []
    try:
        unify_texts(de_text, en_text, comment_token)
    except UnifyError as exc:
        violations.append(VerifyViolation(severity="error", kind="unify", message=str(exc)))

    de_ids = _slide_id_list(de_text, comment_token)
    en_ids = _slide_id_list(en_text, comment_token)
    violations.extend(_id_symmetry_violations(de_ids, en_ids))
    violations.extend(_duplicate_id_violations(de_ids, "DE"))
    violations.extend(_duplicate_id_violations(en_ids, "EN"))
    return violations


def _id_symmetry_violations(de_ids: list[str], en_ids: list[str]) -> list[VerifyViolation]:
    """Flag every slide_id present in one half's id'd cells but not the other's.

    The ``de_id == en_id`` invariant: a localized id'd cell must have its twin under
    the same id in the other half, and a neutral id'd cell is byte-identical in both
    — so the two id *sets* must match. An id-less cell carries no key and is excluded
    (its drop is invisible here — the known limitation).
    """
    de_set, en_set = set(de_ids), set(en_ids)
    out: list[VerifyViolation] = []
    for sid in sorted(de_set - en_set):
        out.append(
            VerifyViolation(
                severity="error",
                kind="id-asymmetry",
                message=(
                    f"slide_id {sid!r} is in the DE half but has no twin in EN "
                    "(broken de_id == en_id pairing — a dropped or mis-keyed twin)"
                ),
                slide_id=sid,
            )
        )
    for sid in sorted(en_set - de_set):
        out.append(
            VerifyViolation(
                severity="error",
                kind="id-asymmetry",
                message=(
                    f"slide_id {sid!r} is in the EN half but has no twin in DE "
                    "(broken de_id == en_id pairing — a dropped or mis-keyed twin)"
                ),
                slide_id=sid,
            )
        )
    return out


def _duplicate_id_violations(ids: list[str], half: str) -> list[VerifyViolation]:
    """Flag every slide_id that appears more than once within one half."""
    return [
        VerifyViolation(
            severity="error",
            kind="duplicate-id",
            message=(
                f"slide_id {sid!r} appears {n} times in the {half} half — ids must be "
                "unique within a half (sync keys on them)"
            ),
            slide_id=sid,
        )
        for sid, n in sorted(Counter(ids).items())
        if n > 1
    ]


def _slide_id_list(text: str, comment_token: str) -> list[str]:
    """Every cell's slide_id (with duplicates, in document order); id-less skipped."""
    _preamble, cells = split_cells(text, comment_token)
    return [c.metadata.slide_id for c in cells if c.metadata.slide_id]


def dropped_id_violations(
    head_text: str, current_text: str, comment_token: str, half: str
) -> list[VerifyViolation]:
    """Warn for each id'd cell present at ``head_text`` but gone from ``current_text``.

    ``half`` is a human label (``"DE"`` / ``"EN"``) for the message. Id-less cells
    are not tracked here (they carry no stable key across an edit).
    """
    head_ids = set(_slide_id_list(head_text, comment_token))
    current_ids = set(_slide_id_list(current_text, comment_token))
    gone = head_ids - current_ids
    return [
        VerifyViolation(
            severity="warning",
            kind="dropped-id",
            message=(
                f"slide_id {sid!r} was present in the {half} half at HEAD but is gone "
                "now — confirm the removal was intentional (a deliberate slide removal "
                "is fine; an accidental drop is not)"
            ),
            slide_id=sid,
        )
        for sid in sorted(gone)
    ]


def verify_pair(de_path: Path, en_path: Path) -> VerifyResult:
    """Structurally verify a resolved DE/EN split pair (reads the files + git HEAD).

    Reuses the split language's comment token (both halves share a programming
    language, so the DE token serves both). The no-drop check is best-effort: it
    runs against whichever halves are tracked at HEAD and is skipped entirely
    (``git_baseline=False``) when neither is.
    """
    comment_token = comment_token_for_path(de_path)
    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")

    violations = structural_violations(de_text, en_text, comment_token)

    git_baseline = False
    for path, current, half in ((de_path, de_text, "DE"), (en_path, en_text, "EN")):
        head_text = _git_ref_text(path, "HEAD")
        if head_text is None:
            continue
        git_baseline = True
        violations.extend(dropped_id_violations(head_text, current, comment_token, half))

    return VerifyResult(
        de_path=de_path, en_path=en_path, violations=violations, git_baseline=git_baseline
    )
