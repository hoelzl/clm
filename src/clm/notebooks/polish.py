"""LLM-powered text cleanup for speaker notes.

This module cleans up raw transcript text or rough speaker notes using an LLM.
It is independent of the voiceover pipeline and can be used standalone via
``clm polish`` or as part of the voiceover workflow.

Requires the ``[summarize]`` extra (litellm).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert editor for educational lecture notes. Your task is to clean up
speaker notes (voiceover text) for presentation slides.

Rules:
- Remove filler words, false starts, repetitions, and verbal tics.
- Fix grammar and punctuation.
- Do NOT remove any substantive content or technical details.
- Keep the style natural and spoken — these are speaker notes, not a textbook.
- Preserve technical terms, variable names, and code references exactly.
- Keep the same language as the input (do not translate).
- Output the cleaned text as a simple list of sentences/thoughts, one per line,
  each starting with "- ".
- If the input contains a "**[Revisited]**" marker, preserve it exactly.
- Do not add any preamble, explanation, or commentary — output only the cleaned notes.
"""


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
    model: str = "openrouter/anthropic/claude-sonnet-4-6",
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
) -> str:
    """Polish speaker notes text using an LLM.

    Args:
        notes_text: Raw notes text to clean up.
        slide_content: Slide content for context (helps the LLM understand
            what's being discussed).
        model: litellm model identifier.
        temperature: Sampling temperature.
        api_base: Custom API base URL.
        api_key: API key override.

    Returns:
        Polished notes text.

    Raises:
        LLMError: On LLM call failure.
    """
    import litellm

    from clm.infrastructure.llm.client import LLMError, _configure_litellm

    _configure_litellm()

    user_message = _build_user_prompt(notes_text, slide_content)

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key

    logger.debug("Polishing notes (%d chars) with model %s", len(notes_text), model)

    try:
        response = await litellm.acompletion(**kwargs)
    except Exception as exc:
        raise LLMError(f"Polish LLM call failed: {exc}") from exc

    result = str(response.choices[0].message.content).strip()

    # Strip any markdown code fences the LLM might wrap the output in
    if result.startswith("```") and result.endswith("```"):
        lines = result.split("\n")
        result = "\n".join(lines[1:-1]).strip()

    return result
