"""Bounded LLM (Opus) *alignment* recovery for the sync engine.

Issue #190 §10 / Phase 5. The deterministic id-migration (§9,
:func:`clm.slides.sync_apply._migrate_drifted_ids`) moves a ``slide_id`` that
drifted off its construct back onto the right cell — but only when the move is
*unambiguous* (a unique, new, construct-matched cell). The genuine residue —
a function renamed in the same edit that split a cell, a true N:1 merge/split,
ambiguous ties (two ``def my_fun``, many bare imports) — is left for this tier.

The recovery is deliberately **bounded**:

- **Body-free.** The model never sees cell source. It is given two ordered lists
  of code cells described only by their content **anchor** components — an AST
  ``construct`` slug, a ``content_hash``, and any ``slide_id`` — and returns an
  ``id ↔ cell`` *map*, never free-form edits. The map is applied deterministically
  by the engine, so the LLM can only *re-identify* cells, never rewrite them.
- **Validated.** Every returned map is checked by :func:`validate_alignment`
  (total over the current cells; ids only from the base set; injective on base
  ids; provably-unchanged cells pinned to their old id). Any failure raises
  :class:`AlignmentInvalid`, and the caller **safe-aborts to no-change-plus-flag**
  — a wrong id is worse than a deferred one.
- **Cached.** Keyed by ``(base_region_fingerprint, current_region_fingerprint,
  prompt_version)`` (:class:`clm.infrastructure.llm.cache.SyncAlignmentCache`),
  so a re-run over the same region never re-spends on the LLM. The fingerprint is
  the *exact* body-free serialization the model sees, so the cache key is sound.
- **Opt-in.** ``clm slides sync --llm-recover`` (default off); without it an
  ambiguous region is simply left untouched and re-surfaces next run.

The engine depends only on the :class:`AlignmentRecoverer` protocol, so tests
drive it with :class:`StaticAlignmentRecoverer` and never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from clm.infrastructure.llm.retry import call_with_retries

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RECOVERY_MODEL",
    "NEW",
    "NONE",
    "RECOVERY_PROMPT_VERSION",
    "AlignmentInvalid",
    "AlignmentRecoverer",
    "OpenRouterAlignmentRecoverer",
    "RecoveryError",
    "RegionCell",
    "StaticAlignmentRecoverer",
    "build_recovery_user_prompt",
    "decode_mapping",
    "encode_mapping",
    "region_fingerprint",
    "validate_alignment",
]

# Claude Opus via OpenRouter — the design's "escalate to Claude (Opus)" tier. A
# distinct constant (cf. the Sonnet judge/translator) so the rare, harder
# alignment call can be tuned independently (Issue #167).
DEFAULT_RECOVERY_MODEL = "anthropic/claude-opus-4"
RECOVERY_PROMPT_VERSION = "recover-v1"

# The two non-id assignments a recoverer may return for a current cell.
NEW = "new"  # genuinely new content → mint a fresh content slug from its construct
NONE = "none"  # the cell should remain without a slide_id


class RecoveryError(Exception):
    """The recoverer could not produce a map (transport/parse failure)."""


class AlignmentInvalid(Exception):
    """A returned map failed validation; the caller must safe-abort (no change)."""


@dataclass(frozen=True)
class RegionCell:
    """One code cell in an alignment region, described **body-free**.

    ``slide_id`` is the id the cell carries (in the current region) or carried (in
    the base region), or ``None``. ``construct`` is the AST construct slug from
    :func:`clm.slides.sync_writeback.construct_of` (``None`` for unnameable cells).
    ``content_hash`` is :func:`clm.slides.sync_writeback.cell_content_hash` of the
    body — it stands in for the body so the model (and the cache key) never need
    the source.
    """

    slide_id: str | None
    construct: str | None
    content_hash: str


@runtime_checkable
class AlignmentRecoverer(Protocol):
    """Re-identifies the cells of an ambiguous code region.

    ``prompt_version`` participates in the cache key so a prompt/model change
    invalidates stale maps.
    """

    prompt_version: str

    def recover(
        self,
        *,
        base_region: list[RegionCell],
        current_region: list[RegionCell],
    ) -> dict[int, str]:
        """Return ``{current_index: assignment}`` for every current cell.

        ``assignment`` is a base ``slide_id``, :data:`NEW`, or :data:`NONE`. Raises
        :class:`RecoveryError` on failure. The map is *not* trusted — the caller
        validates it with :func:`validate_alignment` before applying.
        """
        ...


# ---------------------------------------------------------------------------
# Region fingerprint + map (de)serialization — the cache-key + storage halves.
# ---------------------------------------------------------------------------


def region_fingerprint(region: list[RegionCell]) -> str:
    """Stable sha256 over the body-free region serialization.

    Captures *exactly* what the recoverer sees — the ordered ``(slide_id,
    construct, content_hash)`` triples — so a cached map is a sound function of the
    two region fingerprints. ``content_hash`` already encodes the body uniquely, so
    the fingerprint is body-sensitive without exposing any source.
    """
    payload = [[c.slide_id, c.construct, c.content_hash] for c in region]
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def encode_mapping(mapping: dict[int, str]) -> str:
    """Serialize a ``{int: str}`` map to canonical JSON (string keys, sorted)."""
    return json.dumps(
        {str(k): mapping[k] for k in sorted(mapping)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_mapping(text: str) -> dict[int, str]:
    """Parse a JSON object back into a ``{int: str}`` map.

    Raises :class:`AlignmentInvalid` on any malformed payload (a non-object, a
    non-integer key, or a non-string value) so a corrupt cache row or a stray LLM
    response is treated as a validation failure, not a crash.
    """
    try:
        raw = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AlignmentInvalid(f"alignment map is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise AlignmentInvalid("alignment map must be a JSON object")
    out: dict[int, str] = {}
    for key, value in raw.items():
        try:
            idx = int(key)
        except (ValueError, TypeError) as exc:
            raise AlignmentInvalid(f"alignment key {key!r} is not an integer") from exc
        if not isinstance(value, str):
            raise AlignmentInvalid(f"alignment value for {key!r} is not a string")
        out[idx] = value
    return out


# ---------------------------------------------------------------------------
# Validation — the load-bearing safety net (safe-abort on ANY failure).
# ---------------------------------------------------------------------------


def _pinned_assignments(base_region: list[RegionCell]) -> dict[str, str]:
    """``content_hash → required assignment`` for base cells a hash can pin.

    A current cell byte-identical to an *unchanged* base cell is provably
    unchanged and may not be re-identified by the model. The pin is
    **bidirectional**:

    - a hash that uniquely identifies an **id'd** base cell pins its ``slide_id``
      (an unchanged id'd cell keeps its id), and
    - a hash that uniquely identifies an **id-less** base cell pins :data:`NONE`
      (an unchanged id-less cell stays id-less — the model may not mint a spurious
      id onto unchanged content, which would re-introduce the churn the whole
      design avoids).

    A hash shared by two base cells, or by an id'd and an id-less cell, is
    ambiguous and is excluded — the recurring content-anchor non-uniqueness guard
    (cf. the Counter / ordered-sequence guards elsewhere). Only genuinely *changed*
    cells (a new content hash) are left for the model to decide.
    """
    by_hash: dict[str, set[str | None]] = {}
    for cell in base_region:
        by_hash.setdefault(cell.content_hash, set()).add(cell.slide_id)
    pinned: dict[str, str] = {}
    for chash, ids in by_hash.items():
        if len(ids) == 1:
            only = next(iter(ids))
            pinned[chash] = only if only is not None else NONE
    return pinned


def validate_alignment(
    mapping: dict[int, str],
    base_region: list[RegionCell],
    current_region: list[RegionCell],
) -> dict[int, str]:
    """Return ``mapping`` unchanged if sound, else raise :class:`AlignmentInvalid`.

    The contract the engine relies on before it will apply an LLM-derived map
    (Issue #190 §10). Every check is a *hard* gate — a single failure safe-aborts
    the whole recovery to no-change-plus-flag:

    1. **Total & well-formed** — the keys are exactly ``{0 … len(current)-1}`` (a
       total function on the current cells, no missing or stray index).
    2. **Ids from the base set** — every value is :data:`NEW`, :data:`NONE`, or a
       ``slide_id`` that exists in the base region (no invented ids).
    3. **Injective on base ids** — no base ``slide_id`` is assigned to two current
       cells (an id identifies one cell).
    4. **Unchanged-anchors pinned** (bidirectional) — a current cell
       byte-identical to an unambiguously-identified base cell must keep that
       cell's identity: its ``slide_id`` if the base cell had one, else
       :data:`NONE` (an unchanged id-less cell stays id-less, so the model cannot
       mint a spurious id onto unchanged content). Only genuinely *changed* cells
       are left for the model.
    5. **``NEW`` is nameable** — :data:`NEW` is only valid on a cell with a
       construct (there must be something to mint a slug from).
    """
    n = len(current_region)
    if set(mapping) != set(range(n)):
        raise AlignmentInvalid(
            f"map must cover current indices 0..{n - 1} exactly, got {sorted(mapping)}"
        )

    base_ids = {c.slide_id for c in base_region if c.slide_id is not None}
    for idx, value in mapping.items():
        if value in (NEW, NONE):
            if value == NEW and current_region[idx].construct is None:
                raise AlignmentInvalid(f"index {idx} assigned {NEW!r} but has no construct")
            continue
        if value not in base_ids:
            raise AlignmentInvalid(f"index {idx} assigned unknown base id {value!r}")

    assigned = [v for v in mapping.values() if v not in (NEW, NONE)]
    duplicated = sorted(x for x, count in Counter(assigned).items() if count > 1)
    if duplicated:
        raise AlignmentInvalid(f"base ids assigned to multiple current cells: {duplicated}")

    pinned = _pinned_assignments(base_region)
    for idx, cell in enumerate(current_region):
        forced = pinned.get(cell.content_hash)
        if forced is not None and mapping[idx] != forced:
            raise AlignmentInvalid(
                f"index {idx} is byte-identical to an unchanged base cell pinned to "
                f"{forced!r} but was mapped to {mapping[idx]!r}"
            )
    return mapping


# ---------------------------------------------------------------------------
# Recoverers
# ---------------------------------------------------------------------------


@dataclass
class StaticAlignmentRecoverer:
    """A deterministic recoverer for tests and offline runs.

    Returns ``mapping`` verbatim (a copy). With ``raise_error`` it raises
    :class:`RecoveryError` to exercise the safe-abort path. ``calls`` counts
    invocations so a test can assert the cache short-circuited a second run.
    """

    mapping: dict[int, str] = field(default_factory=dict)
    raise_error: bool = False
    prompt_version: str = "static"
    calls: int = 0

    def recover(
        self,
        *,
        base_region: list[RegionCell],
        current_region: list[RegionCell],
    ) -> dict[int, str]:
        self.calls += 1
        if self.raise_error:
            raise RecoveryError("static recoverer configured to fail")
        return dict(self.mapping)


_SYSTEM_PROMPT = (
    "You realign stable identifiers (slide_id) onto the code cells of a "
    "programming-course slide deck after an edit the deterministic tooling could "
    "not resolve (a function renamed while a cell was split, an ambiguous "
    "duplicate, a merge/split). You are given two ordered lists of CODE CELLS "
    "described WITHOUT their source — only an AST 'construct' name, a content "
    "hash, and any slide_id:\n"
    "  BASE: the last-synced cells, each with the slide_id it carried.\n"
    "  CURRENT: the cells as they are now; some lost their slide_id in the edit.\n\n"
    "Return ONLY a JSON object mapping each CURRENT cell index (a string) to one "
    "of:\n"
    "  - a slide_id taken from BASE, when that current cell is the continuation "
    "of that base cell (e.g. a renamed function keeps its id);\n"
    '  - "new", when the current cell is genuinely new content that should get a '
    "fresh id (it must have a construct);\n"
    '  - "none", when the current cell should remain without an id.\n\n'
    "Rules you MUST follow:\n"
    "  - Map EVERY current index exactly once; assign each BASE slide_id to AT "
    "MOST one current cell.\n"
    "  - A current cell whose content hash EQUALS a base cell's is unchanged: "
    'give it that base cell\'s slide_id (or "none" if the base cell had none).\n'
    "  - Use construct-name and position similarity to spot a rename.\n"
    '  - When unsure, return "none"/"new" rather than guess an id — a wrong id is '
    "worse than none.\n"
    "Return only the JSON object, no commentary, no code fences."
)


def _serialize_region(label: str, region: list[RegionCell]) -> str:
    """Render one region as a compact, indexed, body-free list for the prompt."""
    rows = [
        {
            "index": idx,
            "slide_id": cell.slide_id,
            "construct": cell.construct,
            "content_hash": cell.content_hash,
        }
        for idx, cell in enumerate(region)
    ]
    return f"{label}:\n" + json.dumps(rows, ensure_ascii=False, indent=2)


def build_recovery_user_prompt(
    base_region: list[RegionCell],
    current_region: list[RegionCell],
) -> str:
    """Build the body-free user prompt: the two serialized regions."""
    return (
        _serialize_region("BASE", base_region)
        + "\n\n"
        + _serialize_region("CURRENT", current_region)
    )


@dataclass
class OpenRouterAlignmentRecoverer:
    """LLM-backed recoverer (synchronous OpenAI client, Claude Opus by default).

    Sends the body-free region serialization and parses the returned JSON map.
    Raises :class:`RecoveryError` on any transport/parse failure so the caller
    safe-aborts that region rather than crashing the whole sync. The result is
    *not* trusted — :func:`validate_alignment` gates it before it is applied.
    """

    model: str = DEFAULT_RECOVERY_MODEL
    api_base: str | None = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout: float = 120.0
    prompt_version: str = RECOVERY_PROMPT_VERSION

    def _client(self):  # pragma: no cover - thin network adapter
        from clm.infrastructure.llm.openrouter_client import build_openrouter_client

        return build_openrouter_client(
            api_base=self.api_base, api_key=self.api_key, timeout=self.timeout
        )

    def recover(
        self,
        *,
        base_region: list[RegionCell],
        current_region: list[RegionCell],
    ) -> dict[int, str]:  # pragma: no cover - exercised via mocked client / integration
        user = build_recovery_user_prompt(base_region, current_region)

        def _create():
            return self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )

        try:
            response = call_with_retries(
                _create, exc=Exception, label=f"alignment recovery ({self.model})"
            )
        except Exception as exc:  # noqa: BLE001 - normalize to the protocol's error
            raise RecoveryError(f"alignment recovery call failed: {exc}") from exc
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RecoveryError("alignment recovery returned empty content")
        return decode_mapping(_strip_fences(content))


def _strip_fences(text: str) -> str:
    """Drop a leading/trailing ``` fence if the model wrapped its JSON in one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped
