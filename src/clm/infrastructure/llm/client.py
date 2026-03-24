"""Thin async wrapper around the OpenAI SDK for LLM summarization calls."""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Module-level semaphore, initialized on first use
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore(max_concurrent: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None or _semaphore._value != max_concurrent:
        _semaphore = asyncio.Semaphore(max_concurrent)
    return _semaphore


def _build_client(
    api_base: str | None = None,
    api_key: str | None = None,
):
    """Build an AsyncOpenAI client with the given configuration."""
    import openai

    kwargs: dict = {}
    if api_base:
        kwargs["base_url"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    return openai.AsyncOpenAI(**kwargs)


def _format_llm_error(exc: Exception, notebook_title: str) -> str:
    """Format an LLM error into a concise, user-friendly message."""
    import openai

    exc_type = type(exc).__name__
    raw = str(exc)

    if isinstance(exc, openai.AuthenticationError):
        return (
            f"Authentication failed for '{notebook_title}': "
            "check your API key (OPENAI_API_KEY or CLM_LLM__API_KEY)"
        )
    if isinstance(exc, openai.RateLimitError):
        return f"Rate limited on '{notebook_title}': reduce --max-concurrent or wait and retry"
    if isinstance(exc, openai.BadRequestError):
        lower = raw.lower()
        if "context" in lower or "token" in lower or "too long" in lower:
            return (
                f"Content too long for '{notebook_title}': "
                "the notebook exceeds the model's context window"
            )
        return f"Bad request for '{notebook_title}': {_extract_message(raw)}"
    if isinstance(exc, openai.NotFoundError):
        return (
            f"Model not found for '{notebook_title}': "
            "verify the --model value is a valid model identifier "
            "(e.g. anthropic/claude-sonnet-4-6, openai/gpt-4o)"
        )
    if isinstance(exc, openai.APIConnectionError):
        return (
            f"Cannot connect to API for '{notebook_title}': check your network and --api-base URL"
        )
    if isinstance(exc, openai.InternalServerError):
        return f"API server error for '{notebook_title}': try again later"

    # Fallback
    return f"LLM error for '{notebook_title}' ({exc_type}): {_extract_message(raw)}"


def _extract_message(raw: str) -> str:
    """Extract the essential message from a verbose error string."""
    idx = raw.find("message")
    if idx != -1:
        import json as _json

        brace_start = raw.rfind("{", 0, idx)
        brace_end = raw.find("}", idx)
        if brace_start != -1 and brace_end != -1:
            try:
                obj = _json.loads(raw[brace_start : brace_end + 1])
                if "message" in obj:
                    return str(obj["message"])
            except _json.JSONDecodeError:
                pass

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
        model: Model identifier (e.g. anthropic/claude-sonnet-4-6)
        notebook_title: Title of the notebook
        section_name: Name of the section
        course_name: Name of the course
        temperature: Sampling temperature
        api_base: API base URL (e.g. https://openrouter.ai/api/v1)
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

    client = _build_client(api_base=api_base, api_key=api_key)

    sem = _get_semaphore(max_concurrent)
    async with sem:
        logger.debug(f"Calling LLM for '{notebook_title}' ({audience})")
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
            raise LLMError(_format_llm_error(exc, notebook_title)) from exc
        result_content = str(response.choices[0].message.content)
        return result_content.strip()


class LLMError(Exception):
    """User-friendly LLM call error."""
