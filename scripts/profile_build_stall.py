"""Reproduce and measure the ``clm build`` progress-bar / event-loop behavior.

Generates a throwaway SYNTHETIC course (many trivial topics) in a temp dir and
runs ``clm build`` against it with ``CLM_PROFILE_BUILD=1`` so the build emits the
``[build-profile]`` summary from :mod:`clm.infrastructure.build_profiling`
(poll-loop inter-iteration gaps, on-loop submission cost, payload-build cost).

It is a guard against performance regressions in the build's submission /
completion-poll machinery — in particular the "progress bar freezes for a long
time at ~30 jobs while ``clm monitor`` shows hundreds finishing, then jumps"
stall caused by synchronous job submission starving the completion poll loop.

Safety:
* uses ISOLATED temp cache/jobs DBs and a temp output dir, so it never touches a
  real course's databases or output;
* ``--no-html`` produces notebook/code worker jobs via jupytext WITHOUT running
  any Jupyter kernel, so the run is fast but still exercises the real submission
  + poll loop with real worker subprocesses.

Usage (from the repo root, in the project venv)::

    python scripts/profile_build_stall.py --topics 60 --siblings 3 --sibling-kb 30

Read the ``worst stall`` and ``max gap`` lines: a healthy build keeps the gap
near the poll interval (~0.5 s). A multi-second ``worst stall`` is the freeze.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SLIDE_TEMPLATE = """\
# j2 from 'macros.j2' import header
# {{{{ header("Synthetisches Thema {k}", "Synthetic Topic {k}") }}}}

# %% [markdown]
# ## Synthetic Topic {k}
#
# Generated content so the payload builder has something to read, base64-encode
# (siblings) and hash.
{filler}

# %%
def compute_{k}(n):
    total = 0
    for i in range(n):
        total += i * {k}
    return total


print(compute_{k}(10))

# %%
print("synthetic slide {k} done")
"""


def make_course(root: Path, topics: int, siblings: int, sibling_kb: int) -> Path:
    """Create a synthetic course under ``root`` and return the spec path."""
    slides = root / "data" / "slides" / "module_000_synth"
    slides.mkdir(parents=True, exist_ok=True)

    filler = "\n".join(f"# context line {i} for the synthetic deck" for i in range(20))
    sibling_blob = ("synthetic sibling payload data; " * 64 + "\n") * max(
        1, (sibling_kb * 1024) // 2048
    )

    topic_ids: list[str] = []
    for k in range(1, topics + 1):
        topic_id = f"synth_{k:03d}"
        topic_ids.append(topic_id)
        if siblings > 0:
            tdir = slides / f"topic_{k:03d}_{topic_id}"
            (tdir / "data").mkdir(parents=True, exist_ok=True)
            (tdir / f"slides_{topic_id}.py").write_text(
                SLIDE_TEMPLATE.format(k=k, filler=filler), encoding="utf-8"
            )
            for s in range(siblings):
                (tdir / "data" / f"sibling_{s}.txt").write_text(sibling_blob, encoding="utf-8")
        else:
            (slides / f"topic_{k:03d}_{topic_id}.py").write_text(
                SLIDE_TEMPLATE.format(k=k, filler=filler), encoding="utf-8"
            )

    per_section = max(1, topics // 4)
    sections_xml = []
    for s_idx, start in enumerate(range(0, topics, per_section), start=1):
        chunk = topic_ids[start : start + per_section]
        topics_xml = "\n".join(f"                <topic>{tid}</topic>" for tid in chunk)
        sections_xml.append(
            f"""        <section>
            <name>
                <de>Woche {s_idx}</de>
                <en>Week {s_idx}</en>
            </name>
            <topics>
{topics_xml}
            </topics>
        </section>"""
        )

    spec = root / "course.xml"
    spec.write_text(
        f"""<course>
    <name>
        <de>Synthetischer Lastkurs</de>
        <en>Synthetic Load Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Lasttest</de>
        <en>Load test</en>
    </description>
    <sections>
{chr(10).join(sections_xml)}
    </sections>
</course>
""",
        encoding="utf-8",
    )
    return spec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topics", type=int, default=60, help="number of synthetic topics")
    ap.add_argument(
        "--siblings",
        type=int,
        default=3,
        help="sibling files per topic (0 = single-file topics; >0 exercises the payload base64 cost)",
    )
    ap.add_argument("--sibling-kb", type=int, default=30, help="size of each sibling file in KiB")
    ap.add_argument("--keep", action="store_true", help="keep the temp dir for inspection")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="clm_profile_build_"))
    spec = make_course(workdir, args.topics, args.siblings, args.sibling_kb)

    env = dict(os.environ)
    env["CLM_PROFILE_BUILD"] = "1"

    cmd = [
        sys.executable,
        "-m",
        "clm",
        "--cache-db-path",
        str(workdir / "cache.db"),  # isolated; never the real cache
        "--jobs-db-path",
        str(workdir / "jobs.db"),
        "build",
        str(spec),
        "--data-dir",
        str(workdir / "data"),
        "--output-dir",
        str(workdir / "out"),
        "--no-html",  # notebook/code jobs via jupytext, no Jupyter kernel
        "--no-diagrams",
        "--no-progress",  # measure via the profiler, not the live bar
        "--log-level",
        "WARNING",
    ]
    print(
        f"[profile] topics={args.topics} siblings={args.siblings} "
        f"sibling_kb={args.sibling_kb}\n[profile] workdir={workdir}",
        flush=True,
    )

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    wall = time.perf_counter() - t0

    prof_lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if "[build-profile]" in ln]
    print(f"\n[profile] build wall time: {wall:.1f}s, exit={proc.returncode}")
    if prof_lines:
        for ln in prof_lines:
            print("   " + ln.split("] ", 1)[-1] if "] " in ln else ln)
    else:
        print("   (no [build-profile] lines captured — last 25 stderr lines:)")
        for ln in proc.stderr.splitlines()[-25:]:
            print("   ! " + ln)

    if args.keep:
        print(f"[profile] kept {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
