"""``clm slides translate`` (alias ``bootstrap``) — the cold-start agent toolkit.

Issue #232 created this as a stand-alone command that translated a whole
single-language deck in-process through OpenRouter; issue #961 re-cut it into
the agent-toolkit contract (``clm info agent-tasks``). When an author has
written only ``slides_x.de.<ext>``, the verbs frame and land the synthesis of
the other-language split half — the cold start ``clm slides sync``
deliberately refuses to perform.

Verbs:

- ``report`` (the bare default) — read-only. Twin absent: the task counts and
  a pointer at ``task``; twin present: the read-only v3 sync diff (#520).
- ``task`` — frame the whole-deck cold start as ONE JSON task document
  (instructions + inputs + ``answer_schema`` + freshness tokens). No model,
  no API key.
- ``accept`` — validate an answer document (shape + freshness + coverage) and
  write the twin (and companion) through the ordinary bootstrap engine:
  EN-authority shared ``slide_id``\\ s minted onto both halves, the pair
  recorded in the committed sync ledger.
- ``autopilot`` — the legacy in-process OpenRouter translation for the
  agent-less human; key-gated, never in CI.

Code is mostly **not** translated: a cell with no ``lang`` attribute is shared
and copied byte-for-byte into both halves; only ``lang``-tagged cells are
translated (code cells through the identifier-preserving code prompt). The
voiceover companion is translated in lockstep (skipped when its target
already exists; ``autopilot --force`` regenerates one).

Exit codes (``0`` clean / ``1`` work pending / ``2`` error): ``report`` exits
``1`` when the twin is absent (cold start pending) or the pair has pending
sync items; ``task`` exits ``2`` when the deck cannot be framed; ``accept``
exits ``0`` written (``1`` when the ledger record was withheld), ``2``
rejected with nothing written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import click

from clm.cli._default_verb_group import DefaultVerbGroup
from clm.core.utils.path_utils import path_to_prog_lang
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.infrastructure.llm.cache import (
    CACHE_DB_NAME,
    TranslationCache,
    resolve_cache_dir,
)
from clm.infrastructure.llm.openrouter_client import has_openrouter_api_key
from clm.slides.glossary import GLOSSARY_STEM, resolve_guidance
from clm.slides.sync_translate import (
    DEFAULT_TRANSLATION_MODEL,
    CachingSlideTranslator,
    OpenRouterSlideTranslator,
)
from clm.slides.translate_bootstrap import (
    BootstrapPaths,
    BootstrapResult,
    TranslateBootstrapError,
    bootstrap_deck,
    derive_bootstrap_paths,
)
from clm.slides.translate_deck import TranslateDeckError, plan_cells
from clm.slides.translate_task import TaskUnavailable, build_task

if TYPE_CHECKING:
    from clm.slides.sync_translate import SlideTranslator


def _make_translator(
    translation_model: str,
    translation_cache: TranslationCache | None,
    prog_lang: str = "python",
    guidance: str = "",
    guidance_by_lang: dict[str, str] | None = None,
) -> SlideTranslator:
    """The OpenRouter slide translator, cache-wrapped unless ``--no-cache``.

    Factored out (and module-level) so tests can monkeypatch it with a static
    translator, exactly as the sync CLI tests patch ``OpenRouterSlideTranslator``.
    ``prog_lang`` makes the prompt name the deck's language + comment token;
    ``guidance`` carries optional target-language conventions (style + glossary)
    for the **single-direction** bootstrap path; ``guidance_by_lang`` carries
    **per-language** conventions for the delegated-sync path (bidirectional, like
    ``clm slides sync``). Either is appended to the system prompt and folded into
    the cache key.
    """
    inner = OpenRouterSlideTranslator(
        model=translation_model,
        prog_lang=prog_lang,
        guidance=guidance,
        guidance_by_lang=guidance_by_lang or {},
    )
    if translation_cache is None:
        return inner
    return CachingSlideTranslator(inner=inner, cache=translation_cache)


# ---------------------------------------------------------------------------
# The verb group
# ---------------------------------------------------------------------------


@click.group("translate", cls=DefaultVerbGroup)
def slides_translate_group() -> None:
    """Cold-start translation of a single-language deck (agent toolkit).

    \b
    Bare `clm slides translate DECK` == `clm slides translate report DECK`
    (read-only). Verbs:
      report     twin absent: the task counts + pointer; twin present: sync report
      task       frame the whole-deck cold start as a JSON task (no model, no key)
      accept     validate an answer document and write the twin (+ companion)
      autopilot  the legacy in-process OpenRouter bootstrap (needs an API key)

    \b
    The framing and the write follow the agent-task contract
    (`clm info sync-agents` → "Cold-starting a twin"); the engine never calls
    a model outside `autopilot`.
    """


#: Shared SOURCE argument (one split half) for every verb.
_SOURCE_ARG = click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
_TO_OPTION = click.option(
    "--to",
    "to_lang",
    type=click.Choice(["en", "de"]),
    default=None,
    help=(
        "Target language. Default: the opposite of SOURCE's .de/.en tag "
        "(slides_x.de.<ext> → en). Override when a source mixes/omits lang tags."
    ),
)


def _derive_or_usage_error(source: Path, to_lang: str | None) -> BootstrapPaths:
    try:
        return derive_bootstrap_paths(source, to_lang)
    except TranslateBootstrapError as exc:
        raise click.UsageError(str(exc)) from exc


# ---------------------------------------------------------------------------
# report — the bare default verb (read-only)
# ---------------------------------------------------------------------------


@slides_translate_group.command("report")
@_SOURCE_ARG
@_TO_OPTION
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Preview only: show the target path and how many cells would be "
        "translated vs copied, and write nothing."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def translate_report_cmd(source: Path, to_lang: str | None, dry_run: bool, as_json: bool) -> None:
    """Report the deck's translation state (writes nothing, no model, no key).

    Twin **absent** — the cold start is pending: reports how many cells a
    ``task`` would frame (translated vs copied verbatim) and points at the
    ``task`` / ``accept`` loop; exit ``1``. Twin **present** — the read-only
    v3 sync diff over the pair (exit ``0`` in sync / ``1`` pending items /
    ``2`` unparseable); reconciliation belongs to the ``clm slides sync``
    verbs, so re-running converges and never doubles the deck.
    """
    paths = _derive_or_usage_error(source, to_lang)

    if paths.twin_exists:
        result = bootstrap_deck(source, target_lang=to_lang, force=False)
        exit_code = _exit_code(result)
        if as_json:
            click.echo(json.dumps(_to_dict(result, exit_code), indent=2))
        else:
            _print_human(result)
        sys.exit(exit_code)

    if dry_run:
        _emit_dry_run(paths, as_json=as_json)
        sys.exit(0)
    _emit_cold_start(paths, as_json=as_json)
    sys.exit(1)


def _cold_start_counts(paths: BootstrapPaths) -> tuple[int, int]:
    """(translatable, copied) per the engine's own classification."""
    plans = plan_cells(
        paths.source_path.read_text(encoding="utf-8"),
        source_lang=paths.source_lang,
        comment_token=comment_token_for_path(paths.source_path),
    )
    translatable = sum(
        1 for p in plans if p.kind in ("translated", "header") and p.source_body is not None
    )
    return translatable, len(plans) - translatable


def _emit_cold_start(paths: BootstrapPaths, *, as_json: bool) -> None:
    translatable, copied = _cold_start_counts(paths)
    companion = _companion_preview(paths)
    source = paths.source_path.name
    if as_json:
        click.echo(
            json.dumps(
                {
                    "schema": 1,
                    "tool": "translate",
                    "verb": "report",
                    "action": "cold-start",
                    "source": str(paths.source_path),
                    "target": str(paths.twin_path),
                    "source_lang": paths.source_lang,
                    "target_lang": paths.target_lang,
                    "cells_translatable": translatable,
                    "cells_copied": copied,
                    "companion": companion,
                    "twin_exists": False,
                    "verbs": {
                        "task": f"clm slides translate task {source}",
                        "accept": f"clm slides translate accept {source} --answer answer.json",
                        "autopilot": f"clm slides translate autopilot {source}",
                    },
                    "exit_code": 1,
                },
                indent=2,
            )
        )
        return
    click.echo(
        f"{paths.twin_path.name} does not exist yet — cold start pending: "
        f"{translatable} translatable cell(s), {copied} copied verbatim."
    )
    if companion is not None:
        click.echo(f"Voiceover companion would be translated in lockstep → {companion}.")
    click.echo(
        f"Frame the work with `clm slides translate task {source}`, answer it, then "
        f"land it with `clm slides translate accept {source} --answer answer.json` "
        "(no API key needed), or run `clm slides translate autopilot` with one."
    )


# ---------------------------------------------------------------------------
# task — the framed cold start (read-only, model-free)
# ---------------------------------------------------------------------------


@slides_translate_group.command("task")
@_SOURCE_ARG
@_TO_OPTION
@click.option(
    "--glossary",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Translation conventions file (Markdown: a style note + term glossary) "
        "folded into the framed prompts. Default: auto-discover "
        f"'{GLOSSARY_STEM}.<target-lang>.md' walking up from SOURCE's directory."
    ),
)
def translate_task_cmd(source: Path, to_lang: str | None, glossary: Path | None) -> None:
    """Frame the whole-deck cold start as a JSON task document (read-only).

    Emits ONE task document: caller instructions, every cell of the deck (and
    voiceover companion) with its translation role and source body — shared
    cells as copy rows so you see the whole plan — the three role prompts,
    the ``answer_schema``, and the freshness tokens (``source_fingerprint``,
    ``companion_fingerprint``) that ``accept`` re-checks. Uses no model and
    no API key.

    \b
    Exit codes: 0 task emitted · 2 the deck cannot be framed (the twin
    already exists, or the source is not a single split half).
    """
    try:
        payload = build_task(source, target_lang=to_lang, glossary=glossary)
    except TaskUnavailable as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    sys.exit(0)


# ---------------------------------------------------------------------------
# accept — the validated write
# ---------------------------------------------------------------------------


@slides_translate_group.command("accept")
@_SOURCE_ARG
@_TO_OPTION
@click.option(
    "--answer",
    "answer_src",
    required=True,
    help="The answer document framed by `task`: a file path, or '-' for stdin.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-bootstrap over a twin (and companion) that appeared after framing.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate the answer fully (shape + freshness + coverage) and write nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON outcome envelope.")
def translate_accept_cmd(
    source: Path,
    to_lang: str | None,
    answer_src: str,
    force: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Validate an answer document and write the missing half (+ companion).

    Validates the answer against the task's schema and the LIVE files
    (fingerprint freshness — a concurrent edit rejects, never overwrites),
    requires coverage of exactly the framed cells, then writes through the
    ordinary bootstrap engine: twin and companion atomically, EN-authority
    shared ``slide_id``\\ s minted onto both halves, the pair recorded in the
    committed sync ledger. Uses no model and no API key; nothing is written
    on any validation failure.

    \b
    Exit codes: 0 written · 1 written but the ledger record was withheld ·
    2 rejected / error (nothing written).
    """
    from clm.core.voiceover_companions import companion_name, resolve_companion
    from clm.slides.agent_task import (
        EXIT_CLEAN,
        EXIT_ERROR,
        EXIT_WORK_PENDING,
        VALIDATORS,
        AnswerRejected,
        rejection_payload,
    )
    from clm.slides.translate_deck import TranslateDeckError, translate_deck_text
    from clm.slides.translate_task import TranslateAnswer, prepare_accept

    if answer_src == "-":
        raw = click.get_text_stream("stdin").read()
    else:
        answer_path = Path(answer_src)
        if not answer_path.exists():
            raise click.UsageError(f"answer file not found: {answer_src}")
        raw = answer_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise click.UsageError(f"the answer is not valid JSON: {exc}") from exc

    def _reject(reason: str) -> NoReturn:
        if as_json:
            click.echo(
                json.dumps(
                    rejection_payload(1, tool="translate", verb="accept", reason=reason),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"rejected: {reason}", err=True)
        sys.exit(EXIT_ERROR)

    try:
        answer: TranslateAnswer = VALIDATORS.get("translate-deck")(payload)
        paths, translator = prepare_accept(source, answer, target_lang=to_lang, force=force)
    except (AnswerRejected, KeyError, ValueError, TypeError) as exc:
        # AnswerRejected: a named violation. KeyError: the validator label is
        # unknown (registry drift). ValueError/TypeError: a non-object payload
        # slipping past json.loads (e.g. a bare string/number answer).
        _reject(str(exc))

    comment_token = comment_token_for_path(paths.source_path)
    written: list[str] = []
    companion_written: dict[str, str] | None = None
    cells_translated = cells_copied = 0
    ids_assigned = 0
    ledger_recorded = False
    try:
        if dry_run:
            deck = translate_deck_text(
                paths.source_path.read_text(encoding="utf-8"),
                source_lang=paths.source_lang,
                target_lang=paths.target_lang,
                translator=translator,
                comment_token=comment_token,
            )
            cells_translated, cells_copied = deck.translated_count, deck.copied_count
            source_companion = resolve_companion(paths.source_path)
            if source_companion is not None:
                target = source_companion.parent / companion_name(paths.twin_path)
                if force or not (target.exists() and target.stat().st_size > 0):
                    translate_deck_text(
                        source_companion.read_text(encoding="utf-8"),
                        source_lang=paths.source_lang,
                        target_lang=paths.target_lang,
                        translator=translator,
                        comment_token=comment_token,
                    )
                    companion_written = {"action": "translated", "target": str(target)}
                else:
                    companion_written = {"action": "skipped", "target": str(target)}
        else:
            result = bootstrap_deck(
                source,
                target_lang=to_lang,
                translator=translator,
                force=force,
            )
            assert result.action == "bootstrapped"  # prepare_accept proved twin absent/force
            assert result.deck is not None
            cells_translated, cells_copied = (
                result.deck.translated_count,
                result.deck.copied_count,
            )
            ids_assigned = result.ids_assigned
            ledger_recorded = result.ledger_recorded
            written.append(str(result.twin_path))
            if result.companion is not None:
                companion_written = {
                    "action": result.companion.action,
                    "target": str(result.companion.target),
                }
                if result.companion.action == "translated":
                    written.append(str(result.companion.target))
    except TranslateDeckError as exc:
        _reject(str(exc))

    if dry_run:
        outcome = {
            "schema": 1,
            "tool": "translate",
            "verb": "accept",
            "dry_run": True,
            "applied": False,
            "target": str(paths.twin_path),
            "cells_translated": cells_translated,
            "cells_copied": cells_copied,
            "companion": companion_written,
        }
        if as_json:
            click.echo(json.dumps(outcome, indent=2, ensure_ascii=False))
        else:
            click.echo(
                f"dry-run: would bootstrap {paths.twin_path.name} "
                f"({cells_translated} translated, {cells_copied} copied)."
            )
        sys.exit(EXIT_CLEAN)

    outcome = {
        "schema": 1,
        "tool": "translate",
        "verb": "accept",
        "applied": True,
        "action": "bootstrapped",
        "source": str(paths.source_path),
        "target": str(paths.twin_path),
        "source_lang": paths.source_lang,
        "target_lang": paths.target_lang,
        "written": written,
        "cells_translated": cells_translated,
        "cells_copied": cells_copied,
        "ids_assigned": ids_assigned,
        "ledger_recorded": ledger_recorded,
        "companion": companion_written,
        "exit_code": EXIT_CLEAN,
    }
    if as_json:
        click.echo(json.dumps(outcome, indent=2, ensure_ascii=False))
    else:
        parts = [f"{cells_translated} translated", f"{cells_copied} copied"]
        if ids_assigned:
            parts.append(f"{ids_assigned} slide_id(s) minted")
        click.echo(f"Bootstrapped {paths.twin_path.name} ({', '.join(parts)}).")
        if companion_written is not None and companion_written["action"] == "translated":
            click.echo(f"Translated voiceover companion → {companion_written['target']}.")
        click.echo("Review the new half with `git diff` before committing.")
        if not ledger_recorded:
            click.echo(
                "ledger: record withheld — run `clm slides sync record` after normalizing the pair",
                err=True,
            )
    sys.exit(EXIT_WORK_PENDING if not ledger_recorded else EXIT_CLEAN)


# ---------------------------------------------------------------------------
# autopilot — the legacy in-process OpenRouter path (key-gated)
# ---------------------------------------------------------------------------


@slides_translate_group.command("autopilot")
@_SOURCE_ARG
@_TO_OPTION
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Overwrite an existing twin (and its companion) by re-bootstrapping. "
        "Without it, an existing twin degrades to the read-only sync report."
    ),
)
@click.option(
    "--translation-model",
    default=DEFAULT_TRANSLATION_MODEL,
    show_default=True,
    help="OpenRouter model used to translate the deck. Needs $OPENROUTER_API_KEY (or $OPENAI_API_KEY).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory holding the translation cache (default: "
        "--cache-dir > $CLM_CACHE_DIR > tool.clm.cache_dir > <cwd>/.clm-cache/)."
    ),
)
@click.option(
    "--glossary",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Translation conventions file (Markdown: a style note + term glossary) "
        "appended to the translation prompt. Default: auto-discover "
        f"'{GLOSSARY_STEM}.<target-lang>.md' walking up from SOURCE's directory."
    ),
)
@click.option("--no-cache", is_flag=True, help="Do not read or write the translation cache.")
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
def translate_autopilot_cmd(
    source: Path,
    to_lang: str | None,
    force: bool,
    translation_model: str,
    cache_dir: Path | None,
    glossary: Path | None,
    no_cache: bool,
    no_env_file: bool,
    as_json: bool,
) -> None:
    """Translate the whole deck in-process through OpenRouter (needs a key).

    The agent-less path: for a human with an ``$OPENROUTER_API_KEY`` (or
    ``$OPENAI_API_KEY``). The engine, post-conditions and exit codes are
    those of ``accept``; only the judgment source differs (the embedded
    model instead of an agent's answer document). Agents and CI should use
    ``task`` / ``accept`` — no key, same result.
    """
    paths = _derive_or_usage_error(source, to_lang)

    will_sync = paths.twin_exists and not force
    if will_sync:
        # Read-only diff — no model, no key, no cache. Reconciliation belongs
        # to the `clm slides sync` verbs.
        result = bootstrap_deck(source, target_lang=to_lang, force=False)
        exit_code = _exit_code(result)
        if as_json:
            click.echo(json.dumps(_to_dict(result, exit_code), indent=2))
        else:
            _print_human(result)
        sys.exit(exit_code)

    # Load the project .env so a key kept only in .env is found before the key
    # check (the usual course-repo layout). Skipped with --no-env-file.
    if not no_env_file:
        from clm.cli.env_loading import load_env_files

        load_env_files(paths.source_path.parent, paths.twin_path.parent)

    # The autopilot translates the WHOLE deck, so without a key it would
    # produce nothing useful — fail fast and write nothing.
    if not has_openrouter_api_key():
        click.echo(
            "error: OPENROUTER_API_KEY (or OPENAI_API_KEY) is not set; cannot "
            "translate the deck. Set a key (or keep it in a .env next to the deck) "
            "and re-run — or drive the model-free loop: `clm slides translate task` "
            "+ `clm slides translate accept` (no key needed).",
            err=True,
        )
        sys.exit(1)

    translation_cache: TranslationCache | None = None
    if not no_cache:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        translation_cache = TranslationCache(cache_root / CACHE_DB_NAME)

    # Resolve translation conventions (style + glossary): the bootstrap is
    # single-direction (source -> target), so the target-language glossary applies
    # (an explicit --glossary, else an auto-discovered clm-glossary.<target>.md).
    guidance, glossary_path = resolve_guidance(glossary, source.parent, paths.target_lang)
    if glossary_path is not None and not as_json:
        click.echo(f"Using glossary: {glossary_path}", err=True)

    try:
        translator = _make_translator(
            translation_model,
            translation_cache,
            path_to_prog_lang(source),
            guidance,
        )
        result = bootstrap_deck(
            source,
            target_lang=to_lang,
            translator=translator,
            force=force,
        )
    except TranslateDeckError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    finally:
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
    """0 wrote/clean, 1 review (the existing pair has pending items), 2 error."""
    if result.action == "bootstrapped":
        return 0  # the engine is all-or-nothing; reaching here means it wrote.
    if result.diff_error is not None:
        return 2
    if result.diff is not None and not result.diff.is_clean:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Read-only previews
# ---------------------------------------------------------------------------


def _companion_preview(paths: BootstrapPaths) -> str | None:
    """The target companion name a bootstrap would write, if a source companion
    exists (read-only — used by report)."""
    from clm.core.voiceover_companions import companion_name, resolve_companion

    source_companion = resolve_companion(paths.source_path)
    if source_companion is None:
        return None
    return (source_companion.parent / companion_name(paths.twin_path)).name


def _emit_dry_run(paths: BootstrapPaths, *, as_json: bool) -> None:
    action = "sync" if paths.twin_exists else "bootstrap"
    translatable, copied = _cold_start_counts(paths)
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
            f"{paths.twin_path.name} already exists — `clm slides translate` would "
            f"report the pair's sync state (use `clm slides sync report` for the "
            f"per-member view)."
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
        if result.diff_error is not None:
            click.echo(
                f"{result.twin_path.name} already exists but the pair cannot be "
                f"diffed: {result.diff_error}",
                err=True,
            )
            return
        diff = result.diff
        assert diff is not None
        if diff.is_clean:
            click.echo(
                f"{result.twin_path.name} already exists and the pair is in sync "
                f"({diff.in_sync_count} member(s)) — nothing to translate."
            )
        else:
            click.echo(
                f"{result.twin_path.name} already exists with {len(diff.items)} "
                "pending sync item(s) — nothing was translated. Reconcile with "
                "`clm slides sync report/apply`, then re-run if needed."
            )
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
        "ledger_recorded": result.ledger_recorded,
        "exit_code": exit_code,
    }
    if result.deck is not None:
        out["cells_translated"] = result.deck.translated_count
        out["cells_copied"] = result.deck.copied_count
        out["ids_assigned"] = result.ids_assigned
    if result.action == "synced":
        out["sync"] = {
            "is_clean": result.diff.is_clean if result.diff is not None else False,
            "items": len(result.diff.items) if result.diff is not None else None,
            "error": result.diff_error,
        }
    return out
