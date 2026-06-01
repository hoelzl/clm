"""End-to-end ``clm slides sync`` test for the code-cell / aux-markdown fix.

Mirrors the *structure* of the real-world repro that motivated Issue #166 Phase 6
(a single-language editing pass that adds new slides with code, rewrites a
workshop, moves shared setup code between slide groups, and twins localized
code) on a small, **non-proprietary** "Web APIs" deck. It drives the live
``slides_sync_cmd`` with a judge + translator derived from the gold pairing, so
the whole pipeline runs — classify, reconcile edits, translate + insert new
cells, copy language-neutral code verbatim, fix group order — and the synced DE
half is asserted byte-identical to the hand-authored gold.

Before the fix the sync only touched narrative markdown: every code cell and the
untagged / ``alt`` markdown below were silently dropped, leaving German headings
sitting over stale English code. This test fails hard in that world.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides_sync import CACHE_DB_NAME, slides_sync_cmd
from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import watermark_rows
from clm.slides.sync_translate import TranslationError
from clm.slides.sync_writeback import role_of

# ---------------------------------------------------------------------------
# Deck assembly
# ---------------------------------------------------------------------------


def _deck(*blocks: str) -> str:
    """Assemble cell blocks into a deck (j2 header tight, cells blank-separated)."""
    return "\n\n".join(blocks) + "\n"


def _j2(lang: str, title: str) -> str:
    return f"# j2 from 'macros.j2' import header_{lang}\n# {{{{ header_{lang}(\"{title}\") }}}}"


def _md(lang: str, sid: str, tag: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["{tag}"] slide_id="{sid}"\n{body}'


def _aux_untagged(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{sid}"\n{body}'


def _code_shared(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}'


def _code_idd(lang: str, sid: str, body: str) -> str:
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}'


def _code_idless(lang: str, body: str) -> str:
    return f'# %% lang="{lang}"\n{body}'


# --- original (committed) baseline -----------------------------------------


def _original(lang: str) -> str:
    de = lang == "de"
    return _deck(
        _j2(lang, "Web-APIs" if de else "Web APIs"),
        _md(
            lang,
            "intro",
            "voiceover",
            "# - Willkommen zu Web-APIs" if de else "# - Welcome to web APIs",
        ),
        _md(
            lang,
            "get",
            "slide",
            "# ## Eine GET-Anfrage stellen\n#\n# - Ein GET holt Daten"
            if de
            else "# ## Making a GET request\n#\n# - A GET fetches data",
        ),
        _code_shared("import requests"),
        _code_idd(
            lang,
            "get-call",
            'label = "Geholte Einträge"\nresp = requests.get("https://api.example.com/items")'
            if de
            else 'label = "Fetched items"\nresp = requests.get("https://api.example.com/items")',
        ),
        _md(
            lang,
            "get",
            "voiceover",
            "# - Wir rufen requests.get mit einer URL auf"
            if de
            else "# - We call requests.get with a URL",
        ),
        _md(
            lang,
            "workshop",
            "slide",
            "# ## Workshop: Holen und Ausgeben\n#\n# - Aufgabe: Einträge holen, Anzahl ausgeben"
            if de
            else "# ## Workshop: Fetch and Print\n#\n# - Task: fetch items, print the count",
        ),
        _code_idd(
            lang,
            "ws-setup",
            'URL = "https://api.example.com/items"\nTITLE = "Alle Einträge"'
            if de
            else 'URL = "https://api.example.com/items"\nTITLE = "All items"',
        ),
        _md(lang, "ws-task", "subslide", "# ### Aufgabe: zählen" if de else "# ### Task: count"),
        _code_idless(
            lang,
            'data = requests.get(URL).json()\nprint("Anzahl:", len(data))'
            if de
            else 'data = requests.get(URL).json()\nprint("count:", len(data))',
        ),
        _md(
            lang,
            "ws-task",
            "alt",
            "# ### Lösungshinweise\n#\n# - len(data) verwenden"
            if de
            else "# ### Solution notes\n#\n# - Use len(data)",
        ),
    )


# --- after the single-language editing pass --------------------------------
#
# EN is the edited source; DE is the gold a correct sync must produce. They are
# structurally parallel (same cell sequence, language swapped, prose translated).


def _modified(lang: str) -> str:
    de = lang == "de"
    return _deck(
        _j2(lang, "Web-APIs" if de else "Web APIs"),
        _md(
            lang,
            "intro",
            "voiceover",
            "# - Willkommen zu Web-APIs" if de else "# - Welcome to web APIs",
        ),
        # NEW slide group (id-carrying add): a POST theme, inserted before GET.
        _md(
            lang,
            "post",
            "slide",
            "# ## Eine POST-Anfrage stellen\n#\n# - Ein POST sendet Daten"
            if de
            else "# ## Making a POST request\n#\n# - A POST sends data",
        ),
        _code_shared("import requests"),  # MOVED here from the GET group
        _code_shared("import json"),  # NEW language-neutral cell
        _code_idd(  # NEW localized id'd code cell
            lang,
            "post-body",
            'payload = {"name": "Ein neuer Eintrag"}' if de else 'payload = {"name": "A new item"}',
        ),
        _code_shared('resp = requests.post("https://api.example.com/items", json=payload)'),  # NEW
        _aux_untagged(  # NEW untagged aux markdown
            lang, "post-note", "# - Ein POST-Body ist JSON" if de else "# - A POST body is JSON"
        ),
        _md(
            lang,
            "post",
            "voiceover",
            "# - Wir senden Daten mit requests.post"
            if de
            else "# - We send data with requests.post",
        ),
        # EDITED slide (extra bullet) — import requests no longer in this group.
        _md(
            lang,
            "get",
            "slide",
            "# ## Eine GET-Anfrage stellen\n#\n# - Ein GET holt Daten\n# - GET ist das häufigste Verb"
            if de
            else "# ## Making a GET request\n#\n# - A GET fetches data\n# - GET is the most common verb",
        ),
        _code_idd(  # EDITED localized id'd code (string changed)
            lang,
            "get-call",
            'label = "Einen Eintrag geholt"\nresp = requests.get("https://api.example.com/items")'
            if de
            else 'label = "Fetched one item"\nresp = requests.get("https://api.example.com/items")',
        ),
        _md(
            lang,
            "get",
            "voiceover",
            "# - Wir rufen requests.get mit einer URL auf\n# - Die Antwort hat .json()"
            if de
            else "# - We call requests.get with a URL\n# - The response has .json()",
        ),
        # Rewritten workshop.
        _md(
            lang,
            "workshop",
            "slide",
            "# ## Workshop: Einen Eintrag anlegen\n#\n# - Aufgabe: per POST anlegen, dann per GET holen"
            if de
            else "# ## Workshop: Create an Item\n#\n# - Task: POST a new item, then GET the list",
        ),
        _code_idd(  # EDITED localized id'd code
            lang,
            "ws-setup",
            'URL = "https://api.example.com/items"\nNEW_ITEM = {"name": "Widget"}'
            if de
            else 'URL = "https://api.example.com/items"\nNEW_ITEM = {"name": "Widget"}',
        ),
        _md(
            lang,
            "ws-task",
            "subslide",
            "# ### Aufgabe: anlegen, dann auflisten" if de else "# ### Task: create then list",
        ),
        _code_idless(  # EDITED id-less localized code
            lang,
            'requests.post(URL, json=NEW_ITEM)\nprint("angelegt:", NEW_ITEM["name"])'
            if de
            else 'requests.post(URL, json=NEW_ITEM)\nprint("created:", NEW_ITEM["name"])',
        ),
        _code_shared("items = requests.get(URL).json()"),  # NEW language-neutral cell
        _md(
            lang,
            "ws-task",
            "alt",
            "# ### Lösungshinweise\n#\n# - POST, dann GET zur Bestätigung"
            if de
            else "# ### Solution notes\n#\n# - POST then GET to confirm",
        ),
    )


# ---------------------------------------------------------------------------
# Gold-derived judge + translator (no live LLM)
# ---------------------------------------------------------------------------


def _gold_map(modified_en: str, gold_de: str) -> dict[str, str]:
    """Map each EN cell's stripped content -> the gold DE cell's content."""
    en_cells = parse_cells(modified_en)
    de_cells = parse_cells(gold_de)
    assert len(en_cells) == len(de_cells), (len(en_cells), len(de_cells))
    mapping: dict[str, str] = {}
    for i, (en, de) in enumerate(zip(en_cells, de_cells, strict=True)):
        if en.metadata.is_j2:
            continue
        assert en.metadata.slide_id == de.metadata.slide_id, i
        assert role_of(en.metadata) == role_of(de.metadata), i
        mapping[en.content.strip()] = de.content
    return mapping


class _GoldJudge:
    prompt_version = "gold"

    def __init__(self, mapping: dict[str, str]):
        self._m = mapping

    def propose(self, source_text, target_text, *, source_lang, target_lang):
        gold = self._m.get(source_text.strip())
        if gold is None:
            return SyncProposal(verdict="in_sync", proposed_text=target_text)
        return SyncProposal(verdict="update", proposed_text=gold)


class _GoldTranslator:
    prompt_version = "gold"

    def __init__(self, mapping: dict[str, str]):
        self._m = mapping

    def translate(self, *, source_body, source_lang, target_lang, role):
        gold = self._m.get(source_body.strip())
        if gold is None:
            raise TranslationError(f"no gold translation for {source_body[:60]!r}")
        return gold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _seed_watermark(cache_dir: Path, de_path: Path, en_path: Path, de: str, en: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    wm = SyncWatermarkCache(cache_dir / CACHE_DB_NAME)
    try:
        # Membership-widened, mirroring sync_apply._record_watermark — so the
        # baseline carries the "shared" partition the item-2 anchor diff needs.
        de_rows = watermark_rows(parse_cells(de))
        en_rows = watermark_rows(parse_cells(en))
        wm.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
        wm.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
        wm.put_deck(
            de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
        )
    finally:
        wm.close()


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestSyncCodeE2E:
    def test_single_language_pass_reproduces_gold_de(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        orig_de, orig_en = _original("de"), _original("en")
        mod_en, gold_de = _modified("en"), _modified("de")
        mapping = _gold_map(mod_en, gold_de)

        cache_dir = tmp_path / "cache"
        de_path = tmp_path / "apis.de.py"
        en_path = tmp_path / "apis.en.py"
        de_path.write_text(orig_de, encoding="utf-8")
        en_path.write_text(orig_en, encoding="utf-8")
        _seed_watermark(cache_dir, de_path, en_path, orig_de, orig_en)
        # The author's edit: overwrite EN, leave DE at the baseline.
        en_path.write_text(mod_en, encoding="utf-8")

        # Inject the gold-derived judge + translator into the live command.
        from clm.cli.commands import slides_sync as cmd

        monkeypatch.setattr(cmd, "_resolve_judge", lambda *_a, **_k: _GoldJudge(mapping))
        monkeypatch.setattr(cmd, "OpenRouterSlideTranslator", lambda **_k: _GoldTranslator(mapping))

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--no-env-file", "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0, (result.stderr or "") + (result.output or "")
        produced = de_path.read_text(encoding="utf-8")
        if produced != gold_de:  # pragma: no cover - diagnostic on failure
            import difflib

            diff = "".join(
                difflib.unified_diff(
                    gold_de.splitlines(keepends=True),
                    produced.splitlines(keepends=True),
                    "gold",
                    "produced",
                )
            )
            pytest.fail(f"synced DE != gold:\n{diff}")
        # EN (the edited source) is never rewritten by an en->de pass.
        assert en_path.read_text(encoding="utf-8") == mod_en

    def test_rerun_after_pass_is_idempotent(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        orig_de, orig_en = _original("de"), _original("en")
        mod_en, gold_de = _modified("en"), _modified("de")
        mapping = _gold_map(mod_en, gold_de)
        cache_dir = tmp_path / "cache"
        de_path = tmp_path / "apis.de.py"
        en_path = tmp_path / "apis.en.py"
        de_path.write_text(orig_de, encoding="utf-8")
        en_path.write_text(orig_en, encoding="utf-8")
        _seed_watermark(cache_dir, de_path, en_path, orig_de, orig_en)
        en_path.write_text(mod_en, encoding="utf-8")

        from clm.cli.commands import slides_sync as cmd

        monkeypatch.setattr(cmd, "_resolve_judge", lambda *_a, **_k: _GoldJudge(mapping))
        monkeypatch.setattr(cmd, "OpenRouterSlideTranslator", lambda **_k: _GoldTranslator(mapping))

        first = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--no-env-file", "--cache-dir", str(cache_dir)],
        )
        assert first.exit_code == 0, (first.stderr or "") + (first.output or "")
        de_after_first = de_path.read_text(encoding="utf-8")

        # Second run: the watermark advanced to the synced state -> nothing to do.
        second = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--no-env-file", "--json", "--cache-dir", str(cache_dir)],
        )
        assert second.exit_code == 0, (second.stderr or "") + (second.output or "")
        assert de_path.read_text(encoding="utf-8") == de_after_first  # no churn
