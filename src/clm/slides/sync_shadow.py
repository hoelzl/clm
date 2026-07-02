"""Shadow mode: run the v2 report engine and the v3 differ side by side.

Sync v3 Phase 2 (#520, design §11): before the v3 engine may ever write, it
must *match or beat* v2 on the corpus and the scripted mutation scenarios —
``report v3 --shadow`` runs both engines over the same pair at the same git
baseline and diffs the verdicts. The W10 dogfood replay (52 pairs at the
pre-edit ref, v2: 73 flagged items, ground truth: 3 genuine changes) is the
exit gate: the v3 column must read ~3.

This module is the **comparison harness**, not part of the v3 core: it
imports both engines by design and is deleted (or repurposed as a triage
tool) with the v2 core in Phase 4. It must never appear in the §12.5
import-cleanliness allowlist for v3 modules.

Both engines read the same inputs:

* v2 — :func:`clm.slides.sync_plan.build_sync_plan` with ``baseline_ref``
  (no watermark, no ledger: the deterministic explicit-ref path) projected
  through :func:`clm.slides.sync_report.build_report`.
* v3 — the ≤4-file bundle at the ref parsed into a
  :class:`~clm.slides.bilingual_doc.BilingualDeck` and snapshotted via
  :func:`~clm.slides.sync_diff.baseline_from_deck`; the working tree parsed
  the same way; one :func:`~clm.slides.sync_diff.diff_deck`.

Nothing here writes: shadow is read-only by construction.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections import Counter
from pathlib import Path

from attrs import define, field

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.doc_lenses import load_bundle, parse_bundle
from clm.slides.pairing import (
    derive_split_pair,
    derive_split_pair_from_stem,
    find_split_slide_files_recursive,
    iter_split_pairs,
)
from clm.slides.sync_diff import DeckDiff, baseline_from_deck, diff_deck
from clm.slides.voiceover_tools import COMPANION_SUBDIR, companion_name

logger = logging.getLogger(__name__)

__all__ = [
    "ShadowPair",
    "ShadowReport",
    "bundle_texts_at_ref",
    "shadow_pair",
    "shadow_scope",
]


# ---------------------------------------------------------------------------
# Reading the bundle at a git ref
# ---------------------------------------------------------------------------


def _git_capture(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:  # pragma: no cover - git missing entirely
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _repo_root(path: Path) -> Path | None:
    out = _git_capture(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def _text_at_ref(root: Path, path: Path, ref: str) -> str | None:
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    return _git_capture(root, "show", f"{ref}:{rel}")


def bundle_texts_at_ref(
    de_path: Path, en_path: Path, ref: str
) -> tuple[str | None, str | None, str | None, str | None]:
    """The ≤4-file bundle texts at ``ref`` (``None`` = absent at that ref).

    Companions are resolved through the same subdir-then-sibling precedence
    as :func:`clm.slides.voiceover_tools.resolve_companion`, but against the
    ref's tree instead of the working tree.
    """
    root = _repo_root(de_path)
    if root is None:
        return None, None, None, None

    def companion_at_ref(deck_path: Path) -> str | None:
        name = companion_name(deck_path)
        for candidate in (
            deck_path.parent / COMPANION_SUBDIR / name,
            deck_path.with_name(name),
        ):
            text = _text_at_ref(root, candidate, ref)
            if text is not None:
                return text
        return None

    return (
        _text_at_ref(root, de_path, ref),
        _text_at_ref(root, en_path, ref),
        companion_at_ref(de_path),
        companion_at_ref(en_path),
    )


# ---------------------------------------------------------------------------
# The per-pair shadow verdict
# ---------------------------------------------------------------------------


@define
class ShadowPair:
    """Both engines' verdicts over one deck pair at one baseline."""

    de_path: Path
    en_path: Path
    v2_items: list[dict] = field(factory=list)
    v2_in_sync: bool = False
    v2_error: str | None = None
    v3: DeckDiff | None = None
    v3_base_refusal: list[str] = field(factory=list)
    v3_error: str | None = None

    @property
    def v2_count(self) -> int:
        return len(self.v2_items)

    @property
    def v3_count(self) -> int:
        if self.v3 is None:
            return 0
        n = len(self.v3.items)
        if self.v3.refusal is not None:
            n += 1  # the framed "run normalize" deck item
        return n

    @property
    def agrees_clean(self) -> bool:
        return self.v2_in_sync and self.v3 is not None and self.v3.is_clean

    def payload(self) -> dict:
        return {
            "de_path": str(self.de_path),
            "en_path": str(self.en_path),
            "v2": {
                "in_sync": self.v2_in_sync,
                "count": self.v2_count,
                "error": self.v2_error,
                "items": self.v2_items,
            },
            "v3": {
                "count": self.v3_count,
                "error": self.v3_error,
                "base_refusal": self.v3_base_refusal,
                "report": self.v3.to_payload() if self.v3 else None,
            },
        }


@define
class ShadowReport:
    """The whole shadow sweep, with the summary the triage reads."""

    baseline_ref: str
    pairs: list[ShadowPair] = field(factory=list)

    def summary(self) -> dict:
        v2_total = sum(p.v2_count for p in self.pairs)
        v3_total = sum(p.v3_count for p in self.pairs)
        v2_kinds: Counter[str] = Counter()
        v3_actions: Counter[str] = Counter()
        for pair in self.pairs:
            v2_kinds.update(i.get("kind", "?") for i in pair.v2_items)
            if pair.v3 is not None:
                v3_actions.update(i.action for i in pair.v3.items)
                if pair.v3.refusal is not None:
                    v3_actions["run_normalize"] += 1
        return {
            "baseline": self.baseline_ref,
            "pairs": len(self.pairs),
            "agree_clean": sum(p.agrees_clean for p in self.pairs),
            "v2_total": v2_total,
            "v3_total": v3_total,
            "v2_kinds": dict(sorted(v2_kinds.items())),
            "v3_actions": dict(sorted(v3_actions.items())),
            "errors": {
                "v2": sum(1 for p in self.pairs if p.v2_error),
                "v3": sum(1 for p in self.pairs if p.v3_error),
            },
        }

    def to_payload(self) -> dict:
        return {
            "schema": 3,
            "mode": "shadow",
            "summary": self.summary(),
            "pairs": [p.payload() for p in self.pairs],
        }

    def render_text(self) -> str:
        lines = [f"shadow @ {self.baseline_ref}: v2 vs v3 over {len(self.pairs)} pair(s)"]
        for pair in self.pairs:
            marks: list[str] = []
            if pair.v2_error:
                marks.append(f"v2 ERROR: {pair.v2_error}")
            if pair.v3_error:
                marks.append(f"v3 ERROR: {pair.v3_error}")
            if pair.v3 is not None and pair.v3.refusal is not None:
                codes = ",".join(sorted({r.code for r in pair.v3.refusal.reasons}))
                marks.append(f"v3 refuses ({codes})")
            if pair.v3_base_refusal:
                marks.append(f"base refuses ({','.join(sorted(set(pair.v3_base_refusal)))})")
            status = "; ".join(marks) if marks else ("clean" if pair.agrees_clean else "")
            lines.append(
                f"  {pair.de_path.name}: v2={pair.v2_count:3d}  v3={pair.v3_count:3d}"
                + (f"  [{status}]" if status else "")
            )
            if pair.v3 is not None:
                for item in pair.v3.items:
                    lines.append(
                        f"      v3 {item.outcome}/{item.action} {item.key} "
                        f"({item.direction}) {item.detail[:60]}"
                    )
        summary = self.summary()
        lines.append(
            f"TOTAL: v2={summary['v2_total']} v3={summary['v3_total']} "
            f"agree-clean={summary['agree_clean']}/{summary['pairs']}"
        )
        lines.append(f"v2 kinds:   {json.dumps(summary['v2_kinds'])}")
        lines.append(f"v3 actions: {json.dumps(summary['v3_actions'])}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Running the two engines
# ---------------------------------------------------------------------------


def _run_v2(pair: ShadowPair, ref: str) -> None:
    # Deliberately local: the v2 core loads only inside the shadow harness,
    # never through clm.slides.sync_diff (design §12.5).
    from clm.slides.sync_plan import build_sync_plan
    from clm.slides.sync_report import build_report

    try:
        plan = build_sync_plan(
            pair.de_path,
            pair.en_path,
            baseline_ref=ref,
            allow_git_fallback=False,
            # The real `sync report` surface hardcodes provider_available=True
            # (the #438 read-surface convention) — the shadow's v2 column must
            # match the verdicts it claims to compare against.
            provider_available=True,
        )
        report = build_report(plan)
    except Exception as exc:  # noqa: BLE001 - the sweep must survive any pair
        pair.v2_error = f"{type(exc).__name__}: {exc}"
        return
    # The verdict, not the in-sync CELL COUNT (`report.in_sync` is an int).
    pair.v2_in_sync = bool(report.is_clean)
    pair.v2_items = [
        {
            "item": item.item,
            "tier": item.tier,
            "kind": item.kind,
            "slide_id": item.slide_id,
            "direction": item.direction,
            "reason": item.reason,
        }
        for bucket in (report.mechanical, report.assisted, report.ambiguity)
        for item in bucket
    ]


def _run_v3(pair: ShadowPair, ref: str) -> None:
    try:
        base_de, base_en, base_de_c, base_en_c = bundle_texts_at_ref(
            pair.de_path, pair.en_path, ref
        )
        base = None
        if base_de is not None and base_en is not None:
            comment_token = comment_token_for_path(pair.de_path)
            base_outcome = parse_bundle(
                base_de, base_en, base_de_c, base_en_c, comment_token=comment_token
            )
            if base_outcome.refusal is not None:
                pair.v3_base_refusal = [r.code for r in base_outcome.refusal.reasons]
            elif base_outcome.deck is not None:
                base = baseline_from_deck(base_outcome.deck)
        bundle = load_bundle(pair.de_path, pair.en_path)
        if bundle.outcome.refusal is not None:
            pair.v3 = DeckDiff(refusal=bundle.outcome.refusal)
            return
        assert bundle.outcome.deck is not None
        pair.v3 = diff_deck(bundle.outcome.deck, base)
    except Exception as exc:  # noqa: BLE001 - the sweep must survive any pair
        pair.v3_error = f"{type(exc).__name__}: {exc}"


def shadow_pair(de_path: Path, en_path: Path, baseline_ref: str) -> ShadowPair:
    """Run both engines over one pair at ``baseline_ref``."""
    pair = ShadowPair(de_path=de_path, en_path=en_path)
    _run_v2(pair, baseline_ref)
    _run_v3(pair, baseline_ref)
    return pair


def shadow_scope(scope: Path, baseline_ref: str) -> ShadowReport:
    """Run the shadow sweep over a deck half/stem or a directory."""
    report = ShadowReport(baseline_ref=baseline_ref)
    if scope.is_dir():
        pairs, _solos = iter_split_pairs(find_split_slide_files_recursive(scope))
    else:
        pair = derive_split_pair(scope) or derive_split_pair_from_stem(scope)
        if pair is None:
            raise ValueError(f"{scope} is not a split deck half/stem with an existing twin")
        pairs = [pair]
    for de_path, en_path in pairs:
        report.pairs.append(shadow_pair(de_path, en_path, baseline_ref))
    return report
