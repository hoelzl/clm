"""Does each course language still execute? — one Docker case per language.

CLM configures six programming languages and the notebook images ship kernels
for five of them, but only Python was ever executed by a test. Everything
*around* execution is unit-tested per language (the percent round-trip, the
comment token, the jinja prefix, code extraction, the CMake export); execution
itself was covered for Python alone, so a kernel that stopped working — or an
image bump that renamed one — would first be noticed by a course repository.

Two kinds of check, both inside the existing Docker job, against the image CI
already builds. No new image and no added build time: every kernel here comes
from the Dockerfile's shared ``common`` stage, so the `lite` image has them all.

- :func:`test_image_ships_every_kernel_clm_requests` — cheap and total: the
  names CLM asks for against the names the image installs. A rename breaks every
  deck in a language at once, and the xeus-cling → xeus-cpp move did exactly
  that.
- :class:`TestLanguageExecutionInDocker` — one deck per language through the real
  Docker worker, asserting the rendered deck carries a value the *code* computes.

**Each case must use an HTML target.** Execution is gated by
``evaluate_for_html`` (``output_spec.py``), so a ``format: notebook`` job writes
a deck with no outputs at all — an earlier version of the C++ case passed that
way while asserting nothing about the kernel.

Still not covered here: a full ``clm build`` of a non-Python course spec, where
output targets, code extraction, per-language directories and the CMake export
would be exercised together the way a course repository uses them.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from clm.core.utils.prog_lang_utils import config as prog_lang_config
from clm.core.utils.prog_lang_utils import kernelspec_for, line_comment_for, suffix_for
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
#: Rust is configured but no Dockerfile installs a Rust kernel, so a Rust deck in
#: Docker mode fails with ``NoSuchKernel``. **Measured cost of adding one**
#: (probe image on top of `lite-test`, 2026-07-25): +856 MB of layers and +4 min
#: of build, because conda-forge has no ``evcxr_jupyter`` package — the only
#: route is a Rust toolchain (642 MB) plus ``cargo install --locked
#: evcxr_jupyter`` (compiles rust-analyzer's crates), with cmake/pkg-config for
#: its zmq dependency (171 MB). It would also add crates.io and rustup.rs to the
#: image build's external-fetch surface, which is the documented flake source in
#: `ci.yml`.
#:
#: And the image is only half of it: the Rust *config* was never finished —
#: ``line_comment_for("rust")`` returns ``#`` and the jupytext format is ``md``,
#: neither of which round-trips a ``.rs`` percent deck. So "add the kernel" is
#: not a one-line change even after paying for the image.
KNOWN_MISSING_KERNELS = {"rust"}


@dataclass(frozen=True)
class LanguageCase:
    """One language's smoke deck.

    Attributes:
        prog_lang: The ``prog_lang`` CLM knows the language by.
        cells: Percent-format cell bodies, in order. Assembled with the
            language's own comment token so the deck is written exactly the way
            a course author would write it.
        marker: Substring the executed deck must contain. Always
            ``smoke-result-<lang>=42``, where 42 is *computed* by the code — a
            literal would pass even if nothing ran.
    """

    prog_lang: str
    cells: tuple[str, ...]

    @property
    def marker(self) -> str:
        return f"smoke-result-{self.prog_lang}=42"


def _case(prog_lang: str, *cells: str) -> LanguageCase:
    return LanguageCase(prog_lang=prog_lang, cells=cells)


#: One case per language whose kernel the images ship. Each computes 6 * 7 and
#: prints it with a language-tagged marker, so a deck that was copied through
#: without execution cannot satisfy the assertion.
LANGUAGE_CASES = [
    _case(
        "python",
        "def product(a, b):\n    return a * b",
        'print(f"smoke-result-python={product(6, 7)}")',
    ),
    _case(
        "cpp",
        "#include <iostream>",
        "int product(int a, int b) { return a * b; }",
        'std::cout << "smoke-result-cpp=" << product(6, 7) << std::endl;',
    ),
    _case(
        "csharp",
        "int Product(int a, int b) => a * b;",
        'Console.WriteLine($"smoke-result-csharp={Product(6, 7)}");',
    ),
    _case(
        "java",
        "int product(int a, int b) { return a * b; }",
        'System.out.println("smoke-result-java=" + product(6, 7));',
    ),
    _case(
        "typescript",
        "function product(a: number, b: number): number { return a * b; }",
        "console.log(`smoke-result-typescript=${product(6, 7)}`);",
    ),
]

#: Sanity check on the matrix itself: every language with a shipped kernel must
#: have a case. Without this, dropping a case would silently shrink coverage.
EXPECTED_LANGUAGES = set(prog_lang_config.prog_lang) - KNOWN_MISSING_KERNELS


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


def test_every_shipped_language_has_an_execution_case() -> None:
    """The matrix must cover every language whose kernel the images install."""
    assert {case.prog_lang for case in LANGUAGE_CASES} == EXPECTED_LANGUAGES, (
        "LANGUAGE_CASES and the prog_lang config have drifted. Add a case for a "
        "new language, or add it to KNOWN_MISSING_KERNELS if no image ships its "
        "kernel — do not just delete the case."
    )


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


class TestLanguageExecutionInDocker:
    """A valid deck must run and carry its output into the rendered deck."""

    def _deck(self, case: LanguageCase) -> str:
        """Assemble ``case`` into a percent-format deck for its language."""
        token = line_comment_for(case.prog_lang)
        parts = [f"{token} %% [markdown]", f"{token} # {case.prog_lang} execution smoke test"]
        for cell in case.cells:
            parts.extend(["", f"{token} %%", cell])
        return "\n".join(parts) + "\n"

    def _env(self, tmp_path: Path, case: LanguageCase) -> dict[str, Path]:
        """A jobs DB, a workspace, and a data dir holding this language's deck.

        ``tmp_path`` rather than a hand-rolled ``mkdtemp``: it is already
        per-test and per-xdist-worker, and pytest keeps the last runs' copies
        around for post-mortem, which matters when a container is involved.
        """
        from clm.infrastructure.database.schema import init_database

        db_path = tmp_path / "jobs.db"
        init_database(db_path)

        workspace = tmp_path / "output"
        workspace.mkdir()

        topic_dir = tmp_path / "data" / "slides" / f"smoke_{case.prog_lang}"
        topic_dir.mkdir(parents=True)
        deck = topic_dir / f"smoke{suffix_for(case.prog_lang)}"
        deck.write_text(self._deck(case), encoding="utf-8")

        return {
            "db_path": db_path,
            "workspace": workspace,
            "data_dir": tmp_path / "data",
            "topic_dir": topic_dir,
            "deck": deck,
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

    @pytest.mark.parametrize("case", LANGUAGE_CASES, ids=lambda c: c.prog_lang)
    def test_deck_executes_and_html_carries_the_result(
        self, case: LanguageCase, tmp_path: Path
    ) -> None:
        """The rendered deck must contain the value the code computes.

        One worker (and container) per language rather than one shared worker for
        all of them: a kernel that hangs then fails its own case instead of
        taking the rest of the matrix with it, and the failure names the language.
        """
        from clm.infrastructure.database.job_queue import JobQueue
        from clm.infrastructure.workers.config_loader import load_worker_config
        from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

        image = find_notebook_image()
        if image is None:
            pytest.skip("No notebook Docker image available (run: clm docker build)")

        env = self._env(tmp_path, case)

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
            db_path=env["db_path"],
            workspace_path=env["workspace"],
            data_dir=env["data_dir"],
        )

        workers: list = []
        try:
            workers = manager.start_managed_workers()
            assert workers, "No workers started"
            self._await_workers(env["db_path"], len(workers))

            queue = JobQueue(env["db_path"])
            output_file = env["workspace"] / "public" / f"smoke_{case.prog_lang}.html"
            output_file.parent.mkdir(parents=True, exist_ok=True)

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(env["deck"]),
                output_file=str(output_file),
                content_hash=f"smoke-execution-{case.prog_lang}",
                payload={
                    "kind": "completed",
                    "prog_lang": case.prog_lang,
                    "language": "en",
                    "format": "html",
                    "source_topic_dir": str(env["topic_dir"]),
                },
            )

            # Wide budget: C++ compiles each cell, and the .NET and Java kernels
            # are slow to start. Normally each case finishes inside a minute.
            deadline = time.monotonic() + 300.0
            job = queue.get_job(job_id)
            while time.monotonic() < deadline and job.status not in ("completed", "failed"):
                time.sleep(1)
                job = queue.get_job(job_id)

            assert job.status == "completed", (
                f"{case.prog_lang} deck did not build: status {job.status!r}\nError: {job.error}"
            )
            assert output_file.is_file(), f"No output written to {output_file}"

            html = output_file.read_text(encoding="utf-8", errors="replace")
            assert case.marker in html, (
                f"The executed {case.prog_lang} deck does not contain "
                f"{case.marker!r}, so the kernel did not run the code (a cell "
                f"failure absorbed by skip-errors looks like this too — check the "
                f"worker log).\nOutput written to {output_file}"
            )
        finally:
            manager.stop_managed_workers(workers)
