"""Tests for markdown (.md) notebook file processing.

Verifies that .md files are correctly parsed by jupytext using the "md" format
(auto-detecting standard markdown vs MyST), and that the programming language /
kernel is correctly set from the payload's prog_lang regardless of file format.
"""

import uuid

import pytest
from nbformat import NotebookNode

from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import CompletedOutput, SpeakerOutput

# ============================================================================
# Helpers
# ============================================================================


def make_md_payload(
    markdown_data: str,
    input_file_name: str = "project_setup.md",
    format_: str = "notebook",
    kind: str = "completed",
    language: str = "en",
    prog_lang: str = "python",
) -> NotebookPayload:
    """Create a NotebookPayload for a markdown file."""
    return NotebookPayload(
        input_file=f"/test/{input_file_name}",
        input_file_name=input_file_name,
        output_file="/test/output/notebook",
        data=markdown_data,
        format=format_,
        kind=kind,
        language=language,
        prog_lang=prog_lang,
        correlation_id="test-md-" + uuid.uuid4().hex[:8],
    )


# ============================================================================
# Sample markdown content
# ============================================================================

STANDARD_MARKDOWN_PYTHON = """\
---
jupyter:
  jupytext:
    text_representation:
      format_name: markdown
      extension: .md
  kernelspec:
    display_name: Python 3
    language: python
    name: python3
---

# Project Setup

This is a markdown notebook.

```python
x = 1 + 2
print(x)
```

## Next Section

Some more text.

```python
def greet(name):
    return f"Hello, {name}!"
```
"""

STANDARD_MARKDOWN_JAVA = """\
---
jupyter:
  jupytext:
    text_representation:
      format_name: markdown
      extension: .md
  kernelspec:
    display_name: Java
    language: java
    name: java
---

# Java Project

```java
System.out.println("Hello");
```
"""

MYST_MARKDOWN_PYTHON = """\
---
jupyter:
  jupytext:
    text_representation:
      format_name: myst
      extension: .md
  kernelspec:
    display_name: Python 3
    language: python
    name: python3
---

# MyST Notebook

Some text.

```{code-cell} python
x = 42
print(x)
```

More text.

```{code-cell} python
y = x + 1
```
"""

BARE_MARKDOWN = """\
# Simple Notebook

```python
print("hello")
```
"""

MARKDOWN_WITH_TAGS = """\
---
jupyter:
  jupytext:
    text_representation:
      format_name: markdown
      extension: .md
  kernelspec:
    display_name: Python 3
    language: python
    name: python3
---

# Slide Title

```python tags=["slide"]
x = 1
```

```python tags=["notes"]
# This is a speaker note
```

```python tags=["del"]
# This should be deleted
```

```python
# Regular code
y = 2
```
"""


# ============================================================================
# Format detection tests
# ============================================================================


class TestJupytextReadFormat:
    """Test that _jupytext_read_format returns the correct format."""

    def test_md_file_returns_md_format(self):
        payload = make_md_payload("", input_file_name="project_setup.md")
        fmt = NotebookProcessor._jupytext_read_format(payload)
        assert fmt == "md"

    def test_md_file_returns_md_regardless_of_prog_lang(self):
        for lang in ["python", "java", "cpp", "rust", "csharp", "typescript"]:
            payload = make_md_payload("", input_file_name="slides_topic.md", prog_lang=lang)
            fmt = NotebookProcessor._jupytext_read_format(payload)
            assert fmt == "md", f"Expected 'md' for prog_lang={lang}, got '{fmt}'"

    def test_py_file_returns_py_percent(self):
        payload = make_md_payload("", input_file_name="slides_topic.py", prog_lang="python")
        fmt = NotebookProcessor._jupytext_read_format(payload)
        assert fmt == "py:percent"

    def test_cpp_file_returns_cpp_percent(self):
        payload = make_md_payload("", input_file_name="slides_topic.cpp", prog_lang="cpp")
        fmt = NotebookProcessor._jupytext_read_format(payload)
        assert fmt == "cpp:percent"


# ============================================================================
# Markdown parsing tests (standard markdown format)
# ============================================================================


class TestStandardMarkdownParsing:
    """Test that standard markdown notebooks are correctly parsed into cells."""

    @pytest.mark.asyncio
    async def test_standard_markdown_produces_correct_cell_types(self):
        """Standard markdown should produce markdown and code cells."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_PYTHON)

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_PYTHON, payload)

        cell_types = [cell["cell_type"] for cell in result["cells"]]
        assert "markdown" in cell_types
        assert "code" in cell_types

    @pytest.mark.asyncio
    async def test_standard_markdown_code_cells_have_correct_content(self):
        """Code cells should contain the code from fenced code blocks."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_PYTHON)

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_PYTHON, payload)

        code_sources = [cell["source"] for cell in result["cells"] if cell["cell_type"] == "code"]
        assert any("x = 1 + 2" in src for src in code_sources)
        assert any("def greet(name):" in src for src in code_sources)

    @pytest.mark.asyncio
    async def test_standard_markdown_cells_have_markdown_content(self):
        """Markdown cells should contain the text between code blocks."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_PYTHON)

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_PYTHON, payload)

        md_sources = [cell["source"] for cell in result["cells"] if cell["cell_type"] == "markdown"]
        assert any("Project Setup" in src for src in md_sources)

    @pytest.mark.asyncio
    async def test_bare_markdown_without_header_is_parsed(self):
        """Markdown without a YAML header should still be parsed correctly."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(BARE_MARKDOWN)

        result = await processor.process_notebook_for_spec(BARE_MARKDOWN, payload)

        cell_types = [cell["cell_type"] for cell in result["cells"]]
        assert "markdown" in cell_types
        assert "code" in cell_types

        code_sources = [cell["source"] for cell in result["cells"] if cell["cell_type"] == "code"]
        assert any('print("hello")' in src for src in code_sources)


# ============================================================================
# MyST markdown tests
# ============================================================================


class TestMystMarkdownParsing:
    """Test that MyST markdown notebooks are correctly parsed."""

    @pytest.mark.asyncio
    async def test_myst_markdown_produces_correct_cell_types(self):
        """MyST markdown with ```{code-cell} should produce code cells."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(MYST_MARKDOWN_PYTHON)

        result = await processor.process_notebook_for_spec(MYST_MARKDOWN_PYTHON, payload)

        cell_types = [cell["cell_type"] for cell in result["cells"]]
        assert "code" in cell_types
        assert "markdown" in cell_types

    @pytest.mark.asyncio
    async def test_myst_code_cells_have_correct_content(self):
        """MyST code-cell content should be correctly extracted."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(MYST_MARKDOWN_PYTHON)

        result = await processor.process_notebook_for_spec(MYST_MARKDOWN_PYTHON, payload)

        code_sources = [cell["source"] for cell in result["cells"] if cell["cell_type"] == "code"]
        assert any("x = 42" in src for src in code_sources)
        assert any("y = x + 1" in src for src in code_sources)


# ============================================================================
# Kernel / language_info override tests
# ============================================================================


class TestMarkdownKernelOverride:
    """Test that prog_lang from the payload correctly overrides kernel metadata."""

    @pytest.mark.asyncio
    async def test_python_kernel_set_from_prog_lang(self):
        """A .md file with prog_lang='python' should get the Python kernelspec."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_PYTHON, prog_lang="python")

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_PYTHON, payload)

        assert result.metadata["kernelspec"]["name"] == "python3"
        assert result.metadata["language_info"]["name"] == "python"

    @pytest.mark.asyncio
    async def test_java_kernel_set_from_prog_lang(self):
        """A .md file with prog_lang='java' should get the Java kernelspec."""
        spec = CompletedOutput(format="notebook", prog_lang="java")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_JAVA, prog_lang="java")

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_JAVA, payload)

        assert result.metadata["kernelspec"]["name"] == "java"
        assert result.metadata["kernelspec"]["language"] == "java"
        assert result.metadata["language_info"]["name"] == "Java"

    @pytest.mark.asyncio
    async def test_kernel_override_ignores_file_header_kernel(self):
        """Even if the .md YAML header declares a kernel, prog_lang wins."""
        # The STANDARD_MARKDOWN_JAVA header says Java, but we set prog_lang to python
        spec = CompletedOutput(format="notebook", prog_lang="python")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_JAVA, prog_lang="python")

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_JAVA, payload)

        assert result.metadata["kernelspec"]["name"] == "python3"
        assert result.metadata["language_info"]["name"] == "python"


# ============================================================================
# Output kind tests (speaker notes, code-along)
# ============================================================================


class TestMarkdownOutputKinds:
    """Test that markdown notebooks work with different output kinds."""

    @pytest.mark.asyncio
    async def test_speaker_output_from_markdown(self):
        """Speaker output should work for markdown notebooks."""
        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_PYTHON, kind="speaker")

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_PYTHON, payload)

        assert len(result["cells"]) > 0

    @pytest.mark.asyncio
    async def test_completed_output_from_markdown(self):
        """Completed output should work for markdown notebooks."""
        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_md_payload(STANDARD_MARKDOWN_PYTHON, kind="completed")

        result = await processor.process_notebook_for_spec(STANDARD_MARKDOWN_PYTHON, payload)

        assert len(result["cells"]) > 0
