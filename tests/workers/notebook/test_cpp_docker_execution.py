"""Does C++ still work? — executable coverage for the C++ course pipeline.

Everything about C++ *except execution* is unit-tested elsewhere (the
`cpp:percent` jupytext round-trip, the ``// j2`` jinja prefix, comment-token
parsing, code extraction, the CMake export). Execution was the hole: the only
Docker test that ran a C++ notebook deliberately ran a **broken** one to check
error attribution, so "valid C++ compiles, runs, and its output reaches the
deck" was asserted nowhere. A course repository would find that out instead.

The two tests here are the cheap half of that coverage, and both run inside the
existing Docker job against the image CI already builds — no new image, no added
build time:

- :func:`test_image_ships_every_kernel_clm_requests` — a kernel *rename* in an
  image bump breaks every deck in the language at once, silently, and it has
  happened before (the xeus-cling → xeus-cpp move, still referenced from
  ``sqlite_backend.py`` and ``process_notebook.py``). Comparing the names CLM
  asks for against the names the image installs catches that in seconds, for
  every language rather than only C++.
- :class:`TestCppExecutionInDocker` — a valid C++ deck goes through the real
  Docker notebook worker and the executed output has to carry the value the
  program computed. This is the canary for "C++ courses still build".

What is deliberately *not* here: a full ``clm build`` of a C++ course spec
(output targets, code extraction, per-language directories), and the other
kernels the images ship (C#, Java, TypeScript) which are configured by CLM and
executed by nothing in CI. Those are the same shape of gap one level out; see
the C++ coverage note in the Phase 2 handover.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from clm.workers.notebook.utils.prog_lang_utils import config as prog_lang_config
from clm.workers.notebook.utils.prog_lang_utils import kernelspec_for
from tests.docker_image_helpers import docker_available, find_notebook_image

pytestmark = [
    pytest.mark.docker,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available(), reason="Docker daemon not available"),
]

#: Languages CLM configures a kernel for whose kernel the images do **not**
#: install. Pinned rather than skipped so both directions are visible: a new
#: hole fails the test, and an image that starts shipping Rust also fails it —
#: with "tighten this set", which is the right thing to do at that point.
#:
#: Rust is configured (``prog_lang_utils`` names an ``evcxr``-style ``rust``
#: kernel) but no Dockerfile installs it, so a Rust deck in Docker mode would
#: fail with ``NoSuchKernel``. That is a real limitation, recorded here because
#: nothing else states it.
KNOWN_MISSING_KERNELS = {"rust"}


def _kernelspecs_in(image: str) -> set[str]:
    """Kernel names installed in ``image``, via ``jupyter kernelspec list``."""
    import docker

    client = docker.from_env()
    output = client.containers.run(
        image,
        entrypoint="jupyter",
        command=["kernelspec", "list", "--json"],
        remove=True,
        stdout=True,
        stderr=False,
    )
    return set(json.loads(output)["kernelspecs"])


def test_image_ships_every_kernel_clm_requests() -> None:
    """Every kernel CLM names must exist in the image, or be a known hole.

    ``kernelspec_for(lang)["name"]`` is written into each notebook CLM sends to
    a worker, and the kernel is looked up by that exact name at execution time.
    A mismatch is not a graceful degradation: every deck in the language fails
    with ``NoSuchKernel``, and the build's only clue is the kernel name.
    """
    image = find_notebook_image()
    if image is None:
        pytest.skip("No notebook Docker image available (run: clm docker build)")

    installed = _kernelspecs_in(image)
    requested = {lang: kernelspec_for(lang)["name"] for lang in prog_lang_config.prog_lang}
    missing = {lang for lang, kernel in requested.items() if kernel not in installed}

    assert missing == KNOWN_MISSING_KERNELS, (
        f"kernels CLM requests but {image} does not install: "
        f"{ {lang: requested[lang] for lang in sorted(missing)} }\n"
        f"expected exactly {sorted(KNOWN_MISSING_KERNELS)} to be missing; "
        f"image installs {sorted(installed)}.\n"
        f"A kernel that disappeared or was renamed breaks every deck in that "
        f"language — fix the image or the prog_lang config, do not relax this."
    )


class TestCppExecutionInDocker:
    """A valid C++ deck must compile, run, and carry its output into the deck."""

    #: Percent-format C++ deck. The computed value is deliberately *not* a
    #: literal in the source: asserting on ``42`` proves the kernel evaluated
    #: ``6 * 7``, where asserting on a printed constant would pass even if the
    #: cell output were copied through without execution.
    DECK = """// %% [markdown]
// # C++ execution smoke test

// %%
#include <iostream>

// %%
int product(int a, int b) { return a * b; }

// %%
std::cout << "cpp-smoke-result=" << product(6, 7) << std::endl;
"""

    @pytest.fixture
    def cpp_env(self, tmp_path: Path) -> dict[str, Path]:
        """A jobs DB, a workspace, and a data dir holding one C++ deck.

        ``tmp_path`` rather than a hand-rolled ``mkdtemp``: it is already
        per-test and per-xdist-worker, and pytest keeps the last runs' copies
        around for post-mortem, which matters when a container is involved.
        """
        from clm.infrastructure.database.schema import init_database

        db_path = tmp_path / "jobs.db"
        init_database(db_path)

        workspace = tmp_path / "output"
        workspace.mkdir()

        topic_dir = tmp_path / "data" / "slides" / "test_cpp_smoke"
        topic_dir.mkdir(parents=True)
        (topic_dir / "cpp_smoke.cpp").write_text(self.DECK, encoding="utf-8")

        return {
            "db_path": db_path,
            "workspace": workspace,
            "data_dir": tmp_path / "data",
            "topic_dir": topic_dir,
        }

    def _await_workers(self, db_path: Path, expected: int, timeout: float = 60.0) -> None:
        """Poll until ``expected`` workers report idle/busy.

        Polling with a generous deadline, never a fixed sleep: activation is
        subprocess- and container-gated, so any single sleep is either flaky or
        wasted time (issue #163).
        """
        deadline = time.monotonic() + timeout
        active = 0
        while time.monotonic() < deadline:
            conn = sqlite3.connect(db_path)
            try:
                active = conn.execute(
                    "SELECT COUNT(*) FROM workers WHERE status IN ('idle', 'busy')"
                ).fetchone()[0]
            finally:
                conn.close()
            if active >= expected:
                return
            time.sleep(0.25)
        raise TimeoutError(f"Expected {expected} active worker(s) within {timeout}s; got {active}")

    def test_cpp_deck_executes_and_html_carries_the_result(self, cpp_env: dict[str, Path]) -> None:
        """A ``completed`` HTML deck must contain the value the C++ code computed.

        HTML, not notebook output, because that is where execution happens:
        ``evaluate_for_html`` gates it (``output_spec.py``), so a notebook-format
        job legitimately writes a deck with no outputs at all and would assert
        nothing about the kernel.
        """
        from clm.infrastructure.database.job_queue import JobQueue
        from clm.infrastructure.workers.config_loader import load_worker_config
        from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

        image = find_notebook_image()
        if image is None:
            pytest.skip("No notebook Docker image available (run: clm docker build)")

        worker_config = load_worker_config(
            {
                "default_execution_mode": "docker",
                "notebook_count": 1,
                "plantuml_count": 0,
                "drawio_count": 0,
                "auto_start": True,
                "auto_stop": True,
                "reuse_workers": False,
            }
        )
        worker_config.notebook.image = image

        manager = WorkerLifecycleManager(
            config=worker_config,
            db_path=cpp_env["db_path"],
            workspace_path=cpp_env["workspace"],
            data_dir=cpp_env["data_dir"],
        )

        workers: list = []
        try:
            workers = manager.start_managed_workers()
            assert workers, "No workers started"
            self._await_workers(cpp_env["db_path"], len(workers))

            queue = JobQueue(cpp_env["db_path"])
            output_file = cpp_env["workspace"] / "public" / "cpp_smoke.html"
            output_file.parent.mkdir(parents=True, exist_ok=True)

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(cpp_env["topic_dir"] / "cpp_smoke.cpp"),
                output_file=str(output_file),
                content_hash="cpp-smoke-execution",
                payload={
                    "kind": "completed",
                    "prog_lang": "cpp",
                    "language": "en",
                    "format": "html",
                    "source_topic_dir": str(cpp_env["topic_dir"]),
                },
            )

            # C++ cells are compiled, so give this a wider budget than the
            # Python equivalents; it normally finishes in well under a minute.
            deadline = time.monotonic() + 180.0
            job = queue.get_job(job_id)
            while time.monotonic() < deadline and job.status not in ("completed", "failed"):
                time.sleep(1)
                job = queue.get_job(job_id)

            assert job.status == "completed", (
                f"C++ deck did not build: status {job.status!r}\nError: {job.error}"
            )
            assert output_file.is_file(), f"No output written to {output_file}"

            html = output_file.read_text(encoding="utf-8", errors="replace")
            # ``6 * 7`` is computed by the program, so the rendered value can
            # only be there if the kernel ran it — a deck copied through without
            # execution contains the source but not the 42.
            assert "cpp-smoke-result=42" in html, (
                "The executed deck does not contain the value the C++ code "
                "computes, so the C++ kernel did not run it (a compile failure "
                "absorbed by skip-errors looks like this too — check the worker "
                f"log).\nOutput written to {output_file}"
            )
        finally:
            manager.stop_managed_workers(workers)
