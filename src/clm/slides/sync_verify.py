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
neutral id'd cell appears byte-identically in both — both are symmetric), and a
``(slide_id, role)`` key must not be *duplicated* within a half. Uniqueness is on
the **pair**, not the bare id — the engine reconciles cells *per (slide_id, role)*
(:func:`~clm.slides.sync_writeback.role_of`), so a slide and its inline
``voiceover`` / ``notes`` narrative companion **share** a ``slide_id`` under
different roles by design (that is the standard pattern, not a collision). An
asymmetric id, or a duplicated ``(slide_id, role)`` key, is a corruption unify
cannot see.

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
from clm.slides.sync_writeback import role_of


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
    # The cell role this finding is keyed to, when the check is per-(slide_id, role)
    # (only ``duplicate-id`` today). ``None`` means role-agnostic — a slide-level
    # finding (``id-asymmetry``) or a whole-deck one (``unify``). Used by
    # :func:`structural_gate` to scope a per-slide write gate; it is *not* part of
    # the CLI ``verify`` output (the JSON/human serializers enumerate fields
    # explicitly), so adding it does not change that surface.
    role: str | None = None


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

    de_keys = _slide_id_role_list(de_text, comment_token)
    en_keys = _slide_id_role_list(en_text, comment_token)
    violations.extend(
        _id_symmetry_violations([sid for sid, _role in de_keys], [sid for sid, _role in en_keys])
    )
    violations.extend(_duplicate_id_violations(de_keys, "DE"))
    violations.extend(_duplicate_id_violations(en_keys, "EN"))
    return violations


def structural_gate(
    de_text: str,
    en_text: str,
    comment_token: str,
    *,
    slide_id: str | None = None,
    role: str | None = None,
) -> list[VerifyViolation]:
    """Error-severity structural violations, optionally scoped to one ``(slide_id, role)``.

    The reusable **write-gate** (Issue #455): a ledger / watermark write must never
    record a pair — or a single slide — as in-sync while it fails a structural
    invariant, because that would mask a genuine divergence as "trusted". The return
    value is the *error* subset of :func:`structural_violations` — the exact
    invariants ``clm slides sync verify`` enforces, computed from the **same**
    function, so the gate and the CLI can never drift. An **empty list means "safe to
    record"**; a non-empty list is the reason it is not. It works on in-memory text
    (no file/git read), so the apply path can gate a watermark write on the
    post-apply :class:`~clm.slides.split` state without a re-read.

    **Whole-deck** (``slide_id is None``): every structural error in the pair — the
    gate for batch ``bless`` and the apply-path full watermark write.

    **Scoped** (``slide_id`` given): only the errors attributable to that ``slide_id``
    — the per-slide guard for ``accept --record`` (design note §11.4), where a
    corruption *elsewhere* in the deck must not block recording the one slide an agent
    just reconciled. With ``role`` also given, a duplicate-``(slide_id, role)`` error
    is limited to that exact role (a slide and its inline ``voiceover`` / ``notes``
    companion legitimately share an id under different roles); role-agnostic errors —
    a missing twin (``id-asymmetry``) — always apply to the slide. The whole-deck
    byte-identity oracle (``unify``) carries no ``slide_id``, so it is reported only
    in the whole-deck mode; a scoped call relies on the id symmetry / uniqueness
    checks, which is sufficient because an id'd cell is *localized* (its halves differ
    by translation by design) — byte-identity governs only the id-less neutral cells,
    which carry no ``slide_id`` to scope to.
    """
    errors = [
        v for v in structural_violations(de_text, en_text, comment_token) if v.severity == "error"
    ]
    if slide_id is None:
        return errors
    return [v for v in errors if _scoped_to(v, slide_id, role)]


def _scoped_to(violation: VerifyViolation, slide_id: str, role: str | None) -> bool:
    """Whether ``violation`` falls within the ``(slide_id, role)`` scope of a per-slide gate.

    A violation is in scope only if it names this ``slide_id``. When ``role`` is also
    given, a role-keyed finding (``duplicate-id``, which carries a ``role``) must match
    that role, while a role-agnostic finding (``role is None`` — e.g. ``id-asymmetry``)
    always applies to the slide. When ``role`` is ``None`` the gate covers every role
    under the ``slide_id``.
    """
    if violation.slide_id != slide_id:
        return False
    if role is None:
        return True
    return violation.role is None or violation.role == role


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


def _duplicate_id_violations(
    keys: list[tuple[str, str | None]], half: str
) -> list[VerifyViolation]:
    """Flag every ``(slide_id, role)`` key that appears more than once within one half.

    Uniqueness is on the **(slide_id, role) pair** — the engine's keying unit
    (:func:`role_of`: "cells reconciled per (slide_id, role)"), the same key the
    watermark stores — **not** the bare id. A slide and its inline ``voiceover`` /
    ``notes`` narrative companion legitimately share a ``slide_id`` under different
    roles; that is the standard pattern, not a collision (keying on the bare id
    wrongly flagged every such slide). Two cells with the same id *and* role are a
    true duplicate that would mis-key the sync.
    """
    out: list[VerifyViolation] = []
    for (sid, role), n in sorted(Counter(keys).items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        if n > 1:
            role_label = role if role is not None else "no-role"
            out.append(
                VerifyViolation(
                    severity="error",
                    kind="duplicate-id",
                    message=(
                        f"slide_id {sid!r} (role {role_label!r}) appears {n} times in the "
                        f"{half} half — the (slide_id, role) key must be unique within a half "
                        "(sync keys on it)"
                    ),
                    slide_id=sid,
                    role=role,
                )
            )
    return out


def _slide_id_list(text: str, comment_token: str) -> list[str]:
    """Every cell's slide_id (with duplicates, in document order); id-less skipped."""
    _preamble, cells = split_cells(text, comment_token)
    return [c.metadata.slide_id for c in cells if c.metadata.slide_id]


def _slide_id_role_list(text: str, comment_token: str) -> list[tuple[str, str | None]]:
    """Every id'd cell's ``(slide_id, role)`` key (with duplicates, in document order).

    Mirrors the engine's keying unit (:func:`role_of`): a slide and its ``voiceover``
    / ``notes`` companion share a ``slide_id`` under different roles, so uniqueness is
    on the *pair*. Id-less cells carry no key and are skipped.
    """
    _preamble, cells = split_cells(text, comment_token)
    return [(c.metadata.slide_id, role_of(c.metadata)) for c in cells if c.metadata.slide_id]


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
