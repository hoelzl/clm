from pathlib import Path
from typing import Any, cast

ProgLangConfig = dict[str, Any]

_cpp_config: ProgLangConfig = {
    "file_extensions": ["cpp"],
    "jinja_prefix": "// j2",
    "jupytext_format": "cpp:percent",
    "language_info": {
        "codemirror_mode": "text/x-c++src",
        "file_extension": ".cpp",
        "mimetype": "text/x-c++src",
        "name": "C++",
        "version": "20",
    },
    "kernelspec": {"display_name": "C++20", "language": "cpp", "name": "xcpp20"},
    # Selectable language standards (issue #1228): the course spec's
    # ``<prog-lang standard="23">cpp</prog-lang>``. Each entry overlays the
    # ``kernelspec`` and ``language_info`` above; the default standard must
    # reproduce them exactly, so a course that names no standard — or names
    # the default — builds the same notebooks as before.
    "default_standard": "20",
    "standards": {
        "20": {
            "kernelspec": {"display_name": "C++20", "name": "xcpp20"},
            "language_info": {"version": "20"},
        },
        "23": {
            "kernelspec": {"display_name": "C++23", "name": "xcpp23"},
            "language_info": {"version": "23"},
        },
    },
}

_csharp_config: ProgLangConfig = {
    "file_extensions": ["cs"],
    "jinja_prefix": "// j2",
    "jupytext_format": {"format_name": "percent", "extension": ".cs"},
    "language_info": {
        "file_extension": ".cs",
        "mimetype": "text/x-csharp",
        "name": "C#",
        "pygments_lexer": "csharp",
        "version": "12.0",
    },
    "kernelspec": {
        "display_name": ".NET (C#)",
        "language": "C#",
        "name": ".net-csharp",
    },
}

_java_config: ProgLangConfig = {
    "file_extensions": ["java"],
    "jinja_prefix": "// j2",
    "jupytext_format": {"format_name": "percent", "extension": ".java"},
    "language_info": {
        "codemirror_mode": "java",
        "file_extension": ".java",
        "mimetype": "text/java",
        "name": "Java",
        "pygments_lexer": "java",
        "version": "",
    },
    "kernelspec": {"display_name": "Java", "language": "java", "name": "java"},
}

_python_config: ProgLangConfig = {
    "file_extensions": ["py"],
    "jinja_prefix": "# j2",
    "jupytext_format": "py:percent",
    "language_info": {
        "codemirror_mode": {"name": "ipython", "version": 3},
        "file_extension": ".py",
        "mimetype": "text/x-python",
        "name": "python",
        "nbconvert_exporter": "python",
        "pygments_lexer": "ipython3",
    },
    "kernelspec": {
        "display_name": "Python 3 (ipykernel)",
        "language": "python",
        "name": "python3",
    },
}

_rust_config: ProgLangConfig = {
    "file_extensions": ["rs"],
    "jinja_prefix": "# j2",
    "jupytext_format": "md",
    "language_info": {
        "codemirror_mode": "rust",
        "file_extension": ".rs",
        "mimetype": "text/rust",
        "name": "Rust",
        "pygment_lexer": "rust",
        "version": "",
    },
    "kernelspec": {"display_name": "Rust", "language": "rust", "name": "rust"},
}


_typescript_config: ProgLangConfig = {
    "file_extensions": ["ts"],
    "jinja_prefix": "// j2",
    "jupytext_format": {"format_name": "percent", "extension": ".ts"},
    "language_info": {
        "codemirror_mode": "typescript",
        "file_extension": ".ts",
        "mimetype": "text/x.typescript",
        "name": "typescript",
        "pygments_lexer": "typescript",
        "version": "5.6.2",
    },
    "kernelspec": {"display_name": "Deno", "language": "typescript", "name": "deno"},
}


class Config:
    def __init__(
        self,
        cpp: ProgLangConfig,
        java: ProgLangConfig,
        python: ProgLangConfig,
        rust: ProgLangConfig,
        csharp: ProgLangConfig,
        typescript: ProgLangConfig,
    ):
        self.prog_lang: dict[str, ProgLangConfig] = {
            "cpp": cpp,
            "java": java,
            "python": python,
            "rust": rust,
            "csharp": csharp,
            "typescript": typescript,
        }


config = Config(
    cpp=_cpp_config,
    java=_java_config,
    python=_python_config,
    rust=_rust_config,
    csharp=_csharp_config,
    typescript=_typescript_config,
)


def suffix_for(prog_lang: str) -> str:
    try:
        return "." + cast(str, config.prog_lang[prog_lang]["file_extensions"][0])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def jinja_prefix_for(prog_lang: str) -> str:
    try:
        return cast(str, config.prog_lang[prog_lang]["jinja_prefix"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def line_comment_for(prog_lang: str) -> str:
    """Return the line-comment token for a language.

    ``"#"`` for python/rust, ``"//"`` for cpp/csharp/java/typescript — the token
    that precedes ``%%`` / ``j2`` / ``{{`` in that language's percent-format slide
    files. Derived from the Jinja line-statement prefix (``"# j2"`` / ``"// j2"``)
    so the two can never drift.
    """
    return jinja_prefix_for(prog_lang).split(" ", 1)[0]


def jupytext_format_for(prog_lang: str) -> str | dict[str, str]:
    try:
        return cast(str | dict[str, str], config.prog_lang[prog_lang]["jupytext_format"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def supported_standards(prog_lang: str) -> tuple[str, ...]:
    """The language standards a course can select for *prog_lang*.

    Empty for a language without selectable standards (and for an unknown
    language). For C++: ``("20", "23")``.
    """
    lang_config = config.prog_lang.get(prog_lang, {})
    return tuple(lang_config.get("standards", {}))


def default_standard(prog_lang: str) -> str:
    """The standard *prog_lang* is built with when a course names none (``""`` if n/a)."""
    return cast(str, config.prog_lang.get(prog_lang, {}).get("default_standard", ""))


def normalize_standard(prog_lang: str, standard: str | None) -> str:
    """Canonical form of a requested language standard: ``""`` means "the default".

    Returns ``""`` for no standard and for the language's default standard,
    so that naming the default explicitly is indistinguishable from omitting
    it — in the notebook metadata, the CMake export and the cache keys.

    Raises :class:`ValueError` for a standard *prog_lang* does not offer,
    which includes any standard for a language that has none.
    """
    requested = (standard or "").strip()
    if not requested:
        return ""
    offered = supported_standards(prog_lang)
    if requested not in offered:
        if not offered:
            raise ValueError(
                f"Programming language {prog_lang!r} has no selectable standard "
                f"(got standard {requested!r})."
            )
        raise ValueError(
            f"Unsupported standard {requested!r} for programming language {prog_lang!r}: "
            f"expected one of {', '.join(offered)}."
        )
    return "" if requested == default_standard(prog_lang) else requested


def effective_standard(prog_lang: str, standard: str | None = "") -> str:
    """The standard actually in force: *standard*, or the language's default.

    ``""`` for a language without selectable standards.
    """
    return normalize_standard(prog_lang, standard) or default_standard(prog_lang)


def _standard_overlay(prog_lang: str, standard: str | None, key: str) -> dict[str, Any]:
    normalized = normalize_standard(prog_lang, standard)
    if not normalized:
        return {}
    return cast(dict[str, Any], config.prog_lang[prog_lang]["standards"][normalized][key])


def language_info(prog_lang: str, standard: str | None = "") -> dict[str, Any]:
    """Notebook ``language_info`` metadata for *prog_lang* at *standard*."""
    try:
        base = cast(dict[str, Any], config.prog_lang[prog_lang]["language_info"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e
    overlay = _standard_overlay(prog_lang, standard, "language_info")
    return {**base, **overlay} if overlay else base


def file_extension_for(prog_lang: str) -> str:
    try:
        return cast(str, language_info(prog_lang)["file_extension"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def kernelspec_for(prog_lang: str, standard: str | None = "") -> dict[str, Any]:
    """Notebook ``kernelspec`` metadata — the kernel a deck is executed in.

    *standard* selects among the language's standards (see
    :func:`supported_standards`); empty means the default.
    """
    try:
        base = cast(dict[str, Any], config.prog_lang[prog_lang]["kernelspec"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e
    overlay = _standard_overlay(prog_lang, standard, "kernelspec")
    return {**base, **overlay} if overlay else base


def all_kernelspec_names(prog_lang: str) -> tuple[str, ...]:
    """Every kernel name CLM can request for *prog_lang*, default first."""
    names = [cast(str, kernelspec_for(prog_lang)["name"])]
    for standard in supported_standards(prog_lang):
        name = cast(str, kernelspec_for(prog_lang, standard)["name"])
        if name not in names:
            names.append(name)
    return tuple(names)


def comment_token_for_path(path: Path) -> str:
    """Resolve a slide file's line-comment token from its extension.

    ``.py``/``.rs``/``.md`` → ``"#"``; ``.cs``/``.cpp``/``.java``/``.ts`` → ``"//"``.
    Falls back to ``"#"`` for unknown extensions.
    """
    from clm.core.utils.path_utils import path_to_prog_lang

    try:
        return line_comment_for(path_to_prog_lang(path))
    except (KeyError, ValueError):
        return "#"
