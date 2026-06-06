"""Verify the header-line-less invariant across a //-family course corpus.

For every bilingual deck in a course `slides/` directory this:
  1. reformats it with ``reformat_header_convention.classify_and_reformat``,
  2. renders it through CLM's exact jinja settings with the CURRENT
     ``templates_<lang>/macros.j2``, and
  3. asserts the build invariant: the deck parses to >0 cells and yields
     **exactly one title slide per language** (de and en) — i.e. the
     neutral-wrapper bug (a German title leaking into the EN build) is gone and
     the header macro supplies both language boundaries.

This is the corpus-wide form of the spot-check in the investigation doc §10.5.
Run it before/after applying ``reformat_header_convention.py --apply`` to a repo.

Usage:  python scripts/verify_header_reformat.py {cpp|csharp|java|typescript|all} [limit]
        (course slide roots are configured in COURSES below)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import jupytext
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reformat_header_convention import classify_and_reformat  # noqa: E402

from clm.workers.notebook.utils.jupyter_utils import is_cell_included_for_language  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src/clm/workers/notebook"
EXCLUDE_DIRS = {"_archive", "_deleted", "_old", "Old", ".ipynb_checkpoints", "conversion", "backup"}

COURSES = {
    "cpp": {
        "slides": Path(r"C:/Users/tc/Programming/Cpp/CppCourses/slides"),
        "ext": "cpp",
        "fmt": "cpp:percent",
    },
    "csharp": {
        "slides": Path(r"C:/Users/tc/Programming/CSharp/CSharpCourses/slides"),
        "ext": "cs",
        "fmt": "cs:percent",
    },
    "java": {
        "slides": Path(r"C:/Users/tc/Programming/Java/Courses/JavaCourses/slides"),
        "ext": "java",
        "fmt": "java:percent",
    },
    "typescript": {
        "slides": Path(r"C:/Users/tc/Programming/TypeScript/Courses/TypeScriptCourses/slides"),
        "ext": "ts",
        "fmt": "ts:percent",
    },
}
GLOBALS = {"is_notebook": False, "is_html": True, "lang": "de", "author": "A", "organization": ""}
_TITLE_RE = re.compile(r"<b>(.*?)</b>", re.DOTALL)


def _title_count(nb, lang: str) -> int:
    return sum(
        1
        for c in nb.cells
        if is_cell_included_for_language(c, lang)
        and "font-size:200%" in c.get("source", "")
        and "<b>" in c.get("source", "")
    )


def run_lang(lang: str, limit: int) -> int:
    cfg = COURSES[lang]
    slides: Path = cfg["slides"]
    if not slides.exists():
        print(f"=== {lang} ===\n  SKIP: no course dir {slides}")
        return 0
    template_dir = TEMPLATES / f"templates_{lang}"
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(str(template_dir))]),
        autoescape=False,
        undefined=StrictUndefined,
        line_statement_prefix="// j2",
        keep_trailing_newline=True,
    )
    decks = sorted(
        p
        for p in slides.rglob(f"*.{cfg['ext']}")
        if not (EXCLUDE_DIRS & set(p.parts))
        and "import header" in p.read_text(encoding="utf-8", errors="replace")
    )
    if limit:
        decks = decks[:limit]

    checked = outliers = violations = 0
    notes: list[str] = []
    for deck in decks:
        text = deck.read_text(encoding="utf-8")
        new_text, status = classify_and_reformat(text)
        if status.startswith("outlier"):
            outliers += 1
            notes.append(f"OUTLIER {deck.name}: {status}")
            continue
        # render with the deck's own dir on the path for course-local includes
        env.loader = ChoiceLoader(
            [FileSystemLoader(str(template_dir)), FileSystemLoader(str(deck.parent))]
        )
        try:
            nb = jupytext.reads(env.from_string(new_text, globals=GLOBALS).render(), fmt=cfg["fmt"])
        except Exception as e:  # noqa: BLE001
            violations += 1
            notes.append(f"RENDER-ERR {deck.name}: {type(e).__name__}: {e}")
            continue
        checked += 1
        if len(nb.cells) == 0:
            violations += 1
            notes.append(f"EMPTY {deck.name}: parsed to 0 cells")
            continue
        de, en = _title_count(nb, "de"), _title_count(nb, "en")
        if de != 1 or en != 1:
            violations += 1
            notes.append(f"TITLES {deck.name}: de={de} en={en} (want 1/1)")

    print(f"=== {lang} ===")
    print(
        f"  decks: {len(decks)}  checked: {checked}  outliers: {outliers}  violations: {violations}"
    )
    for n in notes[:30]:
        print(f"    {n}")
    return 1 if violations else 0


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    langs = list(COURSES) if which == "all" else [which]
    rc = 0
    for lang in langs:
        rc |= run_lang(lang, limit)
    print(
        "\n"
        + (
            "ALL OK — exactly one title per language everywhere."
            if rc == 0
            else "VIOLATIONS found (see above)."
        )
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
