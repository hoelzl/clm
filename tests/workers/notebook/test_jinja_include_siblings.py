"""Tests for resolving ``{% include %}`` against a notebook's topic siblings.

A slide deck may want to show a file that lives next to it in the topic — e.g.
a C++ ``add.h`` header rendered inside a fenced code block via
``{% include "add.h" %}``. Jinja's bundled ``PackageLoader`` only searches the
per-language ``templates_<lang>`` directory inside the ``clm`` package, so such
an include used to fail with ``TemplateNotFound``. The processor now searches
the package loader first and falls back to the notebook's sibling files:

* ``payload.other_files`` (base64) → ``DictLoader`` (direct mode), and
* ``source_dir`` on disk → ``FileSystemLoader`` (Docker source-mount mode).

The bundled macros must still win on a name clash, and binary siblings must be
skipped rather than crashing the build.
"""

from __future__ import annotations

from base64 import b64encode
from pathlib import Path

import pytest

from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import SpeakerOutput

ADD_H = "int add(int a, int b);\n"


def _make_processor() -> NotebookProcessor:
    spec = SpeakerOutput(format="notebook", language="en", prog_lang="cpp")
    proc = NotebookProcessor(spec)
    proc._author = "Author"
    proc._organization = "Org"
    return proc


def _payload(data: str, other_files: dict[str, bytes] | None = None) -> NotebookPayload:
    return NotebookPayload(
        data=data,
        input_file="/t/module_110/topic_140/slides_header.cpp",
        input_file_name="slides_header.cpp",
        output_file="/t/out.ipynb",
        kind="speaker",
        prog_lang="cpp",
        language="en",
        format="notebook",
        correlation_id="cid-include",
        author="Author",
        organization="Org",
        other_files=other_files or {},
    )


async def test_include_resolves_sibling_from_other_files() -> None:
    data = '// %% [markdown]\n// ```cpp\n// {% include "add.h" %}\n// ```\n'
    payload = _payload(data, {"add.h": b64encode(ADD_H.encode("utf-8"))})
    proc = _make_processor()

    expanded = await proc.load_and_expand_jinja_template(
        payload.data, payload.input_file_name, payload.correlation_id, payload
    )

    assert "int add(int a, int b);" in expanded


async def test_include_resolves_sibling_from_source_dir(tmp_path: Path) -> None:
    (tmp_path / "add.h").write_text(ADD_H, encoding="utf-8")
    data = '// %% [markdown]\n// ```cpp\n// {% include "add.h" %}\n// ```\n'
    payload = _payload(data)
    proc = _make_processor()

    expanded = await proc.load_and_expand_jinja_template(
        payload.data,
        payload.input_file_name,
        payload.correlation_id,
        payload,
        tmp_path,
    )

    assert "int add(int a, int b);" in expanded


async def test_missing_sibling_still_raises() -> None:
    """No silent pass-through: an include with no matching sibling and no
    bundled template must still fail loudly."""
    data = '// %% [markdown]\n// {% include "does_not_exist.h" %}\n'
    payload = _payload(data)
    proc = _make_processor()

    with pytest.raises(Exception):  # noqa: B017,PT011 - jinja2.TemplateNotFound
        await proc.load_and_expand_jinja_template(
            payload.data, payload.input_file_name, payload.correlation_id, payload
        )


async def test_bundled_macro_not_shadowed_by_sibling() -> None:
    """A sibling named like a bundled template must not override it: the
    package loader is searched first, so ``macros.j2`` still resolves to the
    shipped macros."""
    sabotage = b64encode(b"SIBLING SABOTAGE")
    data = "// j2 from 'macros.j2' import header\n// {{ header('Titel', 'Title') }}\n"
    payload = _payload(data, {"macros.j2": sabotage})
    proc = _make_processor()

    expanded = await proc.load_and_expand_jinja_template(
        payload.data, payload.input_file_name, payload.correlation_id, payload
    )

    assert "SIBLING SABOTAGE" not in expanded
    assert "Title" in expanded


async def test_binary_sibling_is_skipped() -> None:
    """A non-UTF-8 sibling (e.g. a binary asset) must be skipped, not crash
    environment construction; text siblings still resolve."""
    data = '// %% [markdown]\n// {% include "add.h" %}\n'
    payload = _payload(
        data,
        {
            "logo.bin": b64encode(b"\xff\xfe\x00\x01binary"),
            "add.h": b64encode(ADD_H.encode("utf-8")),
        },
    )
    proc = _make_processor()

    expanded = await proc.load_and_expand_jinja_template(
        payload.data, payload.input_file_name, payload.correlation_id, payload
    )

    assert "int add(int a, int b);" in expanded
