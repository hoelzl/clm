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
    _prog_lang_descriptors,
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
