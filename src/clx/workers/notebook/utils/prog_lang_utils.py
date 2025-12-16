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


def jupytext_format_for(prog_lang: str) -> str | dict[str, str]:
    try:
        return cast(str | dict[str, str], config.prog_lang[prog_lang]["jupytext_format"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def language_info(prog_lang: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], config.prog_lang[prog_lang]["language_info"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def file_extension_for(prog_lang: str) -> str:
    try:
        return cast(str, language_info(prog_lang)["file_extension"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e


def kernelspec_for(prog_lang: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], config.prog_lang[prog_lang]["kernelspec"])
    except KeyError as e:
        raise ValueError(f"Unsupported language: {prog_lang}") from e
