"""Thin async wrapper around litellm for LLM summarization calls."""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Module-level semaphore, initialized on first use
_semaphore: asyncio.Semaphore | None = None
_litellm_configured = False


def _get_semaphore(max_concurrent: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None or _semaphore._value != max_concurrent:
        _semaphore = asyncio.Semaphore(max_concurrent)
    return _semaphore


def _configure_litellm():
    """Configure litellm once to suppress noisy stdout output."""
    global _litellm_configured
    if _litellm_configured:
        return
    import litellm

    litellm.suppress_debug_info = True
    _litellm_configured = True


def _format_llm_error(exc: Exception, notebook_title: str) -> str:
    """Format an LLM error into a concise, user-friendly message."""
    import litellm

    exc_type = type(exc).__name__
    raw = str(exc)

    if isinstance(exc, litellm.AuthenticationError):
        return (
            f"Authentication failed for '{notebook_title}': "
            "check your API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "OPENROUTER_API_KEY, or CLM_LLM__API_KEY)"
        )
    if isinstance(exc, litellm.RateLimitError):
        return f"Rate limited on '{notebook_title}': reduce --max-concurrent or wait and retry"
    if isinstance(exc, litellm.ContextWindowExceededError):
        return (
            f"Content too long for '{notebook_title}': "
            "the notebook exceeds the model's context window"
        )
    if isinstance(exc, litellm.NotFoundError):
        # Model or endpoint not found — most likely a bad model name
        return (
            f"Model not found for '{notebook_title}': "
            "verify the --model value is a valid litellm model identifier "
            "(e.g. anthropic/claude-sonnet-4-6, openrouter/z-ai/glm-5)"
        )
    if isinstance(exc, litellm.BadRequestError):
        return f"Bad request for '{notebook_title}': {_extract_message(raw)}"
    if isinstance(exc, litellm.APIConnectionError):
        return (
            f"Cannot connect to API for '{notebook_title}': check your network and --api-base URL"
        )
    if isinstance(exc, (litellm.InternalServerError, litellm.ServiceUnavailableError)):
        return f"API server error for '{notebook_title}': try again later"

    # Fallback: strip the verbose litellm prefix
    return f"LLM error for '{notebook_title}' ({exc_type}): {_extract_message(raw)}"


def _extract_message(raw: str) -> str:
    """Extract the essential message from a verbose litellm error string."""
    # litellm errors often look like:
    #   "litellm.SomeError: ProviderException - {json...}"
    # Try to pull out just the human-readable part.
    for _prefix in ("litellm.", "Provider"):
        idx = raw.find("message")
        if idx != -1:
            # Try to extract the "message" value from JSON-like content
            import json as _json

            # Find the JSON object containing "message"
            brace_start = raw.rfind("{", 0, idx)
            brace_end = raw.find("}", idx)
            if brace_start != -1 and brace_end != -1:
                try:
                    obj = _json.loads(raw[brace_start : brace_end + 1])
                    if "message" in obj:
                        return str(obj["message"])
                except _json.JSONDecodeError:
                    pass
            break

    # Strip common litellm prefixes
    if " - " in raw:
        _, _, after = raw.partition(" - ")
        return after.strip()
    return raw


async def summarize_notebook(
    content: str,
    audience: str,
    model: str,
    notebook_title: str,
    section_name: str,
    course_name: str,
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    max_concurrent: int = 5,
    has_workshop: bool = False,
    language: str = "en",
    style: str = "prose",
) -> str:
    """Call LLM to generate a summary for one notebook.

    Args:
        content: Extracted notebook content
        audience: "client" or "trainer"
        model: litellm model identifier
        notebook_title: Title of the notebook
        section_name: Name of the section
        course_name: Name of the course
        temperature: Sampling temperature
        api_base: Custom API base URL (for OpenRouter, etc.)
        api_key: API key override
        max_concurrent: Max parallel LLM calls
        has_workshop: Whether the notebook contains a workshop
        language: Output language ("en" or "de")
        style: Output style ("prose" or "bullets")

    Returns:
        Generated summary text

    Raises:
        LLMError: On any LLM call failure, with a user-friendly message
    """
    import litellm

    _configure_litellm()

    from clm.infrastructure.llm.prompts import get_prompts

    system_prompt, user_message = get_prompts(
        audience=audience,
        course_name=course_name,
        section_name=section_name,
        notebook_title=notebook_title,
        content=content,
        has_workshop=has_workshop,
        language=language,
        style=style,
    )

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key

    sem = _get_semaphore(max_concurrent)
    async with sem:
        logger.debug(f"Calling LLM for '{notebook_title}' ({audience})")
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            raise LLMError(_format_llm_error(exc, notebook_title)) from exc
        result_content = str(response.choices[0].message.content)
        return result_content.strip()


class LLMError(Exception):
    """User-friendly LLM call error."""
