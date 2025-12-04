"""Tests for prog_lang_utils module.

This module tests all programming language configuration utilities including
suffix extraction, Jinja prefix mapping, Jupytext format mapping, kernel specs,
and language info retrieval.
"""

import pytest

from clx.workers.notebook.utils.prog_lang_utils import (
    Config,
    config,
    file_extension_for,
    jinja_prefix_for,
    jupytext_format_for,
    kernelspec_for,
    language_info,
    suffix_for,
)


class TestConfig:
    """Test the Config class and global config instance."""

    def test_config_has_all_languages(self):
        """Config should have all 6 supported languages."""
        expected_languages = {"python", "cpp", "csharp", "java", "typescript", "rust"}
        actual_languages = set(config.prog_lang.keys())
        assert actual_languages == expected_languages

    def test_config_instance_is_global(self):
        """The config instance should be a global singleton."""
        assert isinstance(config, Config)
        assert config.prog_lang is not None

    def test_each_language_has_required_keys(self):
        """Each language config should have all required keys."""
        required_keys = {
            "file_extensions",
            "jinja_prefix",
            "jupytext_format",
            "language_info",
            "kernelspec",
        }
        for lang, lang_config in config.prog_lang.items():
            actual_keys = set(lang_config.keys())
            missing_keys = required_keys - actual_keys
            assert not missing_keys, f"Language {lang} missing keys: {missing_keys}"


class TestSuffixFor:
    """Test the suffix_for function."""

    def test_suffix_for_python(self):
        """Python suffix should be .py."""
        assert suffix_for("python") == ".py"

    def test_suffix_for_cpp(self):
        """C++ suffix should be .cpp."""
        assert suffix_for("cpp") == ".cpp"

    def test_suffix_for_csharp(self):
        """C# suffix should be .cs."""
        assert suffix_for("csharp") == ".cs"

    def test_suffix_for_java(self):
        """Java suffix should be .java."""
        assert suffix_for("java") == ".java"

    def test_suffix_for_typescript(self):
        """TypeScript suffix should be .ts."""
        assert suffix_for("typescript") == ".ts"

    def test_suffix_for_rust(self):
        """Rust suffix should be .rs."""
        assert suffix_for("rust") == ".rs"

    def test_suffix_for_all_languages(self):
        """Test all language suffixes in one comprehensive test."""
        expected_suffixes = {
            "python": ".py",
            "cpp": ".cpp",
            "csharp": ".cs",
            "java": ".java",
            "typescript": ".ts",
            "rust": ".rs",
        }
        for lang, expected_suffix in expected_suffixes.items():
            actual_suffix = suffix_for(lang)
            assert (
                actual_suffix == expected_suffix
            ), f"Expected {expected_suffix} for {lang}, got {actual_suffix}"

    def test_suffix_for_unsupported_language_raises_valueerror(self):
        """Unsupported language should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: unknown"):
            suffix_for("unknown")

    def test_suffix_for_empty_string_raises_valueerror(self):
        """Empty string should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: "):
            suffix_for("")

    def test_suffix_for_case_sensitive(self):
        """Language names are case-sensitive."""
        with pytest.raises(ValueError, match="Unsupported language: Python"):
            suffix_for("Python")


class TestJinjaPrefixFor:
    """Test the jinja_prefix_for function."""

    def test_jinja_prefix_for_python(self):
        """Python Jinja prefix should be '# j2'."""
        assert jinja_prefix_for("python") == "# j2"

    def test_jinja_prefix_for_cpp(self):
        """C++ Jinja prefix should be '// j2'."""
        assert jinja_prefix_for("cpp") == "// j2"

    def test_jinja_prefix_for_csharp(self):
        """C# Jinja prefix should be '// j2'."""
        assert jinja_prefix_for("csharp") == "// j2"

    def test_jinja_prefix_for_java(self):
        """Java Jinja prefix should be '// j2'."""
        assert jinja_prefix_for("java") == "// j2"

    def test_jinja_prefix_for_typescript(self):
        """TypeScript Jinja prefix should be '// j2'."""
        assert jinja_prefix_for("typescript") == "// j2"

    def test_jinja_prefix_for_rust(self):
        """Rust Jinja prefix should be '# j2'."""
        assert jinja_prefix_for("rust") == "# j2"

    def test_jinja_prefix_groups(self):
        """Languages should be grouped by their comment style."""
        hash_comment_languages = ["python", "rust"]
        slash_comment_languages = ["cpp", "csharp", "java", "typescript"]

        for lang in hash_comment_languages:
            assert jinja_prefix_for(lang) == "# j2", f"{lang} should use hash comment"

        for lang in slash_comment_languages:
            assert jinja_prefix_for(lang) == "// j2", f"{lang} should use slash comment"

    def test_jinja_prefix_for_unsupported_language_raises_valueerror(self):
        """Unsupported language should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: unknown"):
            jinja_prefix_for("unknown")


class TestJupytextFormatFor:
    """Test the jupytext_format_for function."""

    def test_jupytext_format_for_python(self):
        """Python jupytext format should be 'py:percent'."""
        assert jupytext_format_for("python") == "py:percent"

    def test_jupytext_format_for_cpp(self):
        """C++ jupytext format should be 'cpp:percent'."""
        assert jupytext_format_for("cpp") == "cpp:percent"

    def test_jupytext_format_for_rust(self):
        """Rust jupytext format should be 'md'."""
        assert jupytext_format_for("rust") == "md"

    def test_jupytext_format_for_csharp_returns_dict(self):
        """C# jupytext format should return a dict."""
        result = jupytext_format_for("csharp")
        assert isinstance(result, dict)
        assert result == {"format_name": "percent", "extension": ".cs"}

    def test_jupytext_format_for_java_returns_dict(self):
        """Java jupytext format should return a dict."""
        result = jupytext_format_for("java")
        assert isinstance(result, dict)
        assert result == {"format_name": "percent", "extension": ".java"}

    def test_jupytext_format_for_typescript_returns_dict(self):
        """TypeScript jupytext format should return a dict."""
        result = jupytext_format_for("typescript")
        assert isinstance(result, dict)
        assert result == {"format_name": "percent", "extension": ".ts"}

    def test_jupytext_format_for_unsupported_language_raises_valueerror(self):
        """Unsupported language should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: unknown"):
            jupytext_format_for("unknown")


class TestLanguageInfo:
    """Test the language_info function."""

    def test_language_info_for_python(self):
        """Python language_info should contain expected keys."""
        info = language_info("python")
        assert isinstance(info, dict)
        assert info["file_extension"] == ".py"
        assert info["name"] == "python"
        assert info["mimetype"] == "text/x-python"
        assert "codemirror_mode" in info
        assert "pygments_lexer" in info

    def test_language_info_for_cpp(self):
        """C++ language_info should contain expected keys."""
        info = language_info("cpp")
        assert isinstance(info, dict)
        assert info["file_extension"] == ".cpp"
        assert info["name"] == "c++"
        assert info["mimetype"] == "text/x-c++src"

    def test_language_info_for_csharp(self):
        """C# language_info should contain expected keys."""
        info = language_info("csharp")
        assert isinstance(info, dict)
        assert info["file_extension"] == ".cs"
        assert info["name"] == "C#"
        assert info["mimetype"] == "text/x-csharp"

    def test_language_info_for_java(self):
        """Java language_info should contain expected keys."""
        info = language_info("java")
        assert isinstance(info, dict)
        assert info["file_extension"] == ".java"
        assert info["name"] == "Java"
        assert info["mimetype"] == "text/java"

    def test_language_info_for_typescript(self):
        """TypeScript language_info should contain expected keys."""
        info = language_info("typescript")
        assert isinstance(info, dict)
        assert info["file_extension"] == ".ts"
        assert info["name"] == "typescript"

    def test_language_info_for_rust(self):
        """Rust language_info should contain expected keys."""
        info = language_info("rust")
        assert isinstance(info, dict)
        assert info["file_extension"] == ".rs"
        assert info["name"] == "Rust"

    def test_language_info_for_all_languages_has_file_extension(self):
        """All languages should have file_extension in language_info."""
        for lang in config.prog_lang.keys():
            info = language_info(lang)
            assert "file_extension" in info, f"{lang} missing file_extension"
            assert info["file_extension"].startswith(
                "."
            ), f"{lang} file_extension should start with '.'"

    def test_language_info_for_unsupported_language_raises_valueerror(self):
        """Unsupported language should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: unknown"):
            language_info("unknown")


class TestFileExtensionFor:
    """Test the file_extension_for function."""

    def test_file_extension_for_python(self):
        """Python file extension should be .py."""
        assert file_extension_for("python") == ".py"

    def test_file_extension_for_cpp(self):
        """C++ file extension should be .cpp."""
        assert file_extension_for("cpp") == ".cpp"

    def test_file_extension_for_csharp(self):
        """C# file extension should be .cs."""
        assert file_extension_for("csharp") == ".cs"

    def test_file_extension_for_java(self):
        """Java file extension should be .java."""
        assert file_extension_for("java") == ".java"

    def test_file_extension_for_typescript(self):
        """TypeScript file extension should be .ts."""
        assert file_extension_for("typescript") == ".ts"

    def test_file_extension_for_rust(self):
        """Rust file extension should be .rs."""
        assert file_extension_for("rust") == ".rs"

    def test_file_extension_matches_suffix(self):
        """file_extension_for should return the same as suffix_for for all languages."""
        for lang in config.prog_lang.keys():
            extension = file_extension_for(lang)
            suffix = suffix_for(lang)
            assert (
                extension == suffix
            ), f"{lang}: file_extension_for ({extension}) != suffix_for ({suffix})"

    def test_file_extension_for_unsupported_language_raises_valueerror(self):
        """Unsupported language should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: unknown"):
            file_extension_for("unknown")


class TestKernelspecFor:
    """Test the kernelspec_for function."""

    def test_kernelspec_for_python(self):
        """Python kernelspec should be python3."""
        spec = kernelspec_for("python")
        assert isinstance(spec, dict)
        assert spec["name"] == "python3"
        assert spec["language"] == "python"
        assert "display_name" in spec

    def test_kernelspec_for_cpp(self):
        """C++ kernelspec should be xcpp17."""
        spec = kernelspec_for("cpp")
        assert isinstance(spec, dict)
        assert spec["name"] == "xcpp17"
        assert spec["language"] == "C++17"

    def test_kernelspec_for_csharp(self):
        """C# kernelspec should be .net-csharp."""
        spec = kernelspec_for("csharp")
        assert isinstance(spec, dict)
        assert spec["name"] == ".net-csharp"
        assert spec["language"] == "C#"

    def test_kernelspec_for_java(self):
        """Java kernelspec should be java."""
        spec = kernelspec_for("java")
        assert isinstance(spec, dict)
        assert spec["name"] == "java"
        assert spec["language"] == "java"

    def test_kernelspec_for_typescript(self):
        """TypeScript kernelspec should be deno."""
        spec = kernelspec_for("typescript")
        assert isinstance(spec, dict)
        assert spec["name"] == "deno"
        assert spec["language"] == "typescript"

    def test_kernelspec_for_rust(self):
        """Rust kernelspec should be rust."""
        spec = kernelspec_for("rust")
        assert isinstance(spec, dict)
        assert spec["name"] == "rust"
        assert spec["language"] == "rust"

    def test_kernelspec_for_all_languages_has_required_keys(self):
        """All languages should have name, language, and display_name in kernelspec."""
        required_keys = {"name", "language", "display_name"}
        for lang in config.prog_lang.keys():
            spec = kernelspec_for(lang)
            actual_keys = set(spec.keys())
            missing_keys = required_keys - actual_keys
            assert not missing_keys, f"{lang} kernelspec missing keys: {missing_keys}"

    def test_kernelspec_for_unsupported_language_raises_valueerror(self):
        """Unsupported language should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported language: unknown"):
            kernelspec_for("unknown")


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_all_functions_raise_valueerror_for_none(self):
        """All functions should raise appropriate errors for None input."""
        functions = [
            suffix_for,
            jinja_prefix_for,
            jupytext_format_for,
            language_info,
            file_extension_for,
            kernelspec_for,
        ]

        for func in functions:
            with pytest.raises((ValueError, TypeError, KeyError)):
                func(None)  # type: ignore

    def test_language_names_are_lowercase(self):
        """All language names in config should be lowercase."""
        for lang in config.prog_lang.keys():
            assert lang == lang.lower(), f"Language name '{lang}' should be lowercase"
