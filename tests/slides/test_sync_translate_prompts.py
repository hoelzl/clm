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


# --- per-language guidance (the bidirectional sync add path) ----------------


def test_guidance_by_lang_selects_per_target() -> None:
    # sync is bidirectional: a DE target gets the DE conventions, an EN target the
    # EN conventions, from one translator instance.
    t = OpenRouterSlideTranslator(guidance_by_lang={"de": "Sie", "en": "formal English"})
    assert t._guidance_for("de") == "Sie"
    assert t._guidance_for("en") == "formal English"


def test_guidance_by_lang_absent_language_appends_nothing() -> None:
    # The common course shape: only a DE glossary. A DE->EN add (target en) has no
    # conventions; an EN->DE add (target de) uses the DE conventions.
    t = OpenRouterSlideTranslator(guidance_by_lang={"de": "Sie"})
    assert t._guidance_for("de") == "Sie"
    assert t._guidance_for("en") == ""


def test_guidance_by_lang_wins_over_single_guidance() -> None:
    # The two are alternatives; when guidance_by_lang has content it takes precedence
    # per target and the single string is ignored.
    t = OpenRouterSlideTranslator(guidance="single", guidance_by_lang={"de": "per-de"})
    assert t._guidance_for("de") == "per-de"
    assert t._guidance_for("en") == ""  # not "single"


def test_empty_guidance_by_lang_falls_back_to_single() -> None:
    # An all-empty map is treated as no map, so the single guidance still applies.
    t = OpenRouterSlideTranslator(guidance="single", guidance_by_lang={"de": "  \n"})
    assert t._guidance_for("de") == "single"
    assert t._guidance_for("en") == "single"


def test_guidance_by_lang_folds_into_prompt_version() -> None:
    base = OpenRouterSlideTranslator()
    g = OpenRouterSlideTranslator(guidance_by_lang={"de": "Sie", "en": "formal"})
    assert g.prompt_version.startswith("translate-v1:g")
    assert g.prompt_version != base.prompt_version
    # Stable per content, order-independent (sorted by language).
    assert (
        OpenRouterSlideTranslator(guidance_by_lang={"en": "formal", "de": "Sie"}).prompt_version
        == g.prompt_version
    )
    # Editing either side invalidates by cache miss.
    assert (
        OpenRouterSlideTranslator(guidance_by_lang={"de": "du", "en": "formal"}).prompt_version
        != g.prompt_version
    )
    # All-empty map → bare v1 key (no flag-day invalidation).
    assert OpenRouterSlideTranslator(guidance_by_lang={"de": "  "}).prompt_version == "translate-v1"


def test_single_guidance_prompt_version_unchanged_by_new_field() -> None:
    # Regression: adding guidance_by_lang must not change the single-guidance cache
    # key shape (an existing translate cache stays valid).
    import hashlib

    text = "Address the reader with 'Sie'."
    fp = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    assert OpenRouterSlideTranslator(guidance=text).prompt_version == f"translate-v1:g{fp}"


def test_single_string_and_map_signatures_do_not_collide() -> None:
    # Distinct cache-key namespaces (:g for single-string, :gm for the map) mean a
    # single-string guidance and a per-language map NEVER share a key — even this
    # adversarial input, where the one-entry map encodes to exactly the single string
    # ("de\x1eSie") and so produces the same sha, must still key different entries.
    single = OpenRouterSlideTranslator(guidance="de\x1eSie")
    mapped = OpenRouterSlideTranslator(guidance_by_lang={"de": "Sie"})
    assert single.prompt_version != mapped.prompt_version
    assert single.prompt_version.startswith("translate-v1:g")
    assert mapped.prompt_version.startswith("translate-v1:gm")


# --- the system-prompt assembly seam (base + selected guidance) -------------


def test_system_message_appends_selected_guidance() -> None:
    t = OpenRouterSlideTranslator(guidance_by_lang={"de": "GLOSSARY-DE", "en": "GLOSSARY-EN"})
    # EN->DE add: the DE conventions are appended; the EN ones are not.
    de_msg = t._system_message("slide", "en", "de")
    assert de_msg.endswith("\n\nGLOSSARY-DE")
    assert "GLOSSARY-EN" not in de_msg
    # DE->EN add: the EN conventions.
    en_msg = t._system_message("slide", "de", "en")
    assert en_msg.endswith("\n\nGLOSSARY-EN")


def test_system_message_no_guidance_is_bare_prompt() -> None:
    t = OpenRouterSlideTranslator()
    msg = t._system_message("slide", "de", "en")
    # With no glossary the message is EXACTLY the formatted base prompt — no appended
    # "\n\n<guidance>" block. The equality check fully proves that.
    assert msg == _SYSTEM_PROMPT.format(
        source_lang="German",
        target_lang="English",
        role="slide",
        comment_prefix="# ",
        prog_lang_name="Python",
    )


def test_system_message_brace_safe_guidance() -> None:
    # A glossary with JSON / f-string braces must be appended verbatim, never read
    # as a .format() field (it is concatenated AFTER formatting).
    t = OpenRouterSlideTranslator(guidance_by_lang={"de": 'keep {"role": "system"} literal'})
    msg = t._system_message("slide", "en", "de")
    assert msg.endswith('keep {"role": "system"} literal')
