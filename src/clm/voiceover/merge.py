"""Merge voiceover content using a single-pass LLM call.

This module implements the ``polish_and_merge`` function and the batching
logic that packs multiple slides into a single LLM call for efficiency.
When the baseline is empty, it degrades to the current polish behavior
(insert fresh voiceover from transcript only).

The merge prompt is a single LLM call per slide (or batch of slides) that
handles noise filtering, content preservation, addition integration, and
baseline rewrites holistically. No multi-pass pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default character budget for batching multiple slides into one LLM call.
DEFAULT_BATCH_CHAR_LIMIT = 20_000

# Default model for merge operations (Claude Sonnet 4.6 via OpenRouter).
DEFAULT_MERGE_MODEL = "anthropic/claude-sonnet-4-6"


@dataclass
class MergeResult:
    """Result of merging baseline + transcript for one slide."""

    slide_id: str
    merged_bullets: str
    rewrites: list[dict] = field(default_factory=list)
    dropped_from_transcript: list[str] = field(default_factory=list)


@dataclass
class SlideInput:
    """Input data for one slide's merge operation."""

    slide_id: str
    baseline: str
    transcript: str
    slide_content: str
    boundary_hint: bool = False


@dataclass
class PropagationInput:
    """Input data for one slide's cross-language propagation.

    Carries the source-language baseline/merged pair along with the
    target-language baseline, so the propagate prompt can produce a
    target-language voiceover that mirrors the source-side changes.
    """

    slide_id: str
    source_baseline: str
    source_merged: str
    source_rewrites: list[dict]
    target_baseline: str
    slide_content: str
    source_lang: str
    target_lang: str


@dataclass
class PropagationResult:
    """Result of propagating one slide's changes to the target language."""

    slide_id: str
    translated_bullets: str
    corresponded_changes: list[dict] = field(default_factory=list)
    target_preserved_unchanged: bool = True


def _load_system_prompt(language: str) -> str:
    """Load the language-specific merge system prompt."""
    filename = f"merge_{language}.md"
    prompt_dir = Path(__file__).parent / "prompts"
    prompt_path = prompt_dir / filename
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Fallback to English
    fallback = prompt_dir / "merge_en.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No merge prompt found for language '{language}'")


def _load_propagate_prompt(source_lang: str, target_lang: str) -> str:
    """Load the system prompt for propagating from source_lang to target_lang."""
    filename = f"propagate_{source_lang}_to_{target_lang}.md"
    prompt_dir = Path(__file__).parent / "prompts"
    prompt_path = prompt_dir / filename
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"No propagate prompt found for {source_lang} -> {target_lang} (expected {prompt_path})"
    )


def _build_user_prompt(slide: SlideInput) -> str:
    """Build the user prompt for a single slide."""
    parts = []

    if slide.slide_content.strip():
        parts.append(f"SLIDE CONTEXT (do not include in output):\n{slide.slide_content.strip()}")

    baseline_label = "BASELINE VOICEOVER (preserve; may rewrite only on factual contradiction):"
    if slide.baseline.strip():
        parts.append(f"{baseline_label}\n{slide.baseline.strip()}")
    else:
        parts.append(f"{baseline_label}\n(empty -- no existing voiceover)")

    parts.append(
        f"TRANSCRIPT (candidate additions, filter aggressively):\n{slide.transcript.strip()}"
    )

    if slide.boundary_hint:
        parts.append(
            "NOTE: This slide spans a recording part boundary. "
            "Be extra suspicious of greeting/sign-off noise near the "
            "start or end of the transcript."
        )

    return "\n\n".join(parts)


def _build_batch_user_prompt(slides: list[SlideInput]) -> str:
    """Build a user prompt containing multiple slides for batch processing."""
    parts = []
    parts.append(
        f"Process the following {len(slides)} slides. "
        "Return a JSON array with one result object per slide, "
        "keyed by slide_id. Each object follows the same schema as "
        "a single-slide response."
    )

    for slide in slides:
        parts.append(f"--- SLIDE: {slide.slide_id} ---")
        parts.append(_build_user_prompt(slide))

    return "\n\n".join(parts)


def _parse_single_result(raw: str, slide_id: str) -> MergeResult:
    """Parse a single-slide JSON response from the LLM."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    data = json.loads(text)

    return MergeResult(
        slide_id=slide_id,
        merged_bullets=data.get("merged_bullets", ""),
        rewrites=data.get("rewrites", []),
        dropped_from_transcript=data.get("dropped_from_transcript", []),
    )


def _parse_batch_result(raw: str, slide_ids: list[str]) -> dict[str, MergeResult]:
    """Parse a batch JSON response from the LLM.

    Returns a dict of slide_id -> MergeResult.

    Raises:
        json.JSONDecodeError: If the response is not valid JSON.
        ValueError: If the response structure is unexpected.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    data = json.loads(text)

    # Accept both a JSON array and a JSON object keyed by slide_id
    results: dict[str, MergeResult] = {}

    if isinstance(data, list):
        for item in data:
            sid = item.get("slide_id", "")
            results[sid] = MergeResult(
                slide_id=sid,
                merged_bullets=item.get("merged_bullets", ""),
                rewrites=item.get("rewrites", []),
                dropped_from_transcript=item.get("dropped_from_transcript", []),
            )
    elif isinstance(data, dict):
        # Could be a single result or keyed by slide_id
        if "merged_bullets" in data:
            # Single result in a dict
            sid = data.get("slide_id", slide_ids[0] if slide_ids else "")
            results[sid] = MergeResult(
                slide_id=sid,
                merged_bullets=data.get("merged_bullets", ""),
                rewrites=data.get("rewrites", []),
                dropped_from_transcript=data.get("dropped_from_transcript", []),
            )
        else:
            # Keyed by slide_id
            for sid, item in data.items():
                if isinstance(item, dict):
                    results[sid] = MergeResult(
                        slide_id=sid,
                        merged_bullets=item.get("merged_bullets", ""),
                        rewrites=item.get("rewrites", []),
                        dropped_from_transcript=item.get("dropped_from_transcript", []),
                    )
    else:
        raise ValueError(f"Unexpected JSON structure: {type(data)}")

    return results


def _estimate_chars(slide: SlideInput) -> int:
    """Estimate character count for a slide input."""
    return len(slide.baseline) + len(slide.transcript) + len(slide.slide_content)


def build_batches(
    slides: list[SlideInput],
    char_limit: int = DEFAULT_BATCH_CHAR_LIMIT,
) -> list[list[SlideInput]]:
    """Pack slides into batches respecting the character budget.

    Each batch stays under ``char_limit`` total input characters.
    A single slide that exceeds the limit gets its own batch.
    """
    batches: list[list[SlideInput]] = []
    current_batch: list[SlideInput] = []
    current_chars = 0

    for slide in slides:
        slide_chars = _estimate_chars(slide)

        if current_batch and current_chars + slide_chars > char_limit:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(slide)
        current_chars += slide_chars

    if current_batch:
        batches.append(current_batch)

    return batches


async def polish_and_merge(
    baseline_bullets: str,
    transcript_text: str,
    slide_content: str = "",
    language: str = "de",
    boundary_hint: bool = False,
    *,
    model: str = DEFAULT_MERGE_MODEL,
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    slide_id: str = "",
    langfuse_context: dict | None = None,
) -> MergeResult:
    """Merge baseline voiceover with transcript using a single LLM call.

    When ``baseline_bullets`` is empty, this degrades to the current
    polish behavior (clean transcript into bullets). When non-empty, it
    merges the transcript additions into the baseline while preserving
    existing content and filtering noise.

    Args:
        baseline_bullets: Existing voiceover cell content ("" if none).
        transcript_text: Raw aligned transcript for this slide.
        slide_content: Slide code/markdown for context.
        language: Language code ("de" or "en").
        boundary_hint: Whether this slide spans a recording part boundary.
        model: LLM model identifier.
        temperature: Sampling temperature.
        api_base: API base URL (e.g. OpenRouter).
        api_key: API key override.
        slide_id: Identifier for the slide (used in results).
        langfuse_context: Optional dict of Langfuse-specific kwargs
            (``name``, ``trace_id``, ``metadata``) forwarded to the
            ``create()`` call.  Ignored when the client is not
            Langfuse-wrapped.

    Returns:
        MergeResult with merged bullets and structured metadata.

    Raises:
        LLMError: On LLM call failure.
    """
    from clm.infrastructure.llm.client import LLMError, _build_client

    slide_input = SlideInput(
        slide_id=slide_id,
        baseline=baseline_bullets,
        transcript=transcript_text,
        slide_content=slide_content,
        boundary_hint=boundary_hint,
    )

    system_prompt = _load_system_prompt(language)
    user_message = _build_user_prompt(slide_input)

    client = _build_client(api_base=api_base, api_key=api_key)

    logger.debug(
        "polish_and_merge slide=%s baseline=%d chars, transcript=%d chars",
        slide_id,
        len(baseline_bullets),
        len(transcript_text),
    )

    create_kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if langfuse_context:
        create_kwargs.update(langfuse_context)

    try:
        response = await client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        raise LLMError(f"Merge LLM call failed for slide {slide_id}: {exc}") from exc

    raw = str(response.choices[0].message.content).strip()

    try:
        return _parse_single_result(raw, slide_id)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning(
            "Failed to parse JSON from LLM for slide %s, using raw text as merged_bullets: %s",
            slide_id,
            exc,
        )
        return MergeResult(
            slide_id=slide_id,
            merged_bullets=raw,
        )


async def merge_batch(
    slides: list[SlideInput],
    *,
    language: str = "de",
    model: str = DEFAULT_MERGE_MODEL,
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    langfuse_context: dict | None = None,
) -> list[MergeResult]:
    """Merge a batch of slides in a single LLM call.

    On JSON parse failure, falls back to per-slide calls for the batch.

    Args:
        slides: List of slide inputs to merge.
        language: Language code ("de" or "en").
        model: LLM model identifier.
        temperature: Sampling temperature.
        api_base: API base URL.
        api_key: API key override.
        langfuse_context: Optional Langfuse kwargs forwarded to ``create()``.

    Returns:
        List of MergeResult, one per input slide, in order.
    """
    from clm.infrastructure.llm.client import LLMError, _build_client

    if len(slides) == 1:
        result = await polish_and_merge(
            slides[0].baseline,
            slides[0].transcript,
            slides[0].slide_content,
            language,
            slides[0].boundary_hint,
            model=model,
            temperature=temperature,
            api_base=api_base,
            api_key=api_key,
            slide_id=slides[0].slide_id,
            langfuse_context=langfuse_context,
        )
        return [result]

    system_prompt = _load_system_prompt(language)
    user_message = _build_batch_user_prompt(slides)
    slide_ids = [s.slide_id for s in slides]

    client = _build_client(api_base=api_base, api_key=api_key)

    logger.debug("Batch merge: %d slides", len(slides))

    create_kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if langfuse_context:
        create_kwargs.update(langfuse_context)

    try:
        response = await client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        raise LLMError(f"Batch merge LLM call failed: {exc}") from exc

    raw = str(response.choices[0].message.content).strip()

    try:
        parsed = _parse_batch_result(raw, slide_ids)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Batch JSON parse failed (%s), falling back to per-slide calls",
            exc,
        )
        results = []
        for slide in slides:
            result = await polish_and_merge(
                slide.baseline,
                slide.transcript,
                slide.slide_content,
                language,
                slide.boundary_hint,
                model=model,
                temperature=temperature,
                api_base=api_base,
                api_key=api_key,
                slide_id=slide.slide_id,
                langfuse_context=langfuse_context,
            )
            results.append(result)
        return results

    # Return results in input order
    results = []
    for slide in slides:
        if slide.slide_id in parsed:
            results.append(parsed[slide.slide_id])
        else:
            logger.warning(
                "Slide %s missing from batch response, falling back to per-slide call",
                slide.slide_id,
            )
            result = await polish_and_merge(
                slide.baseline,
                slide.transcript,
                slide.slide_content,
                language,
                slide.boundary_hint,
                model=model,
                temperature=temperature,
                api_base=api_base,
                api_key=api_key,
                slide_id=slide.slide_id,
                langfuse_context=langfuse_context,
            )
            results.append(result)

    return results


# ---------------------------------------------------------------------------
# Cross-language propagation (Item 2)
# ---------------------------------------------------------------------------


def _format_source_diff(slide: PropagationInput) -> str:
    """Render the baseline -> merged diff as structured text for the LLM.

    Uses the rewrites list from the merge result for explicit rewrites,
    and a line-diff between baseline and merged for added/dropped bullets.
    """
    baseline_bullets = [
        line for line in slide.source_baseline.splitlines() if line.strip().startswith("- ")
    ]
    merged_bullets = [
        line for line in slide.source_merged.splitlines() if line.strip().startswith("- ")
    ]

    baseline_set = set(baseline_bullets)
    merged_set = set(merged_bullets)

    rewritten_originals = {rw.get("original", "").strip() for rw in slide.source_rewrites}
    rewritten_revised = {rw.get("revised", "").strip() for rw in slide.source_rewrites}

    added = [
        b for b in merged_bullets if b not in baseline_set and b.strip() not in rewritten_revised
    ]
    dropped = [
        b for b in baseline_bullets if b not in merged_set and b.strip() not in rewritten_originals
    ]

    parts: list[str] = [
        f"SOURCE DIFF ({slide.source_lang} baseline -> {slide.source_lang} merged):"
    ]
    if not (added or dropped or slide.source_rewrites):
        parts.append("(no structured deltas; merged differs from baseline stylistically only)")
    for bullet in added:
        parts.append(f'- added: "{bullet.strip()}"')
    for rw in slide.source_rewrites:
        parts.append("- rewritten:")
        parts.append(f'    original: "{rw.get("original", "").strip()}"')
        parts.append(f'    revised:  "{rw.get("revised", "").strip()}"')
    for bullet in dropped:
        parts.append(f'- dropped: "{bullet.strip()}"')
    return "\n".join(parts)


def _build_propagate_user_prompt(slide: PropagationInput) -> str:
    """Build the user prompt for a single slide's propagation."""
    parts: list[str] = []

    if slide.slide_content.strip():
        parts.append(f"SLIDE CONTEXT (do not include in output):\n{slide.slide_content.strip()}")

    parts.append(
        f"SOURCE BASELINE ({slide.source_lang}):\n" + (slide.source_baseline.strip() or "(empty)")
    )
    parts.append(
        f"SOURCE MERGED ({slide.source_lang}):\n" + (slide.source_merged.strip() or "(empty)")
    )
    parts.append(_format_source_diff(slide))
    parts.append(
        f"TARGET BASELINE ({slide.target_lang}):\n"
        + (slide.target_baseline.strip() or "(empty -- no existing target voiceover)")
    )
    return "\n\n".join(parts)


def _build_propagate_batch_user_prompt(slides: list[PropagationInput]) -> str:
    """Build a user prompt containing multiple slides for batch propagation."""
    parts = [
        f"Process the following {len(slides)} slides. Return a JSON array "
        "with one result object per slide, keyed by slide_id. Each object "
        "follows the same schema as a single-slide propagation response."
    ]
    for slide in slides:
        parts.append(f"--- SLIDE: {slide.slide_id} ---")
        parts.append(_build_propagate_user_prompt(slide))
    return "\n\n".join(parts)


def _parse_single_propagation(raw: str, slide_id: str) -> PropagationResult:
    """Parse a single-slide propagation JSON response."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    data = json.loads(text)

    return PropagationResult(
        slide_id=slide_id,
        translated_bullets=data.get("translated_bullets", ""),
        corresponded_changes=data.get("corresponded_changes", []),
        target_preserved_unchanged=bool(data.get("target_preserved_unchanged", True)),
    )


def _parse_propagation_batch(raw: str, slide_ids: list[str]) -> dict[str, PropagationResult]:
    """Parse a batch propagation JSON response keyed by slide_id."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    data = json.loads(text)
    results: dict[str, PropagationResult] = {}

    if isinstance(data, list):
        for item in data:
            sid = item.get("slide_id", "")
            results[sid] = PropagationResult(
                slide_id=sid,
                translated_bullets=item.get("translated_bullets", ""),
                corresponded_changes=item.get("corresponded_changes", []),
                target_preserved_unchanged=bool(item.get("target_preserved_unchanged", True)),
            )
    elif isinstance(data, dict):
        if "translated_bullets" in data:
            sid = data.get("slide_id", slide_ids[0] if slide_ids else "")
            results[sid] = PropagationResult(
                slide_id=sid,
                translated_bullets=data.get("translated_bullets", ""),
                corresponded_changes=data.get("corresponded_changes", []),
                target_preserved_unchanged=bool(data.get("target_preserved_unchanged", True)),
            )
        else:
            for sid, item in data.items():
                if isinstance(item, dict):
                    results[sid] = PropagationResult(
                        slide_id=sid,
                        translated_bullets=item.get("translated_bullets", ""),
                        corresponded_changes=item.get("corresponded_changes", []),
                        target_preserved_unchanged=bool(
                            item.get("target_preserved_unchanged", True)
                        ),
                    )
    else:
        raise ValueError(f"Unexpected JSON structure: {type(data)}")

    return results


def _estimate_propagation_chars(slide: PropagationInput) -> int:
    """Estimate character count for a propagation input."""
    return (
        len(slide.source_baseline)
        + len(slide.source_merged)
        + len(slide.target_baseline)
        + len(slide.slide_content)
    )


def build_propagation_batches(
    slides: list[PropagationInput],
    char_limit: int = DEFAULT_BATCH_CHAR_LIMIT,
) -> list[list[PropagationInput]]:
    """Pack propagation inputs into batches respecting the character budget."""
    batches: list[list[PropagationInput]] = []
    current: list[PropagationInput] = []
    current_chars = 0

    for slide in slides:
        slide_chars = _estimate_propagation_chars(slide)
        if current and current_chars + slide_chars > char_limit:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(slide)
        current_chars += slide_chars

    if current:
        batches.append(current)
    return batches


async def propagate_one(
    slide: PropagationInput,
    *,
    model: str = DEFAULT_MERGE_MODEL,
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    langfuse_context: dict | None = None,
) -> PropagationResult:
    """Propagate one slide's merge deltas to the target language."""
    from clm.infrastructure.llm.client import LLMError, _build_client

    system_prompt = _load_propagate_prompt(slide.source_lang, slide.target_lang)
    user_message = _build_propagate_user_prompt(slide)

    client = _build_client(api_base=api_base, api_key=api_key)

    logger.debug(
        "propagate slide=%s %s->%s",
        slide.slide_id,
        slide.source_lang,
        slide.target_lang,
    )

    create_kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if langfuse_context:
        create_kwargs.update(langfuse_context)

    try:
        response = await client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        raise LLMError(f"Propagate LLM call failed for slide {slide.slide_id}: {exc}") from exc

    raw = str(response.choices[0].message.content).strip()

    try:
        return _parse_single_propagation(raw, slide.slide_id)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning(
            "Failed to parse JSON for propagate slide %s, using raw text: %s",
            slide.slide_id,
            exc,
        )
        return PropagationResult(
            slide_id=slide.slide_id,
            translated_bullets=raw,
            target_preserved_unchanged=False,
        )


async def propagate_batch(
    slides: list[PropagationInput],
    *,
    model: str = DEFAULT_MERGE_MODEL,
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    langfuse_context: dict | None = None,
) -> list[PropagationResult]:
    """Propagate a batch of slides' merge deltas in a single LLM call.

    On JSON parse failure, falls back to per-slide calls.

    Args:
        slides: Propagation inputs (all must share source_lang/target_lang).
        model: LLM model identifier.
        temperature: Sampling temperature.
        api_base: API base URL.
        api_key: API key override.
        langfuse_context: Optional Langfuse kwargs forwarded to ``create()``.

    Returns:
        List of PropagationResult in input order.
    """
    from clm.infrastructure.llm.client import LLMError, _build_client

    if not slides:
        return []

    if len(slides) == 1:
        return [
            await propagate_one(
                slides[0],
                model=model,
                temperature=temperature,
                api_base=api_base,
                api_key=api_key,
                langfuse_context=langfuse_context,
            )
        ]

    # All slides in a batch must share source/target languages.
    source_lang = slides[0].source_lang
    target_lang = slides[0].target_lang
    system_prompt = _load_propagate_prompt(source_lang, target_lang)
    user_message = _build_propagate_batch_user_prompt(slides)
    slide_ids = [s.slide_id for s in slides]

    client = _build_client(api_base=api_base, api_key=api_key)

    logger.debug("Batch propagate: %d slides %s->%s", len(slides), source_lang, target_lang)

    create_kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if langfuse_context:
        create_kwargs.update(langfuse_context)

    try:
        response = await client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        raise LLMError(f"Batch propagate LLM call failed: {exc}") from exc

    raw = str(response.choices[0].message.content).strip()

    try:
        parsed = _parse_propagation_batch(raw, slide_ids)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Propagate batch JSON parse failed (%s), falling back to per-slide calls",
            exc,
        )
        results: list[PropagationResult] = []
        for slide in slides:
            result = await propagate_one(
                slide,
                model=model,
                temperature=temperature,
                api_base=api_base,
                api_key=api_key,
                langfuse_context=langfuse_context,
            )
            results.append(result)
        return results

    # Return results in input order
    results = []
    for slide in slides:
        if slide.slide_id in parsed:
            results.append(parsed[slide.slide_id])
        else:
            logger.warning(
                "Slide %s missing from propagate batch response, falling back",
                slide.slide_id,
            )
            result = await propagate_one(
                slide,
                model=model,
                temperature=temperature,
                api_base=api_base,
                api_key=api_key,
                langfuse_context=langfuse_context,
            )
            results.append(result)

    return results
