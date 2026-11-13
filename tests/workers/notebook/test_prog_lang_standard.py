"""The course's language standard, from the spec to the notebook the worker writes (#1228).

``<prog-lang standard="23">cpp</prog-lang>`` has to travel
``CourseSpec`` → ``NotebookFile`` → ``ProcessNotebookOperation`` →
``NotebookPayload`` → the worker's notebook metadata, where the kernel name
decides which kernel executes the deck. Each hop is unit-tested where it
lives; these tests cover the two ends that are only reached through real
objects: the payloads a spec produces, and the notebook a payload produces.

Kernel *execution* at the selected standard is a Docker test
(``test_language_docker_execution.py``, the ``cpp23`` case).
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_spec import CourseSpec
from clm.core.messaging.notebook_classes import NotebookPayload
from clm.core.operations.process_notebook import ProcessNotebookOperation
from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import CompletedOutput

CPP_DECK = """\
// %% [markdown] lang="en" tags=["slide"]
// # Ranges

// %%
#include <vector>

// %%
std::vector<int> numbers{1, 2, 3};
"""

COURSE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name>
        <de>C++ Kurs</de>
        <en>C++ Course</en>
    </name>
    PROG_LANG
    <description>
        <de>Ein C++ Kurs</de>
        <en>A C++ course</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>ranges</topic>
            </topics>
        </section>
    </sections>
</course>
"""


def _cpp_payload(standard: str = "", format_: str = "notebook") -> NotebookPayload:
    return NotebookPayload(
        input_file="/test/slides_ranges.cpp",
        input_file_name="slides_ranges.cpp",
        output_file="/test/output/01 Ranges",
        data=CPP_DECK,
        format=format_,
        kind="completed",
        language="en",
        prog_lang="cpp",
        prog_lang_standard=standard,
        correlation_id="test-std-" + uuid.uuid4().hex[:8],
    )


class TestWorkerWritesTheKernelOfTheStandard:
    """The kernel name in the notebook is what executes it — and what students open."""

    @pytest.mark.asyncio
    async def test_cpp23_notebook_names_the_xcpp23_kernel(self):
        processor = NotebookProcessor(CompletedOutput(format="notebook", prog_lang="cpp"))
        payload = _cpp_payload("23")

        notebook = await processor.process_notebook_for_spec(CPP_DECK, payload)

        assert notebook.metadata["kernelspec"] == {
            "display_name": "C++23",
            "language": "cpp",
            "name": "xcpp23",
        }
        assert notebook.metadata["language_info"]["version"] == "23"

    @pytest.mark.asyncio
    async def test_default_notebook_still_names_the_xcpp20_kernel(self):
        processor = NotebookProcessor(CompletedOutput(format="notebook", prog_lang="cpp"))
        payload = _cpp_payload()

        notebook = await processor.process_notebook_for_spec(CPP_DECK, payload)

        assert notebook.metadata["kernelspec"]["name"] == "xcpp20"
        assert notebook.metadata["kernelspec"]["display_name"] == "C++20"
        assert notebook.metadata["language_info"]["version"] == "20"

    @pytest.mark.asyncio
    async def test_the_standard_changes_nothing_but_the_metadata(self):
        """Same cells either way: the standard selects a kernel, not content."""
        default = await NotebookProcessor(
            CompletedOutput(format="notebook", prog_lang="cpp")
        ).process_notebook_for_spec(CPP_DECK, _cpp_payload())
        cpp23 = await NotebookProcessor(
            CompletedOutput(format="notebook", prog_lang="cpp")
        ).process_notebook_for_spec(CPP_DECK, _cpp_payload("23"))

        assert [cell.source for cell in cpp23.cells] == [cell.source for cell in default.cells]


class TestSpecStandardReachesEveryPayload:
    """``<prog-lang standard>`` must reach the payload of every output of a deck."""

    def _course(self, tmp_path: Path, prog_lang_element: str) -> Course:
        data_dir = tmp_path / "course"
        topic_dir = data_dir / "slides" / "module_100_test" / "topic_100_ranges"
        topic_dir.mkdir(parents=True)
        (topic_dir / "slides_ranges.cpp").write_text(
            '// j2 from \'macros.j2\' import header\n// {{ header("Ranges", "Ranges") }}\n\n'
            "// %%\nint x = 1;\n",
            encoding="utf-8",
        )
        spec = CourseSpec.from_file(io.StringIO(COURSE_XML.replace("PROG_LANG", prog_lang_element)))
        return Course.from_spec(spec, data_dir, tmp_path / "out")

    async def _payloads(self, course: Course) -> list[NotebookPayload]:
        def unwrap(operation) -> list[ProcessNotebookOperation]:
            if isinstance(operation, ProcessNotebookOperation):
                return [operation]
            found: list[ProcessNotebookOperation] = []
            for child in getattr(operation, "operations", ()):
                found.extend(unwrap(child))
            return found

        notebooks = [f for f in course.files if isinstance(f, NotebookFile)]
        assert len(notebooks) == 1
        payloads = []
        for target in course.output_targets:
            operation = await notebooks[0].get_processing_operation(
                target.output_root, target=target
            )
            for op in unwrap(operation):
                payloads.append(await op.payload())
        return payloads

    @pytest.mark.asyncio
    async def test_cpp23_course_sends_the_standard_with_every_payload(self, tmp_path):
        course = self._course(tmp_path, '<prog-lang standard="23">cpp</prog-lang>')

        payloads = await self._payloads(course)

        assert len(payloads) > 1
        assert {p.prog_lang for p in payloads} == {"cpp"}
        assert {p.prog_lang_standard for p in payloads} == {"23"}
        # ... for every output format, the code export included.
        assert {"html", "notebook", "code"} <= {p.format for p in payloads}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "element", ["<prog-lang>cpp</prog-lang>", '<prog-lang standard="20">cpp</prog-lang>']
    )
    async def test_default_course_sends_no_standard(self, tmp_path, element):
        payloads = await self._payloads(self._course(tmp_path, element))

        assert payloads
        assert {p.prog_lang_standard for p in payloads} == {""}

    @pytest.mark.asyncio
    async def test_the_same_deck_gets_different_cache_keys_per_standard(self, tmp_path):
        """A deck shared by a C++20 and a C++23 course must not share cache entries."""
        cpp20 = await self._payloads(self._course(tmp_path / "a", "<prog-lang>cpp</prog-lang>"))
        cpp23 = await self._payloads(
            self._course(tmp_path / "b", '<prog-lang standard="23">cpp</prog-lang>')
        )

        def keys(payloads):
            return {(p.kind, p.language, p.format): p.execution_cache_hash() for p in payloads}

        keys20, keys23 = keys(cpp20), keys(cpp23)
        assert keys20.keys() == keys23.keys()
        assert all(keys20[k] != keys23[k] for k in keys20)
