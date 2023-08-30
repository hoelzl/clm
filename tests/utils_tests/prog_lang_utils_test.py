from typing import Mapping

import pytest

from clm.utils.prog_lang_utils import suffix_for, language_info, kernelspec_for


def test_suffix_for():
    assert suffix_for("cpp") == "cpp"
    assert suffix_for("java") == "java"
    assert suffix_for("python") == "py"
    assert suffix_for("rust") == "rs"
    with pytest.raises(ValueError, match="Unsupported language: fortran"):
        suffix_for("fortran")


def test_language_info_for_cpp():
    info = language_info("cpp")
    assert isinstance(info, Mapping)
    assert info["codemirror_mode"] == "text/x-c++src"
    assert info["file_extension"] == ".cpp"
    assert info["mimetype"] == "text/x-c++src"
    assert info["name"] == "c++"
    assert info["version"] == "17"


def test_language_info_for_java():
    info = language_info("java")
    assert isinstance(info, Mapping)
    assert info["codemirror_mode"] == "java"
    assert info["file_extension"] == ".java"
    assert info["mimetype"] == "text/java"
    assert info["name"] == "Java"
    assert info["pygments_lexer"] == "java"


def test_language_info_for_python():
    info = language_info("python")
    assert isinstance(info, Mapping)
    assert info["codemirror_mode"] == {"name": "ipython", "version": 3}
    assert info["file_extension"] == ".py"
    assert info["mimetype"] == "text/x-python"
    assert info["name"] == "python"
    assert info["nbconvert_exporter"] == "python"
    assert info["pygments_lexer"] == "ipython3"


def test_language_info_for_rust():
    info = language_info("rust")
    assert isinstance(info, Mapping)
    assert info["codemirror_mode"] == "rust"
    assert info["file_extension"] == ".rs"
    assert info["mimetype"] == "text/rust"
    assert info["name"] == "Rust"
    assert info["pygment_lexer"] == "rust"
    assert info["version"] == ""


def test_language_info_for_unknown_language():
    with pytest.raises(ValueError, match="Unsupported language: fortran"):
        language_info("fortran")


def test_kernelspec_for_python():
    spec = kernelspec_for("python")
    assert isinstance(spec, Mapping)
    assert spec["display_name"] == "Python 3 (ipykernel)"
    assert spec["language"] == "python"
    assert spec["name"] == "python3"


def test_kernelspec_for_cpp():
    spec = kernelspec_for("cpp")
    assert isinstance(spec, Mapping)
    assert spec["display_name"] == "C++17"
    assert spec["language"] == "C++17"
    assert spec["name"] == "xcpp17"


def test_kernelspec_for_rust():
    spec = kernelspec_for("rust")
    assert isinstance(spec, Mapping)
    assert spec["display_name"] == "Rust"
    assert spec["language"] == "rust"
    assert spec["name"] == "rust"


def test_kernelspec_for_unknown_language():
    with pytest.raises(ValueError, match="Unsupported language: fortran"):
        kernelspec_for("fortran")
