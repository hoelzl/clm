"""Coverage-matrix probes for the sync engine (2026-06-09 architecture review).

Evidence harness for ``docs/claude/sync-engine-architecture-assessment.md`` §2.1.
Each probe sets up a baseline (git-HEAD or watermark), applies a one-sided edit,
runs build_sync_plan + apply_plan (StaticSlideTranslator, judge=None; no API key),
and reports:

  PROPAGATED  - the change reached the other half
  ALERTED     - errors/deferred/issue-error raised, watermark held
  SILENT-DROP - plan.is_noop, no alert, change not propagated (the forbidden state)

As of master ``fab89615``, P1 / P5 / P9 are the three live SILENT-DROP cells (all
tag-channel); promote each into ``tests/slides/`` as an expected-alert regression
test once the channel-generic tag-parity fail-safe (assessment §5 P0) lands.

Run: ``uv run python scripts/sync_matrix_probes.py``
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.slides.sync_apply import _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator


def _title(lang, sid="title", txt="T"):
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _slide(lang, sid, txt):
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _ncode(body, tags='["keep"]'):
    return f"# %% tags={tags}\n{body}\n"


def _idless_code(lang, body, tags=None):
    t = f" tags={tags}" if tags else ""
    return f'# %% lang="{lang}"{t}\n{body}\n'


def _idd_code(lang, sid, body):
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _deck(*parts):
    return "\n".join(parts)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _sync(tmp, baseline, de0, en0, de1, en1, mapping=None):
    db = tmp / "clm-llm.sqlite"
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de0, encoding="utf-8")
    en_path.write_text(en0, encoding="utf-8")
    if baseline == "git-head":
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "t@example.com")
        _git(tmp, "config", "user.name", "Test")
        _git(tmp, "add", "-A")
        _git(tmp, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    else:
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        wm.close()
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    translator = StaticSlideTranslator(mapping=mapping or {}, default="<<XL>>")
    wm = SyncWatermarkCache(db)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=wm)
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


def _alerted(plan, result):
    return (
        plan.has_errors
        or result.has_errors
        or result.deferred > 0
        or any(i.severity == "error" for i in plan.issues)
    )


def verdict(name, plan, result, propagated, extra=""):
    if propagated:
        v = "PROPAGATED"
    elif _alerted(plan, result):
        v = "ALERTED"
    elif plan.is_noop:
        v = "SILENT-DROP"
    else:
        v = f"OTHER(noop={plan.is_noop}, wm={result.watermark_recorded})"
    wm = "wm-advanced" if result.watermark_recorded else "wm-held"
    print(f"{name:55s} -> {v:12s} [{wm}] {extra}")
    print(f"    summary: {plan.summary()}")
    if result.errors:
        print(f"    errors: {result.errors[:2]}")
    return v


def run(name, fn):
    with tempfile.TemporaryDirectory() as td:
        fn(name, Path(td))


# --- P1: tag-only edit x id-less localized x GIT-HEAD baseline ---------------
def p1(name, tmp):
    de = _deck(_title("de"), _idless_code("de", 'print("hallo")'))
    en = _deck(_title("en"), _idless_code("en", 'print("hello")'))
    # EN adds tags=["keep"] to the id-less localized cell (tag-only; body unchanged)
    en1 = _deck(_title("en"), _idless_code("en", 'print("hello")', tags='["keep"]'))
    plan, result, de_after, _ = _sync(tmp, "git-head", de, en, de, en1)
    propagated = 'tags=["keep"]' in de_after.split("\n")[-3]  # tag mirrored onto DE cell?
    propagated = '# %% lang="de" tags=["keep"]' in de_after
    verdict(name, plan, result, propagated)


# --- P1b: same, WATERMARK baseline (control: Tier C should mirror it) --------
def p1b(name, tmp):
    de = _deck(_title("de"), _idless_code("de", 'print("hallo")'))
    en = _deck(_title("en"), _idless_code("en", 'print("hello")'))
    en1 = _deck(_title("en"), _idless_code("en", 'print("hello")', tags='["keep"]'))
    plan, result, de_after, _ = _sync(tmp, "watermark", de, en, de, en1)
    propagated = '# %% lang="de" tags=["keep"]' in de_after
    verdict(name, plan, result, propagated)


# --- P2: tag-only edit x id'd localized code x GIT-HEAD baseline -------------
def p2(name, tmp):
    de = _deck(_title("de"), _idd_code("de", "c1", "x = 1"))
    en = _deck(_title("en"), _idd_code("en", "c1", "x = 1"))
    en1 = _deck(_title("en"), '# %% lang="en" tags=["keep", "alt"] slide_id="c1"\nx = 1\n')
    plan, result, de_after, _ = _sync(tmp, "git-head", de, en, de, en1)
    propagated = '"alt"' in de_after
    verdict(name, plan, result, propagated)


# --- P3: one-sided REMOVE of an id-less localized cell (both baselines) ------
def p3(name, tmp, baseline="watermark"):
    de = _deck(_title("de"), _idless_code("de", 'print("hallo")'), _ncode("import os"))
    en = _deck(_title("en"), _idless_code("en", 'print("hello")'), _ncode("import os"))
    en1 = _deck(_title("en"), _ncode("import os"))  # EN deletes its id-less localized cell
    plan, result, de_after, _ = _sync(tmp, baseline, de, en, de, en1)
    propagated = "hallo" not in de_after
    verdict(f"{name}[{baseline}]", plan, result, propagated)


# --- P4: one-sided INTRA-GROUP reorder of two distinct neutral cells ---------
def p4(name, tmp, baseline="watermark"):
    de = _deck(_title("de"), _ncode("import os"), _ncode("import sys"))
    en = _deck(_title("en"), _ncode("import os"), _ncode("import sys"))
    en1 = _deck(_title("en"), _ncode("import sys"), _ncode("import os"))  # EN swaps them
    plan, result, de_after, _ = _sync(tmp, baseline, de, en, de, en1)
    body = de_after
    propagated = body.find("import sys") < body.find("import os")
    verdict(f"{name}[{baseline}]", plan, result, propagated)


# --- P5: #285 open residual — id-less tag-only retag under a concurrent move -
def p5(name, tmp):
    de0 = _deck(
        _slide("de", "a", "A"), _idless_code("de", 'print("hallo")'), _slide("de", "b", "B")
    )
    en0 = _deck(
        _slide("en", "a", "A"), _idless_code("en", 'print("hello")'), _slide("en", "b", "B")
    )
    # DE: tag-only edit on its id-less cell. EN: reorders groups (b before a).
    de1 = _deck(
        _slide("de", "a", "A"),
        _idless_code("de", 'print("hallo")', tags='["keep"]'),
        _slide("de", "b", "B"),
    )
    en1 = _deck(
        _slide("en", "b", "B"), _slide("en", "a", "A"), _idless_code("en", 'print("hello")')
    )
    plan, result, de_after, en_after = _sync(tmp, "watermark", de0, en0, de1, en1)
    propagated = '# %% lang="en" tags=["keep"]' in en_after
    verdict(name, plan, result, propagated, extra=f"de keep-tag kept: {'keep' in de_after}")


# --- P6: one-sided id-less localized ADD x git-head ---------------------------
def p6(name, tmp, baseline="git-head"):
    de = _deck(_title("de"), _ncode("import os"))
    en = _deck(_title("en"), _ncode("import os"))
    en1 = _deck(_title("en"), _ncode("import os"), _idless_code("en", 'print("new")'))
    plan, result, de_after, _ = _sync(
        tmp, baseline, de, en, de, en1, mapping={'print("new")': 'print("neu")'}
    )
    propagated = "neu" in de_after
    verdict(f"{name}[{baseline}]", plan, result, propagated)


# --- P7: reconfirm #282 — group reorder EN + neutral edit DE ------------------
def p7(name, tmp):
    de0 = _deck(_slide("de", "a", "A"), _ncode("import os"), _slide("de", "b", "B"))
    en0 = _deck(_slide("en", "a", "A"), _ncode("import os"), _slide("en", "b", "B"))
    de1 = _deck(_slide("de", "a", "A"), _ncode("import os  # EDIT"), _slide("de", "b", "B"))
    en1 = _deck(_slide("en", "b", "B"), _slide("en", "a", "A"), _ncode("import os"))
    plan, result, de_after, en_after = _sync(tmp, "watermark", de0, en0, de1, en1)
    propagated = "EDIT" in en_after
    verdict(name, plan, result, propagated, extra=f"de edit intact: {'EDIT' in de_after}")


# --- P8: reconfirm #216 — cold-start both-id-less pair does not double -------
def p8(name, tmp):
    de = _deck(
        '# %% [markdown] lang="de" tags=["slide"]\n# # Titel\n',
        _idless_code("de", 'print("hallo")'),
    )
    en = _deck(
        '# %% [markdown] lang="en" tags=["slide"]\n# # Title\n',
        _idless_code("en", 'print("hello")'),
    )
    plan, result, de_after, en_after = _sync(tmp, "git-head", de, en, de, en)
    doubled = de_after.count("# %%") > de.count("# %%") or en_after.count("# %%") > en.count(
        "# %%"
    )
    propagated = False
    print(f"    doubled: {doubled}")
    verdict(name, plan, result, propagated, extra=f"doubled={doubled}")


# --- P9: one-sided TAG-ONLY edit on a language-neutral shared cell ------------
def p9(name, tmp, baseline="watermark"):
    de = _deck(_title("de"), _ncode("import os"))
    en = _deck(_title("en"), _ncode("import os"))
    en1 = _deck(_title("en"), _ncode("import os", tags='["keep", "alt"]'))
    plan, result, de_after, _ = _sync(tmp, baseline, de, en, de, en1)
    propagated = '"alt"' in de_after
    verdict(f"{name}[{baseline}]", plan, result, propagated)


if __name__ == "__main__":
    run("P1 idless-localized retag x git-head", p1)
    run("P1b idless-localized retag x watermark (control)", p1b)
    run("P2 id'd code retag x git-head", p2)
    run("P3 idless-localized remove", lambda n, t: p3(n, t, "watermark"))
    run("P3 idless-localized remove", lambda n, t: p3(n, t, "git-head"))
    run("P4 intra-group neutral reorder", lambda n, t: p4(n, t, "watermark"))
    run("P4 intra-group neutral reorder", lambda n, t: p4(n, t, "git-head"))
    run("P5 #285 idless retag under move", p5)
    run("P6 idless-localized add", p6)
    run("P7 #282 reorder-vs-neutral-edit", p7)
    run("P8 #216 cold-start no doubling", p8)
    run("P9 neutral-cell tag-only edit", lambda n, t: p9(n, t, "watermark"))
    run("P9 neutral-cell tag-only edit", lambda n, t: p9(n, t, "git-head"))
