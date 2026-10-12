"""Static checks on the Docker build context defined by ``.dockerignore``.

Every service Dockerfile runs ``COPY . ./clm``, so anything the context lets
through ends up in ``/app/clm`` of a published image. Issue #1084: the
gitignored agent scratch dir ``.scratch/`` (733 MB) was not dockerignored and
was baked into locally built images.

The matcher below follows BuildKit's ``.dockerignore`` semantics (moby
``patternmatcher``): patterns are anchored at the context root, ``*`` and
``?`` never cross ``/``, ``**`` spans any number of directories, a pattern
that matches a parent directory excludes everything below it, and the last
matching pattern wins (``!`` re-includes). It deliberately differs from
gitignore matching, where ``*.md`` would also hit ``src/**/*.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _load_rules() -> list[tuple[bool, re.Pattern[str]]]:
    rules = []
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:].strip() if negated else line
        pattern = pattern.strip("/")
        rules.append((negated, _pattern_to_regex(pattern)))
    return rules


RULES = _load_rules()


def is_excluded(path: str) -> bool:
    """Whether ``path`` (POSIX, relative to the context root) is left out."""
    parts = path.split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    excluded = False
    for negated, regex in RULES:
        if any(regex.match(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


LOCAL_STATE = [
    ".scratch/s8/src/clm/__init__.py",
    ".scratch/boot_probe.py",
    ".clm/voiceover/cache.json",
    ".clm-cache/entry",
    ".clm-test-corpus/deck.py",
    ".hypothesis/examples/abc",
    ".import_linter_cache/x",
    ".jupyterlite.doit.db",
    ".venv/Lib/site-packages/x.py",
    ".claude/worktrees/issue-1/pyproject.toml",
    ".mypy_cache/3.12/x.json",
    ".docker-cache/drawio/index.json",
    ".pytest_cache/v/cache",
    # A tool cache nobody has heard of yet: the root `.*` rule covers it.
    ".some-future-tool/state.db",
    "clm_jobs.db",
    "clm_cache.db-wal",
    "tests/test_x.py",
    "docs/index.md",
]


@pytest.mark.parametrize("path", LOCAL_STATE)
def test_local_state_is_not_in_the_build_context(path: str) -> None:
    assert is_excluded(path), f"{path} would be COPYed into /app/clm"


BUILD_INPUTS = [
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "uv.lock",
    "docker/drawio/entrypoint.sh",
    "docker/drawio/ArchitectsDaughter-Regular.ttf",
    "docker/plantuml/plantuml-1.2024.6.jar",
]


@pytest.mark.parametrize("path", BUILD_INPUTS)
def test_build_inputs_are_in_the_build_context(path: str) -> None:
    assert (REPO_ROOT / path).exists(), f"stale fixture: {path} is gone"
    assert not is_excluded(path), f"{path} is dockerignored but the build needs it"


def test_the_whole_package_source_is_in_the_build_context() -> None:
    # The images `pip install ./clm` and COPY src/clm/data/... from the
    # context, so no packaged file may be filtered out (e.g. by a root-only
    # pattern accidentally written as `**/...`).
    sources = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src" / "clm").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    assert len(sources) > 100
    excluded = [path for path in sources if is_excluded(path)]
    assert excluded == []


def test_matcher_follows_root_anchored_docker_semantics() -> None:
    # `*.md` is root-only in .dockerignore; the info topics must still ship.
    assert is_excluded("CHANGELOG.md")
    assert not is_excluded("src/clm/cli/info_topics/commands.md")
    # `.*` is root-only too: dot files inside the package are kept.
    assert not is_excluded("src/clm/recordings/web/static/.gitkeep")
    # `**/build` is re-included for the build engine package only.
    assert is_excluded("build/lib/x.py")
    assert not is_excluded("src/clm/build/__init__.py")
