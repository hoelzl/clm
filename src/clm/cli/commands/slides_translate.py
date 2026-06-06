"""``clm slides translate`` (alias ``bootstrap``) — full-deck cold-start translation.

Issue #232. When an author writes a deck in a **single** language (only
``slides_x.de.py``), this command synthesizes the other-language split half as a
complete translation of the whole deck — the cold start that ``clm slides sync``
deliberately refuses to perform (``sync`` only fills per-cell gaps inside an
*already-existing* pair).

Code is mostly **not** translated: a cell with no ``lang`` attribute is shared
and copied byte-for-byte into both halves; only ``lang``-tagged cells are
translated (code cells through the identifier-preserving code prompt). The
voiceover companion is translated in lockstep. Freshly-bootstrapped pairs are
minted with EN-authority shared ``slide_id``\\ s and the sync watermark is
recorded, so the next ``clm slides translate`` (or ``clm slides sync``) is a
clean incremental no-op rather than a re-translation.

Dispatch (design decision D2): when the twin is **absent** the deck is
bootstrapped; when it is **present** the command degrades to incremental
``sync`` (``build_sync_plan`` + ``apply_plan``), so re-running converges and
never doubles the deck.

Exit codes:

- ``0`` — wrote the new half (or the delegated sync was clean)
- ``1`` — needs review (a delegated sync deferred something), or no API key
- ``2`` — a hard error (a bad source path, or the engine could not translate)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from clm.cli.commands.slides_sync import CACHE_DB_NAME, _resolve_judge
from clm.infrastructure.llm.cache import (
    SyncWatermarkCache,
    TranslationCache,
    resolve_cache_dir,
)
from clm.infrastructure.llm.openrouter_client import (
    DEFAULT_SYNC_JUDGE_MODEL,
    has_openrouter_api_key,
)
from clm.infrastructure.utils.path_utils import path_to_prog_lang
from clm.slides.sync_translate import (
    DEFAULT_TRANSLATION_MODEL,
    CachingSlideTranslator,
    OpenRouterSlideTranslator,
)
from clm.slides.translate_bootstrap import (
    BootstrapResult,
    TranslateBootstrapError,
    bootstrap_deck,
    derive_bootstrap_paths,
)
from clm.slides.translate_deck import TranslateDeckError

if TYPE_CHECKING:
    from clm.slides.sync_translate import SlideTranslator
    from clm.slides.translate_bootstrap import BootstrapPaths


def _make_translator(
    translation_model: str,
    translation_cache: TranslationCache | None,
    prog_lang: str = "python",
) -> SlideTranslator:
    """The OpenRouter slide translator, cache-wrapped unless ``--no-cache``.

    Factored out (and module-level) so tests can monkeypatch it with a static
    translator, exactly as the sync CLI tests patch ``OpenRouterSlideTranslator``.
    ``prog_lang`` makes the prompt name the deck's language + comment token.
    """
    inner = OpenRouterSlideTranslator(model=translation_model, prog_lang=prog_lang)
    if translation_cache is None:
        return inner
    return CachingSlideTranslator(inner=inner, cache=translation_cache)


@click.command("translate")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--to",
    "to_lang",
    type=click.Choice(["en", "de"]),
    default=None,
    help=(
        "Target language. Default: the opposite of SOURCE's .de/.en tag "
        "(slides_x.de.py → en). Override when a source mixes/omits lang tags."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Preview only: show the target path and how many cells would be "
        "translated vs copied, and write nothing. Uses no LLM and no API key."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Overwrite an existing twin (and its companion) by re-bootstrapping. "
        "Without it, an existing twin degrades to an incremental sync instead."
    ),
)
@click.option(
    "--translation-model",
    default=DEFAULT_TRANSLATION_MODEL,
    show_default=True,
    help="OpenRouter model used to translate the deck. Needs $OPENROUTER_API_KEY (or $OPENAI_API_KEY).",
)
@click.option(
    "--provider",
    type=click.Choice(["openrouter", "local"]),
    default=lambda: os.environ.get("CLM_SYNC_PROVIDER") or "openrouter",
    show_default="openrouter (or $CLM_SYNC_PROVIDER)",
    help=(
        "Edit-reconciliation judge backend for the delegated-sync path (when the "
        "twin already exists): 'openrouter' (default) or 'local' (Ollama). Unused "
        "on the bootstrap path."
    ),
)
@click.option(
    "--llm-model",
    default=None,
    help=f"Model for the delegated-sync edit judge (default '{DEFAULT_SYNC_JUDGE_MODEL}' for openrouter).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory holding the translation + watermark caches (default: "
        "--cache-dir > $CLM_CACHE_DIR > tool.clm.cache_dir > <cwd>/.clm-cache/)."
    ),
)
@click.option(
    "--no-cache", is_flag=True, help="Do not read or write the translation/watermark caches."
)
@click.option(
    "--no-env-file",
    is_flag=True,
    default=False,
    help=(
        "Do not auto-load a .env file. By default the command walks up from "
        "SOURCE's directory and loads the first .env found, so a project "
        "$OPENROUTER_API_KEY / $OPENAI_API_KEY is available to the translator."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def slides_translate_cmd(
    source: Path,
    to_lang: str | None,
    dry_run: bool,
    force: bool,
    translation_model: str,
    provider: str,
    llm_model: str | None,
    cache_dir: Path | None,
    no_cache: bool,
    no_env_file: bool,
    as_json: bool,
) -> None:
    """Translate a single-language deck SOURCE into its other-language split half.

    SOURCE is one half of a split deck (``slides_x.de.py`` or ``slides_x.en.py``).
    The matching ``slides_x.en.py`` / ``slides_x.de.py`` is synthesized as a full
    translation; a voiceover companion is translated in lockstep. Run
    ``clm slides unify`` afterward for a bilingual file, or just keep editing the
    halves and ``clm slides sync`` them.

    \b
    Behavior:
      * Twin absent → bootstrap: translate the whole deck, copy shared (no-lang)
        cells verbatim, mint EN-authority shared slide_ids, and record the sync
        watermark. Re-running is then a clean incremental sync.
      * Twin present → delegate to incremental sync (never re-translates the deck).
      * --dry-run previews the plan and writes nothing.
    """
    # Resolve direction + twin path up front (pure, no LLM) so we know whether
    # this is a bootstrap or a sync before building any client. A bad source
    # (bilingual stem, voiceover companion, contradictory --to) errors here.
    try:
        paths = derive_bootstrap_paths(source, to_lang)
    except TranslateBootstrapError as exc:
        raise click.UsageError(str(exc)) from exc

    if dry_run:
        _emit_dry_run(paths, as_json=as_json)
        sys.exit(0)

    will_sync = paths.twin_exists and not force

    # Load the project .env so a key kept only in .env is found before the key
    # check (the usual course-repo layout). Skipped with --no-env-file.
    if not no_env_file:
        from clm.cli.env_loading import load_env_files

        load_env_files(paths.source_path.parent, paths.twin_path.parent)

    # The bootstrap path translates the WHOLE deck, so without a key it would
    # produce nothing useful — fail fast and write nothing (unlike sync's
    # per-cell defer). The delegated-sync path degrades like `clm slides sync`.
    if not will_sync and not has_openrouter_api_key():
        click.echo(
            "error: OPENROUTER_API_KEY (or OPENAI_API_KEY) is not set; cannot "
            "translate the deck. Set a key (or keep it in a .env next to the deck) "
            "and re-run.",
            err=True,
        )
        sys.exit(1)

    watermark_cache: SyncWatermarkCache | None = None
    translation_cache: TranslationCache | None = None
    if not no_cache:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
        translation_cache = TranslationCache(cache_root / CACHE_DB_NAME)

    result: BootstrapResult
    try:
        translator = _make_translator(
            translation_model, translation_cache, path_to_prog_lang(source)
        )
        # A judge is only consulted on the delegated-sync path (twin present).
        judge = _resolve_judge(provider, llm_model, None, None) if will_sync else None
        result = bootstrap_deck(
            source,
            target_lang=to_lang,
            translator=translator,
            judge=judge,
            watermark_cache=watermark_cache,
            force=force,
        )
    except TranslateDeckError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    finally:
        if watermark_cache is not None:
            watermark_cache.close()
        if translation_cache is not None:
            translation_cache.close()

    exit_code = _exit_code(result)
    if as_json:
        click.echo(json.dumps(_to_dict(result, exit_code), indent=2))
    else:
        _print_human(result)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def _exit_code(result: BootstrapResult) -> int:
    """0 wrote/clean, 1 review (a delegated sync deferred), 2 sync error."""
    if result.action == "bootstrapped":
        return 0  # the engine is all-or-nothing; reaching here means it wrote.
    apply_result = result.apply_result
    if apply_result is None:
        return 0
    if apply_result.has_errors or (result.plan is not None and result.plan.has_errors):
        return 2
    if apply_result.deferred > 0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Dry-run (side-effect-free preview)
# ---------------------------------------------------------------------------


def _count_cells(text: str) -> tuple[int, int]:
    """(translatable, copied): lang-tagged cells vs language-neutral/shared cells."""
    from clm.slides.raw_cells import split_cells

    _, cells = split_cells(text)
    translatable = sum(1 for c in cells if c.metadata.lang is not None)
    copied = len(cells) - translatable
    return translatable, copied


def _companion_preview(paths: BootstrapPaths) -> str | None:
    """The target companion name a bootstrap would write, if a source companion
    exists (read-only — used by --dry-run)."""
    from clm.slides.voiceover_tools import companion_name, resolve_companion

    source_companion = resolve_companion(paths.source_path)
    if source_companion is None:
        return None
    return (source_companion.parent / companion_name(paths.twin_path)).name


def _emit_dry_run(paths: BootstrapPaths, *, as_json: bool) -> None:
    action = "sync" if paths.twin_exists else "bootstrap"
    translatable, copied = _count_cells(paths.source_path.read_text(encoding="utf-8"))
    companion = _companion_preview(paths)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "mode": "dry-run",
                    "action": action,
                    "source": str(paths.source_path),
                    "target": str(paths.twin_path),
                    "source_lang": paths.source_lang,
                    "target_lang": paths.target_lang,
                    "cells_translatable": translatable,
                    "cells_copied": copied,
                    "companion": companion,
                    "twin_exists": paths.twin_exists,
                },
                indent=2,
            )
        )
        return
    if action == "sync":
        click.echo(
            f"{paths.twin_path.name} already exists — `clm slides translate` would run "
            f"an incremental sync (use `clm slides sync --dry-run` for the per-cell plan)."
        )
        return
    click.echo(
        f"Would bootstrap {paths.twin_path.name} from {paths.source_path.name} "
        f"({paths.source_lang}→{paths.target_lang}): "
        f"{translatable} cell(s) to translate, {copied} copied verbatim."
    )
    if companion is not None:
        click.echo(f"Would translate voiceover companion → {companion}.")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_human(result: BootstrapResult) -> None:
    if result.action == "synced":
        apply_result = result.apply_result
        assert apply_result is not None
        click.echo(
            f"{result.twin_path.name} already existed — ran incremental sync: "
            f"{apply_result.applied} applied, {apply_result.deferred} deferred, "
            f"{len(apply_result.errors)} error(s)."
        )
        for err in apply_result.errors:
            click.echo(f"  error: {err}")
        if apply_result.applied > 0:
            click.echo("Review the propagated changes with `git diff` before committing.")
        return

    deck = result.deck
    assert deck is not None
    parts = [f"{deck.translated_count} translated", f"{deck.copied_count} copied"]
    if result.ids_assigned:
        parts.append(f"{result.ids_assigned} slide_id(s) minted")
    click.echo(f"Bootstrapped {result.twin_path.name} ({', '.join(parts)}).")
    if result.companion is not None:
        if result.companion.action == "translated":
            click.echo(f"Translated voiceover companion → {result.companion.target.name}.")
        else:
            click.echo(
                f"Left existing voiceover companion {result.companion.target.name} untouched "
                f"(use --force to regenerate)."
            )
    click.echo("Review the new half with `git diff` before committing.")


def _to_dict(result: BootstrapResult, exit_code: int) -> dict:
    companion: dict | None = None
    if result.companion is not None:
        companion = {
            "action": result.companion.action,
            "source": str(result.companion.source),
            "target": str(result.companion.target),
        }
    out: dict = {
        "action": result.action,
        "source": str(result.source_path),
        "target": str(result.twin_path),
        "source_lang": result.source_lang,
        "target_lang": result.target_lang,
        "companion": companion,
        "watermark_recorded": result.watermark_recorded,
        "exit_code": exit_code,
    }
    if result.deck is not None:
        out["cells_translated"] = result.deck.translated_count
        out["cells_copied"] = result.deck.copied_count
        out["ids_assigned"] = result.ids_assigned
    if result.apply_result is not None:
        out["sync"] = {
            "applied": result.apply_result.applied,
            "deferred": result.apply_result.deferred,
            "errors": list(result.apply_result.errors),
        }
    return out
