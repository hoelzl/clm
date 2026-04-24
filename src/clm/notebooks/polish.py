"""LLM-powered text cleanup for speaker notes.

This module cleans up raw transcript text or rough speaker notes using an LLM.
It is independent of the voiceover pipeline and can be used standalone via
``clm polish`` or as part of the voiceover workflow.

Requires the ``[summarize]`` extra (openai).
"""

from __future__ import annotations

import logging

from clm.notebooks.polish_levels import PolishLevel, load_prompt

logger = logging.getLogger(__name__)

# Kept for backwards compatibility — external code that imported SYSTEM_PROMPT
# directly still works.  The canonical source is now
# ``clm/notebooks/polish_levels/standard.md``.
SYSTEM_PROMPT = load_prompt(PolishLevel.standard)


def _build_user_prompt(notes_text: str, slide_content: str) -> str:
    """Build the user prompt with slide context and notes to polish."""
    parts = []
    if slide_content.strip():
        parts.append(
            "Here is the slide content for context (do not include this in your output):\n\n"
            f"```\n{slide_content.strip()}\n```\n"
        )
    parts.append(f"Please clean up the following speaker notes:\n\n```\n{notes_text.strip()}\n```")
    return "\n".join(parts)


async def polish_text(
    notes_text: str,
    slide_content: str = "",
    *,
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    polish_level: PolishLevel = PolishLevel.standard,
) -> str:
    """Polish speaker notes text using an LLM.

    Args:
        notes_text: Raw notes text to clean up.
        slide_content: Slide content for context (helps the LLM understand
            what's being discussed).
        model: Model identifier (e.g. gpt-4o-mini, or an OpenRouter
            model like anthropic/claude-sonnet-4-6 when using api_base).
        temperature: Sampling temperature.
        api_base: API base URL (e.g. https://openrouter.ai/api/v1).
        api_key: API key override.
        polish_level: How aggressively to edit the notes.  Defaults to
            ``PolishLevel.standard``.  Pass ``PolishLevel.verbatim`` to
            return the input unchanged without making any LLM call.

    Returns:
        Polished notes text (or the unchanged input when
        ``polish_level == PolishLevel.verbatim``).

    Raises:
        LLMError: On LLM call failure.
    """
    if polish_level == PolishLevel.verbatim:
        return notes_text

    from clm.infrastructure.llm.client import LLMError, _build_client

    system_prompt = load_prompt(polish_level)
    client = _build_client(api_base=api_base, api_key=api_key)

    user_message = _build_user_prompt(notes_text, slide_content)

    logger.debug(
        "Polishing notes (%d chars) with model %s at level %s",
        len(notes_text),
        model,
        polish_level,
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
        )
    except Exception as exc:
        raise LLMError(f"Polish LLM call failed: {exc}") from exc

    result = str(response.choices[0].message.content).strip()

    # Strip any markdown code fences the LLM might wrap the output in
    if result.startswith("```") and result.endswith("```"):
        lines = result.split("\n")
        result = "\n".join(lines[1:-1]).strip()

    return result
