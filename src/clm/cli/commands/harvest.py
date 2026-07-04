"""``clm harvest`` — recover narration from recorded videos (#546 Phase 2).

The agent-first rebuild of the video→voiceover feature (epic #546,
`docs/proposals/video-narration-harvest.md`): the deterministic pipeline
lives behind read-only verbs, judgment stays with the driving agent. This
module carries the group plus the Phase-2 surface:

* ``report`` (the default verb) — run the cached deterministic tier and
  emit per-slide JSON keyed by v3 ``MemberKey``, with a structural novelty
  class per slide. Read-only; no model; no key.
* the re-homed diagnostics ``transcribe`` / ``detect`` / ``identify`` /
  ``identify-rev`` / ``cache`` / ``trace`` (shared with ``clm voiceover``
  until the Phase-4 cutover deletes the old names).

``task`` / ``accept`` / ``verify`` / ``autopilot`` arrive in Phases 3–4.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.cli._lazy_group import LazyGroup

_DEFAULT_VERB = "report"


class _DefaultVerbGroup(LazyGroup):
    """A group whose bare ``clm harvest DECK VIDEO`` runs ``report``.

    Unlike the ``slides sync`` variant this one resolves the default verb in
    :meth:`resolve_command` (after group options are parsed), because the
    harvest group carries the cache flags — a ``parse_args`` prepend would
    fire on ``--no-cache`` before Click ever saw it as a group option.
    """

    def resolve_command(self, ctx: click.Context, args: list[str]):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if args and not args[0].startswith("-"):
                cmd = self.get_command(ctx, _DEFAULT_VERB)
                if cmd is not None:
                    return _DEFAULT_VERB, cmd, args
            raise


@click.group("harvest", cls=_DefaultVerbGroup)
@click.option(
    "--cache-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the cache location (default: <deck dir>/.clm/voiceover-cache).",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Disable the artifact cache for this invocation.",
)
@click.option(
    "--refresh-cache",
    is_flag=True,
    default=False,
    help="Force recomputation and overwrite existing cache entries.",
)
@click.pass_context
def harvest_group(ctx, cache_root, no_cache, refresh_cache):
    """Recover spoken narration from recorded videos into slide decks.

    \b
    Bare `clm harvest DECK VIDEO…` == `clm harvest report DECK VIDEO…`.
    Verbs:
      report        what did the recording say, slide by slide? (read-only)
      transcribe    ASR only: dump the transcript (diagnostic)
      detect        slide-transition detection only (diagnostic)
      identify      which slides appear in a video? (diagnostic)
      identify-rev  which git revision of a deck was recorded? (diagnostic)
      cache         inspect/prune the artifact cache
      trace         inspect merge trace logs

    The deterministic tier (ASR, transition detection, OCR matching,
    alignment) is engine-owned, cached, and model-free; curation and
    translation judgment belong to the driving agent (epic #546).

    Requires: pip install clm[voiceover]
    """
    from clm.voiceover.cache import CachePolicy

    ctx.ensure_object(dict)
    ctx.obj["cache_policy"] = CachePolicy(
        enabled=not no_cache,
        refresh=refresh_cache,
        cache_root=cache_root,
    )


# ---------------------------------------------------------------------------
# report — the read-only default verb
# ---------------------------------------------------------------------------


@harvest_group.command("report")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=str)
@click.option(
    "--lang",
    required=True,
    type=click.Choice(["de", "en"]),
    help="The recorded (spoken) language; SLIDES must be that half of the pair.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON report envelope.")
@click.option(
    "--transcript",
    "transcript_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Skip ASR and load a precomputed transcript from PATH "
    "(JSON from `clm harvest transcribe -o`). Single-video only.",
)
@click.option(
    "--alignment",
    "alignment_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Skip ASR, detection, and matching; load a precomputed alignment "
    "from PATH (cache artifact shape). Single-video only.",
)
@click.option(
    "--whisper-model",
    default="large-v3",
    show_default=True,
    help="Whisper model size for ASR.",
)
@click.option(
    "--backend",
    "backend_name",
    default="faster-whisper",
    show_default=True,
    help="Transcription backend.",
)
@click.option(
    "--device",
    default="auto",
    show_default=True,
    type=click.Choice(["auto", "cuda", "cpu"]),
    help="Device for ASR.",
)
@click.pass_context
def harvest_report_cmd(
    ctx,
    slides: Path,
    videos: tuple[str, ...],
    lang: str,
    as_json: bool,
    transcript_override: Path | None,
    alignment_override: Path | None,
    whisper_model: str,
    backend_name: str,
    device: str,
):
    """What did the recording say, slide by slide? (read-only)

    Runs the cached deterministic tier (transcribe → detect transitions →
    OCR-match → align) over VIDEOS and joins the result with the deck
    bundle: one item per slide, keyed by the v3 member handle
    (`id:<slide_id>`), carrying the aligned transcript, the existing
    voiceover baseline on both language sides, and a structural novelty
    class: no_existing_vo | transcript_adds_material | covered |
    unmatched_slide (plus unmatched_speech per unassigned segment).

    \b
    Exit codes:
      0  nothing to harvest (all covered / silent)
      1  actionable items (new material or unmatched speech)
      2  error (unreadable deck, non-normalized bundle, bad inputs)

    No model, no key, no writes — also the human dry-run.
    """
    from clm.cli.commands.voiceover import (
        _expand_video_args,
        _load_alignment_override,
        _load_transcript_override,
    )
    from clm.notebooks.slide_parser import parse_slides
    from clm.slides.doc_lenses import DocLensError, load_bundle
    from clm.voiceover.cache import CachePolicy
    from clm.voiceover.harvest import (
        HarvestUsageError,
        build_report,
        report_exit_code,
        run_pipeline,
    )

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())
    video_paths = _expand_video_args(videos)

    # The v3 deck bundle: both languages + companions, the identity source.
    try:
        bundle = load_bundle(slides)
    except DocLensError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    if bundle.outcome.deck is None:
        refusal = bundle.outcome.refusal
        click.echo("error: the deck bundle is not normalized:", err=True)
        if refusal is not None:
            for reason in refusal.reasons:
                click.echo(f"  [{reason.code}] {reason.detail}", err=True)
        click.echo("run `clm slides normalize` on the pair first.", err=True)
        sys.exit(2)

    # The recorded-language view the OCR matcher and aligner key on.
    slide_groups = parse_slides(slides, lang)

    transcript = _load_transcript_override(transcript_override) if transcript_override else None
    alignment = _load_alignment_override(alignment_override) if alignment_override else None
    try:
        artifacts = run_pipeline(
            slides,
            video_paths,
            lang,
            slide_groups,
            policy=policy,
            backend_name=backend_name,
            whisper_model=whisper_model,
            device=device,
            transcript_override=transcript,
            alignment_override=alignment,
        )
    except HarvestUsageError as exc:
        raise click.UsageError(str(exc)) from exc

    report = build_report(bundle, slide_groups, artifacts, lang=lang, video_paths=video_paths)
    if as_json:
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human_report(report)
    sys.exit(report_exit_code(report))


def _print_human_report(report: dict) -> None:
    summary = report["summary"]
    click.echo(
        f"harvest report: {summary['slides']} slide(s), "
        f"video language {report['video_language']}, "
        f"fingerprint {report['video_fingerprint']}"
    )
    for cls, count in summary["classes"].items():
        if count:
            click.echo(f"  {cls}: {count}")
    if summary["unmatched_speech"]:
        click.echo(f"  unmatched_speech: {summary['unmatched_speech']} segment(s)")
    for item in report["items"]:
        if item["class"] in ("covered", "unmatched_slide"):
            continue
        key = item["key"] or f"(no id, slide_index {item['slide_index']})"
        click.echo(f"  {item['class']:26} {key}  {item['title']}")
    click.echo(
        "actionable — run with --json for the full per-slide payload"
        if summary["actionable"]
        else "nothing to harvest"
    )


# ---------------------------------------------------------------------------
# Re-homed diagnostics (shared with `clm voiceover` until the Phase-4 cutover)
# ---------------------------------------------------------------------------


def _register_diagnostics() -> None:
    from clm.cli.commands.voiceover import (
        cache_group,
        detect,
        identify,
        identify_rev_cmd,
        trace_group,
        transcribe,
    )

    harvest_group.add_command(transcribe)
    harvest_group.add_command(detect)
    harvest_group.add_command(identify)
    harvest_group.add_command(identify_rev_cmd, name="identify-rev")
    harvest_group.add_command(cache_group, name="cache")
    harvest_group.add_command(trace_group, name="trace")


_register_diagnostics()
