"""Fetch the pinned public test corpus (#682) into ``.clm-test-corpus/``.

The corpus gates in ``tests/slides/test_public_corpus.py`` run against the
`ClmTestCourse <https://github.com/hoelzl/ClmTestCourse>`_ checkout this
script produces — always at the pin recorded in
``tests/slides/public_corpus_pin.py``, so the gates measure a fixed corpus
and a failure is attributable to CLM. Idempotent: an up-to-date checkout is
left alone; a stale one is fetched forward and hard-reset to the pin (the
directory is disposable — it is gitignored and never hand-edited).

Usage::

    python scripts/fetch_test_corpus.py          # into <repo>/.clm-test-corpus
    python scripts/fetch_test_corpus.py DIR      # elsewhere (CI caches, etc.)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / ".clm-test-corpus"

# Read (not import) the pin module: one source of truth, no sys.path games.
_PIN_FILE = REPO_ROOT / "tests" / "slides" / "public_corpus_pin.py"
_pin_text = _PIN_FILE.read_text(encoding="utf-8")


def _pinned(name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"', _pin_text, re.MULTILINE)
    assert match is not None, f"{name} not found in {_PIN_FILE}"
    return match.group(1)


PUBLIC_CORPUS_REPO = _pinned("PUBLIC_CORPUS_REPO")
PUBLIC_CORPUS_PIN = _pinned("PUBLIC_CORPUS_PIN")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False
    )


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    if not (target / ".git").exists():
        target.mkdir(parents=True, exist_ok=True)
        for step in (
            ("init", "-q"),
            ("remote", "add", "origin", PUBLIC_CORPUS_REPO),
        ):
            result = _git(target, *step)
            if result.returncode != 0:
                print(result.stderr.strip(), file=sys.stderr)
                return 1
    head = _git(target, "rev-parse", "HEAD")
    if head.returncode == 0 and head.stdout.strip() == PUBLIC_CORPUS_PIN:
        print(f"corpus already at pin {PUBLIC_CORPUS_PIN[:12]} -> {target}")
        return 0
    fetch = _git(target, "fetch", "--depth", "1", "origin", PUBLIC_CORPUS_PIN)
    if fetch.returncode != 0:
        print(fetch.stderr.strip(), file=sys.stderr)
        return 1
    reset = _git(target, "reset", "--hard", PUBLIC_CORPUS_PIN)
    if reset.returncode != 0:
        print(reset.stderr.strip(), file=sys.stderr)
        return 1
    print(f"corpus fetched at pin {PUBLIC_CORPUS_PIN[:12]} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
