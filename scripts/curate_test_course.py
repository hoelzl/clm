"""Curate the public test-course corpus from the private course repos (#682).

The CLM regression gates need a *pinned, CI-runnable* corpus; the maintainer's
decision (2026-08-04, recorded on #682) is a curated public test-course repo,
shared with #681. This script is the regeneration path: selection is a
committed manifest, sanitization is deterministic and structure-preserving,
and verification proves the staged decks measure the same way the originals
do — the Option C validation from the issue.

Three subcommands::

    python scripts/curate_test_course.py analyze SRC_DIR...
        Per-pair stats over one or more slides trees (refusal codes, member
        counts, positional-pool sizes, companion layout, kind/langness mix)
        plus per-shape suggestions — the measured basis for the manifest.

    python scripts/curate_test_course.py stage MANIFEST OUT --src NAME=DIR...
        Copy + sanitize the manifest's decks into OUT (a course-repo-shaped
        tree). Only deck halves and their companions are staged — this corpus
        is parse/diff-grade, not build-grade (no images, no data files; the
        #681 buildable cassette topic is authored separately, not derived).

    python scripts/curate_test_course.py verify MANIFEST OUT --src NAME=DIR...
        Structural parity, original vs staged, per deck: refusal-code sets,
        member key sequences, per-member (langness, layout, kind, role,
        owner), observation-kind multisets, and the shared/equal byte-parity
        relations the differ keys on. Any drift is a lossy sanitization in
        exactly the dimension the gates measure.

Sanitization rules (deterministic — same input line, same output line, which
is what preserves every byte-equality relation between DE/EN halves):

* cell header lines (``# %%`` / ``// %%``) are kept verbatim — ids, tags,
  ``lang=``, ``for_slide``, ``vo_anchor`` are the structure under test;
* j2 template lines keep their machinery, but quoted string arguments are
  replaced (deck titles are course content);
* markdown body lines keep their comment prefix and leading markdown markers
  (heading level, list/quote markers); the prose is replaced by filler words
  drawn per-language (``lang="de"`` cells German-looking, ``en`` English,
  shared/neutral cells language-neutral tokens — so the #772 German-text
  detector and ``content_lang`` see the same *kind* of content);
* code-cell code lines are kept verbatim; full-line comments are replaced by
  filler. Inline trailing comments and string literals are kept (flagged for
  review when they contain non-ASCII), because rewriting inside code risks
  changing what the corpus measures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from clm.slides.doc_identity import iter_with_groups  # noqa: E402
from clm.slides.doc_lenses import load_bundle  # noqa: E402
from clm.slides.pairing import (  # noqa: E402
    find_split_slide_files_recursive,
    iter_split_pairs,
)

_DE_WORDS = (
    "beispiel daten wert funktion klasse modul liste zahl text struktur "
    "aufgabe hinweis abschnitt inhalt thema schritt muster regel fall ansatz"
).split()
_EN_WORDS = (
    "example data value function class module list number text structure "
    "task hint section content topic step pattern rule case approach"
).split()
_NEUTRAL_WORDS = "alpha beta gamma delta omega token item node unit block".split()

_HEADER_RE = re.compile(r"^\s*(#|//)\s*%%")
_J2_RE = re.compile(r"^\s*(#|//)\s*(j2\b|\{\{)")
_QUOTED_RE = re.compile(r'"([^"]*)"')
# Comment prefix + leading markdown structure that must survive sanitization.
_MD_MARKERS_RE = re.compile(r"^(?P<prefix>\s*(?:#|//)\s?)(?P<markers>(?:#{1,6} |[-*>] |\d+\. )*)")


def _filler(seed_text: str, words: list[str], approx_len: int) -> str:
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    picked: list[str] = []
    i = 0
    while sum(len(w) + 1 for w in picked) < max(approx_len, 4) and i < 24:
        picked.append(words[digest[i % len(digest)] % len(words)])
        i += 1
    return " ".join(picked) or words[digest[0] % len(words)]


def _words_for(lang: str | None) -> list[str]:
    if lang == "de":
        return _DE_WORDS
    if lang == "en":
        return _EN_WORDS
    return _NEUTRAL_WORDS


def _sanitize_j2_line(line: str) -> str:
    lang = "de" if "_de" in line else ("en" if "_en" in line else None)

    def repl(match: re.Match) -> str:
        original = str(match.group(1))
        if not original.strip():
            return str(match.group(0))
        return '"' + _filler(original, _words_for(lang), len(original)).title() + '"'

    return _QUOTED_RE.sub(repl, line)


def _cell_lang(header_line: str) -> str | None:
    match = re.search(r'lang="(de|en)"', header_line)
    return match.group(1) if match else None


def sanitize_deck_text(text: str, comment_token: str) -> str:
    """The deterministic, structure-preserving prose replacement."""
    out: list[str] = []
    in_markdown = False
    cell_lang: str | None = None
    for line in text.splitlines():
        if _HEADER_RE.match(line):
            in_markdown = "[markdown]" in line
            cell_lang = _cell_lang(line)
            out.append(line)
            continue
        if not line.strip():
            out.append(line)
            continue
        if _J2_RE.match(line):
            out.append(_sanitize_j2_line(line))
            continue
        stripped = line.lstrip()
        is_comment = stripped.startswith(comment_token)
        if not in_markdown and not is_comment:
            out.append(line)  # code stays code
            continue
        if not in_markdown and is_comment:
            # full-line comment in a code cell
            prefix = line[: len(line) - len(stripped)] + comment_token + " "
            body = stripped[len(comment_token) :].strip()
            if not body:
                out.append(line)
                continue
            out.append(prefix + _filler(body, _words_for(cell_lang), len(body)))
            continue
        # markdown body line
        match = _MD_MARKERS_RE.match(line)
        assert match is not None
        prefix = match.group("prefix") + match.group("markers")
        body = line[match.end() :]
        if not body.strip():
            out.append(line)
            continue
        filler = _filler(body, _words_for(cell_lang), len(body))
        if match.group("markers").startswith("#"):
            filler = filler.title()
        out.append(prefix + filler)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def _pair_stats(de_path: Path) -> dict:
    bundle = load_bundle(de_path)
    stats: dict = {"deck": de_path.name, "topic": str(de_path.parent)}
    if bundle.outcome.refusal is not None:
        stats["refusal_codes"] = sorted({r.code for r in bundle.outcome.refusal.reasons})
        return stats
    deck = bundle.outcome.deck
    assert deck is not None
    members = list(iter_with_groups(deck))
    pools: Counter = Counter()
    kinds: Counter = Counter()
    langness: Counter = Counter()
    for member, _group in members:
        kinds[member.kind] += 1
        langness[member.langness] += 1
        if member.key.scheme == "pos":
            pools[member.key.value.rsplit("/", 1)[0]] += 1
    stats.update(
        n_members=len(members),
        max_pool=max(pools.values(), default=0),
        n_pos=sum(pools.values()),
        kinds=dict(kinds),
        langness=dict(langness),
        observations=sorted({o.kind for o in deck.observations}),
        companion=(
            "subdir"
            if bundle.de_companion_path and bundle.de_companion_path.parent.name == "voiceover"
            else ("sibling" if bundle.de_companion_path else "none")
        ),
        token=bundle.comment_token,
    )
    return stats


def cmd_analyze(src_dirs: list[Path]) -> int:
    for src in src_dirs:
        pairs, _solos = iter_split_pairs(find_split_slide_files_recursive(src))
        rows = [_pair_stats(de) for de, _en in pairs]
        parsed = [r for r in rows if "refusal_codes" not in r]
        refused = [r for r in rows if "refusal_codes" in r]
        print(f"\n=== {src}  ({len(parsed)} parsed / {len(refused)} refused)")
        by_code: dict[str, list[str]] = {}
        for r in refused:
            for code in r["refusal_codes"]:
                by_code.setdefault(code, []).append(r["topic"] + "/" + r["deck"])
        for code, decks in sorted(by_code.items()):
            print(f"  refusal {code}: {len(decks)} deck(s); e.g. {decks[0]}")
        for title, key in (
            ("largest positional pool", lambda r: r["max_pool"]),
            ("most members", lambda r: r["n_members"]),
            ("most localized", lambda r: r["langness"].get("localized", 0)),
            ("most shared code", lambda r: r["kinds"].get("code", 0)),
        ):
            top = sorted(parsed, key=key, reverse=True)[:3]
            print(f"  {title}:")
            for r in top:
                print(
                    f"    {key(r):4d}  {r['topic']}\\{r['deck']}"
                    f"  (members={r['n_members']} pool={r['max_pool']} "
                    f"companion={r['companion']} obs={r['observations']})"
                )
        with_obs = [r for r in parsed if r["observations"]]
        print(f"  decks with parse observations: {len(with_obs)}")
        for r in with_obs[:6]:
            print(f"    {r['observations']}  {r['topic']}\\{r['deck']}")
        for layout in ("subdir", "sibling"):
            sample = [r for r in parsed if r["companion"] == layout]
            print(f"  companion={layout}: {len(sample)} deck(s)")
            for r in sorted(sample, key=lambda r: r["n_members"], reverse=True)[:3]:
                print(f"    members={r['n_members']:3d}  {r['topic']}\\{r['deck']}")
    return 0


# ---------------------------------------------------------------------------
# synthetic decks — shapes the live corpora no longer carry
# ---------------------------------------------------------------------------

# The normalize cleanups drove four of the five refusal codes (and the sibling
# companion layout) out of the real repos, but the gates still measure them,
# so the public corpus carries hand-authored minimal decks — no sanitization,
# no provenance, authored for exactly one shape each.

_HDR_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel\") }}\n\n"
_HDR_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title\") }}\n\n"


def _slide(lang: str, slug: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


_SYNTHETIC_DECKS: dict[str, dict[str, str]] = {
    "duplicate_id": {
        "slides_duplicate_id.de.py": _HDR_DE
        + _slide("de", "s0", "Titel")
        + _slide("de", "s0", "Nochmal"),
        "slides_duplicate_id.en.py": _HDR_EN
        + _slide("en", "s0", "Title")
        + _slide("en", "s0", "Again"),
    },
    "idless_anchor": {
        "slides_idless_anchor.de.py": _HDR_DE
        + '# %% [markdown] lang="de" tags=["slide"]\n#\n# # Kein Bezeichner\n',
        "slides_idless_anchor.en.py": _HDR_EN
        + '# %% [markdown] lang="en" tags=["slide"]\n#\n# # No identifier\n',
    },
    "idless_narrative": {
        # The canonical blank `#` lead keeps validate quiet here — this deck
        # exists for exactly ONE finding class (the id-less narrative), and a
        # cosmetic co-finding would blur what a gate failure on it means.
        "slides_idless_narrative.de.py": _HDR_DE
        + _slide("de", "s0", "Titel")
        + '# %% [markdown] lang="de" tags=["notes"]\n#\n# Notiz ohne Bezeichner.\n',
        "slides_idless_narrative.en.py": _HDR_EN
        + _slide("en", "s0", "Title")
        + '# %% [markdown] lang="en" tags=["notes"]\n#\n# Note without identifier.\n',
    },
    "legacy_title_companion": {
        "slides_legacy_title.de.py": _HDR_DE + _slide("de", "s0", "Titel"),
        "slides_legacy_title.en.py": _HDR_EN + _slide("en", "s0", "Title"),
        "voiceover_legacy_title.de.py": (
            '# %% [markdown] lang="de" tags=["notes"] slide_id="title"\n#\n# - Erzählung.\n'
        ),
        "voiceover_legacy_title.en.py": (
            '# %% [markdown] lang="en" tags=["notes"] slide_id="title"\n#\n# - Narration.\n'
        ),
    },
    "companion_sibling": {
        "slides_sibling_layout.de.py": _HDR_DE + _slide("de", "s0", "Titel"),
        "slides_sibling_layout.en.py": _HDR_EN + _slide("en", "s0", "Title"),
        "voiceover_sibling_layout.de.py": (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="s0" slide_id="s0-vo"\n'
            "# Beispiel Erzählung für die Folie.\n"
        ),
        "voiceover_sibling_layout.en.py": (
            '# %% [markdown] lang="en" tags=["voiceover"] for_slide="s0" slide_id="s0-vo"\n'
            "# Example narration for the slide.\n"
        ),
    },
}


# ---------------------------------------------------------------------------
# stage / verify
# ---------------------------------------------------------------------------


def _load_manifest(path: Path) -> dict:
    manifest: dict = json.loads(path.read_text(encoding="utf-8"))
    return manifest


def _resolve_sources(pairs: list[str]) -> dict[str, Path]:
    sources = {}
    for item in pairs:
        name, _, value = item.partition("=")
        sources[name] = Path(value)
    return sources


def _deck_files(topic_dir: Path, deck_stem: str) -> list[Path]:
    """The ≤4 source files of one deck: halves + companions (either layout)."""
    files = sorted(topic_dir.glob(f"{deck_stem}.??.*"))
    companion_stem = "voiceover_" + deck_stem.split("_", 1)[1]
    files += sorted(topic_dir.glob(f"{companion_stem}.??.*"))
    files += sorted((topic_dir / "voiceover").glob(f"{companion_stem}.??.*"))
    return [f for f in files if f.is_file()]


def _comment_token_for(path: Path) -> str:
    return "//" if path.suffix in {".cpp", ".cs", ".java", ".ts", ".rs"} else "#"


def cmd_stage(manifest_path: Path, out: Path, source_pairs: list[str]) -> int:
    manifest = _load_manifest(manifest_path)
    sources = _resolve_sources(source_pairs)
    flags: list[str] = []
    for entry in manifest["decks"]:
        if entry["corpus"] == "synthetic":
            dest_dir = out / "slides" / entry["dest"]
            dest_dir.mkdir(parents=True, exist_ok=True)
            for name, content in _SYNTHETIC_DECKS[entry["deck"]].items():
                (dest_dir / name).write_text(content, encoding="utf-8", newline="\n")
            print(f"staged synthetic {entry['deck']} -> {dest_dir}")
            continue
        src_root = sources[entry["corpus"]]
        topic_dir = src_root / entry["topic"]
        dest_dir = out / "slides" / entry["dest"]
        files = _deck_files(topic_dir, entry["deck"])
        if not files:
            print(f"ERROR: no files for {entry['deck']} under {topic_dir}", file=sys.stderr)
            return 2
        for f in files:
            rel = f.relative_to(topic_dir)
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            sanitized = sanitize_deck_text(f.read_text(encoding="utf-8"), _comment_token_for(f))
            target.write_text(sanitized, encoding="utf-8", newline="\n")
            for i, line in enumerate(sanitized.splitlines(), 1):
                if not line.isascii() and not _HEADER_RE.match(line):
                    if not line.lstrip().startswith(("#", "//")):
                        flags.append(f"{target}:{i}: non-ASCII in code line kept verbatim")
        print(f"staged {entry['deck']} -> {dest_dir}  ({len(files)} file(s))")
    if flags:
        review = out / "REVIEW-FLAGS.txt"
        review.write_text("\n".join(flags) + "\n", encoding="utf-8")
        print(f"{len(flags)} line(s) flagged for review -> {review}")
    return 0


def _structure(de_path: Path) -> dict:
    bundle = load_bundle(de_path)
    if bundle.outcome.refusal is not None:
        return {"refusal": sorted({r.code for r in bundle.outcome.refusal.reasons})}
    deck = bundle.outcome.deck
    assert deck is not None
    members = []
    for member, group in iter_with_groups(deck):
        de = member.de.lines if member.de else None
        en = member.en.lines if member.en else None
        members.append(
            {
                "key": member.key.render(),
                "group": group,
                "langness": member.langness,
                "layout": member.layout,
                "kind": member.kind,
                "role": member.role,
                "owner": member.owner.render() if member.owner else None,
                "sides": (de is not None, en is not None),
                "byte_equal": de == en,
            }
        )
    return {
        "members": members,
        "observations": sorted(o.kind for o in deck.observations),
    }


def cmd_verify(manifest_path: Path, out: Path, source_pairs: list[str]) -> int:
    manifest = _load_manifest(manifest_path)
    sources = _resolve_sources(source_pairs)
    failures = 0
    for entry in manifest["decks"]:
        if entry["corpus"] == "synthetic":
            staged_de = next(
                iter(sorted((out / "slides" / entry["dest"]).glob("slides_*.de.*"))), None
            )
            assert staged_de is not None, entry["dest"]
            staged = _structure(staged_de)
            expected = entry.get("expect_refusal")
            got = staged.get("refusal")
            if (expected or None) == (got or None):
                print(f"ok   synthetic {entry['deck']}  (refusal={got})")
            else:
                failures += 1
                print(f"FAIL synthetic {entry['deck']}: refusal {got} != expected {expected}")
            continue
        src_root = sources[entry["corpus"]]
        original_de = next(
            iter(sorted((src_root / entry["topic"]).glob(f"{entry['deck']}.de.*"))), None
        )
        staged_de = next(
            iter(sorted((out / "slides" / entry["dest"]).glob(f"{entry['deck']}.de.*"))), None
        )
        if original_de is None or staged_de is None:
            print(f"FAIL {entry['deck']}: missing de half (orig={original_de}, staged={staged_de})")
            failures += 1
            continue
        original = _structure(original_de)
        staged = _structure(staged_de)
        if original == staged:
            n = len(original.get("members", [])) or original.get("refusal")
            print(f"ok   {entry['deck']}  ({n})")
        else:
            failures += 1
            print(f"FAIL {entry['deck']}: structural drift")
            for key in ("refusal", "observations"):
                if original.get(key) != staged.get(key):
                    print(f"  {key}: {original.get(key)} -> {staged.get(key)}")
            o_members = original.get("members", [])
            s_members = staged.get("members", [])
            if len(o_members) != len(s_members):
                print(f"  member count: {len(o_members)} -> {len(s_members)}")
            for om, sm in zip(o_members, s_members, strict=False):
                if om != sm:
                    print(f"  first drift at {om['key']}: {om} -> {sm}")
                    break
    print(f"\n{len(manifest['decks']) - failures}/{len(manifest['decks'])} decks parity-clean")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("src", nargs="+", type=Path)
    for name in ("stage", "verify"):
        p = sub.add_parser(name)
        p.add_argument("manifest", type=Path)
        p.add_argument("out", type=Path)
        p.add_argument("--src", action="append", default=[], metavar="NAME=DIR")
    args = parser.parse_args()
    if args.command == "analyze":
        return cmd_analyze(args.src)
    if args.command == "stage":
        return cmd_stage(args.manifest, args.out, args.src)
    return cmd_verify(args.manifest, args.out, args.src)


if __name__ == "__main__":
    raise SystemExit(main())
