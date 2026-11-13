"""Which C++ standard library do the notebook image's kernel and compiler see? (#1228)

The notebook image carries two C++ front ends: the xeus-cpp kernel (an
embedded clang) that executes a deck, and the system g++ that compiles the
deck's code export. They share **one** standard library, because the kernel
does not bring its own headers — it asks the ``c++`` on ``PATH`` for its
include paths. The base image's g++ therefore decides which C++23 library
features a deck can use, in the notebook *and* in the exported project.

That coupling is invisible in the Dockerfile and was wrong in two ways before
#1228: the two image variants sat on different distributions (g++ 13 under
``full``, g++ 14 under ``lite``), and neither was new enough — ``<print>``
needs libstdc++ 14, ``<flat_map>`` 15, and pipe-form ``std::ranges::to`` under
the kernel's clang 21 needs a libstdc++ newer than the 14.2.0 release (the
only 14 Ubuntu 24.04 offered). So these tests pin the properties a base-image
bump must not lose:

- the system g++ compiles and runs a translation unit using the C++23 library;
- the ``xcpp23`` kernel executes the same library features;
- kernel and compiler report the same libstdc++ release.

Features are *used*, not version-sniffed: a result computed through
``std::ranges::to`` and printed by ``std::println`` cannot appear unless both
exist. Like every Docker test this runs against the ``lite`` image CI builds;
the kernel and the toolchain come from stages both variants share.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.docker_image_helpers import docker_available, find_notebook_image

pytestmark = [pytest.mark.docker, pytest.mark.integration]

#: The oldest libstdc++ release that provides everything below (see the module
#: docstring for why 14 is not enough).
MINIMUM_LIBSTDCXX_RELEASE = 15

#: Sums 2, 4, …, 12 — through a pipe-form ``std::ranges::to`` and a
#: ``std::flat_map`` — and prints the 42 with ``std::println``.
_TRANSLATION_UNIT = r"""
#include <algorithm>
#include <flat_map>
#include <functional>
#include <print>
#include <ranges>
#include <vector>

int main() {
    auto doubled = std::views::iota(1, 7)
                 | std::views::transform([](int i) { return 2 * i; })
                 | std::ranges::to<std::vector>();
    std::flat_map<int, int> by_index;
    for (auto [index, value] : std::views::enumerate(doubled)) {
        by_index[static_cast<int>(index)] = value;
    }
    int total = 0;
    for (auto [index, value] : by_index) { total += value; }
    std::println("export-result={} glibcxx={}", total, _GLIBCXX_RELEASE);
}
"""

#: Runs inside the image: executes cells in the ``xcpp23`` kernel and relays
#: their output. The kernel-killing shape the validator knows (a lambda inside
#: a display macro, #1044) is avoided — the lambda sits in a plain declaration.
_KERNEL_PROBE = r"""
import queue
import sys

from jupyter_client.manager import start_new_kernel

CELLS = [
    "#include <algorithm>\n#include <functional>\n#include <iostream>\n"
    "#include <print>\n#include <ranges>\n#include <vector>",
    "auto doubled = std::views::iota(1, 7)"
    " | std::views::transform([](int i) { return 2 * i; })"
    " | std::ranges::to<std::vector>();",
    'std::println("kernel-result={}", std::ranges::fold_left(doubled, 0, std::plus{}));',
    'std::cout << "kernel-glibcxx=" << _GLIBCXX_RELEASE << std::endl;',
]

manager, client = start_new_kernel(kernel_name="xcpp23", startup_timeout=120)
failed = False
try:
    for cell in CELLS:
        message_id = client.execute(cell)
        while True:
            try:
                message = client.get_iopub_msg(timeout=120)
            except queue.Empty:
                print(f"TIMEOUT in cell: {cell!r}", file=sys.stderr)
                failed = True
                break
            if message["parent_header"].get("msg_id") != message_id:
                continue
            kind, content = message["msg_type"], message["content"]
            if kind == "stream":
                stream = sys.stdout if content["name"] == "stdout" else sys.stderr
                print(content["text"], end="", file=stream)
            elif kind == "error":
                print(f"ERROR in cell: {cell!r}", file=sys.stderr)
                print("\n".join(content.get("traceback", [])), file=sys.stderr)
                failed = True
            elif kind == "status" and content["execution_state"] == "idle":
                break
        if failed:
            break
finally:
    client.stop_channels()
    manager.shutdown_kernel(now=True)
sys.exit(1 if failed else 0)
"""


@pytest.fixture(scope="module")
def image() -> str:
    if not docker_available():
        pytest.skip("Docker daemon not available")
    found = find_notebook_image()
    if found is None:
        pytest.skip("No notebook Docker image available (run: clm docker build)")
    return found


def _run(image: str, argv: list[str], *, timeout: int = 300) -> str:
    """Run ``argv`` in the image as an arbitrary uid and return stdout.

    The uid exists nowhere in the image, like the host uid the executor runs
    workers under on native Linux.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "4242:4242", "--entrypoint", argv[0], image, *argv[1:]],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"{result.returncode}: {result.stdout}\n{result.stderr}"
    return result.stdout


def _field(output: str, name: str) -> str:
    """The value of the ``name=value`` token in ``output``."""
    for token in output.split():
        key, _, value = token.partition("=")
        if key == name:
            return value
    raise AssertionError(f"no {name}= in output:\n{output}")


def _compile_and_run_export(image: str) -> str:
    script = (
        'set -eu; dir="$(mktemp -d)"; cat > "$dir/unit.cpp"; '
        'c++ -std=c++23 "$dir/unit.cpp" -o "$dir/unit"; "$dir/unit"'
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--user", "4242:4242", "--entrypoint", "sh", image]
        + ["-c", script],
        input=_TRANSLATION_UNIT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"{result.returncode}: {result.stdout}\n{result.stderr}"
    return result.stdout


def test_the_system_compiler_builds_and_runs_the_cpp23_library(image: str) -> None:
    """What the code export's CMake project gets: ``c++`` on ``PATH``, C++23.

    The binary is *run*, not only compiled: a compiler whose library headers
    are newer than the ``libstdc++.so`` the image loads would link and then
    fail at start-up.
    """
    output = _compile_and_run_export(image)

    assert _field(output, "export-result") == "42", output
    assert int(_field(output, "glibcxx")) >= MINIMUM_LIBSTDCXX_RELEASE, output


def test_the_xcpp23_kernel_executes_the_cpp23_library(image: str) -> None:
    """``std::println`` output of a value computed via pipe-form ``ranges::to``.

    Under libstdc++ 13 the cell fails on ``<print>``; under the 14.2.0
    release on the pipe (clang 21 rejects its ``std::forward_like``).
    """
    output = _run(image, ["python", "-c", _KERNEL_PROBE])

    assert _field(output, "kernel-result") == "42", output


def test_the_kernel_and_the_compiler_share_one_standard_library(image: str) -> None:
    """A deck must not compile in the notebook and fail in its export, or vice versa.

    The kernel takes its libstdc++ headers from the ``c++`` on ``PATH``; this
    fails if a second set of headers (a conda ``libstdcxx-devel``, a conda
    compiler shadowing the system one) ever comes between them.
    """
    kernel = _field(_run(image, ["python", "-c", _KERNEL_PROBE]), "kernel-glibcxx")
    compiler = _field(_compile_and_run_export(image), "glibcxx")

    assert kernel == compiler, (
        f"the xcpp23 kernel sees libstdc++ {kernel}, the system compiler libstdc++ {compiler}"
    )
    assert int(kernel) >= MINIMUM_LIBSTDCXX_RELEASE
