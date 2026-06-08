"""Language-aware sync_translate prompts (Problem A Y-5).

The translator prompts must name the deck's actual comment token and programming
language, so a //-deck is told to preserve "// " prefixes and a C# code cell is
translated as C#, not "runnable Python".
"""

from __future__ import annotations

import pytest

from clm.slides.sync_translate import (
    _CODE_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    _TITLE_SYSTEM_PROMPT,
    OpenRouterSlideTranslator,
    _prog_lang_descriptors,
    _system_prompt_for,
)


@pytest.mark.parametrize(
    "prog_lang,prefix,name",
    [
        ("python", "# ", "Python"),
        ("rust", "# ", "Rust"),
        ("csharp", "// ", "C#"),
        ("cpp", "// ", "C++"),
        ("java", "// ", "Java"),
        ("unknownlang", "# ", "Unknownlang"),  # graceful fallback (token "#", name from the lang)
    ],
)
def test_prog_lang_descriptors(prog_lang: str, prefix: str, name: str) -> None:
    assert _prog_lang_descriptors(prog_lang) == (prefix, name)


def test_typescript_uses_slashes() -> None:
    prefix, _ = _prog_lang_descriptors("typescript")
    assert prefix == "// "


def test_code_prompt_names_the_language() -> None:
    prefix, name = _prog_lang_descriptors("csharp")
    prompt = _CODE_SYSTEM_PROMPT.format(
        source_lang="German",
        target_lang="English",
        role="code",
        comment_prefix=prefix,
        prog_lang_name=name,
    )
    assert "C# code cell" in prompt
    assert "runnable C#" in prompt
    assert "Python" not in prompt  # no Python hardcoding leaks into a C# prompt


def test_markdown_prompt_uses_comment_prefix() -> None:
    prefix, name = _prog_lang_descriptors("cpp")
    prompt = _SYSTEM_PROMPT.format(
        source_lang="German",
        target_lang="English",
        role="voiceover",
        comment_prefix=prefix,
        prog_lang_name=name,
    )
    assert "'// '" in prompt  # the model is told the // line prefix
    assert "'# '" not in prompt  # no stray Python prefix instruction


def test_python_prompt_unchanged() -> None:
    prefix, name = _prog_lang_descriptors("python")
    prompt = _SYSTEM_PROMPT.format(
        source_lang="German",
        target_lang="English",
        role="voiceover",
        comment_prefix=prefix,
        prog_lang_name=name,
    )
    assert "'# '" in prompt  # regression: Python decks still told the # prefix


# --- title role (the header_<lang> macro argument) -------------------------


def test_title_role_selects_title_prompt() -> None:
    assert _system_prompt_for("title") is _TITLE_SYSTEM_PROMPT
    assert _system_prompt_for("code") is _CODE_SYSTEM_PROMPT
    assert _system_prompt_for("slide") is _SYSTEM_PROMPT


def test_title_prompt_forbids_prefix_and_quotes() -> None:
    prompt = _TITLE_SYSTEM_PROMPT.format(
        source_lang="English",
        target_lang="German",
        role="title",
        comment_prefix="# ",
        prog_lang_name="Python",
    )
    assert "no Markdown" in prompt
    assert "no surrounding quotes" in prompt
    assert "comment prefix" in prompt  # the leading-'# ' leak guard


# --- guidance (glossary) folds into the cache key --------------------------


def test_guidance_folds_into_prompt_version() -> None:
    base = OpenRouterSlideTranslator()
    assert base.prompt_version == "translate-v1"  # no glossary → v1 key, no flag-day

    g = OpenRouterSlideTranslator(guidance="Address the reader with 'Sie'.")
    assert g.prompt_version.startswith("translate-v1:g")
    assert g.prompt_version != base.prompt_version

    # Same guidance → same version (a stable, reusable cache key).
    assert OpenRouterSlideTranslator(guidance="Address the reader with 'Sie'.").prompt_version == (
        g.prompt_version
    )
    # Different guidance → different version (editing the glossary invalidates).
    assert OpenRouterSlideTranslator(guidance="Use 'du'.").prompt_version != g.prompt_version
    # Whitespace-only guidance is treated as none.
    assert OpenRouterSlideTranslator(guidance="   \n").prompt_version == "translate-v1"
