"""``clm harvest`` — recover narration from recorded videos (#546 Phase 2).

The agent-first rebuild of the video→voiceover feature (epic #546,
`docs/proposals/video-narration-harvest.md`): the deterministic pipeline
lives behind read-only verbs, judgment stays with the driving agent. This
module carries the group plus the Phase-2 surface:

* ``report`` (the default verb) — run the cached deterministic tier and
  emit per-slide JSON keyed by v3 ``MemberKey``, with a structural novelty
  class per slide. Read-only; no model; no key.
* ``task`` — frame one slide's curation/translation judgment for the
  driving agent (instructions + inputs + answer_schema + freshness
  tokens). Read-only.
* ``accept`` — validate a bullet-list answer and write it through the v3
  model (id-keyed member edit, atomic ≤4-file write); ``--record`` banks
  it into the sync ledger under ``harvest:<video-fingerprint>``
  provenance with the §6 one-sided-trust semantics. The only write path.
* ``verify`` — the structural post-check, delegating to the same engine
  as ``clm slides sync verify``.
* the re-homed diagnostics ``transcribe`` / ``detect`` / ``identify`` /
  ``identify-rev`` / ``cache`` / ``trace`` (shared with ``clm voiceover``
  until the Phase-4 cutover deletes the old names).

``autopilot`` (the embedded-model one-shot) arrives in Phase 4.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.cli._default_verb_group import DefaultVerbLazyGroup


@click.group("harvest", cls=DefaultVerbLazyGroup)
@click.option(
    "--cache-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the cache location (default: the shared cache dir's voiceover/ "
    "subdir — CLM_CACHE_DIR, [tool.clm] cache_dir, or <project-root>/.clm-cache/).",
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
      task          frame one slide's curation/translation judgment (read-only)
      accept        validate a bullet-list answer + write it (the agent write path)
      verify        structural post-check on the pair
      align         review/correct the pipeline's heuristic alignment (#960)
      autopilot     legacy all-in-one WITH embedded models (agent-less humans)
      backfill      identify-rev → sync-at-rev → port over historical revisions
      port          transfer voiceover between slide files (LLM merge)
      compare       LLM diff of voiceover between two slide files
      transcribe    ASR only: dump the transcript (diagnostic)
      detect        slide-transition detection only (diagnostic)
      identify      which slides appear in a video? (diagnostic)
      identify-rev  which git revision of a deck was recorded? (diagnostic)
      sync-at-rev   run autopilot against a historical revision (scratch output)
      cache         inspect/prune the artifact cache
      trace         inspect merge trace logs

    \b
    The agent loop (see `clm info harvest-agents`):
      harvest report → (task → judge → accept [--record])* → verify
      → clm slides sync report   (twin translation continues there)

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
# Shared plumbing for the pipeline-driven verbs (report, task)
# ---------------------------------------------------------------------------


def _pipeline_options(f):
    """The option stack `report` and `task` share (video → alignment inputs)."""
    options = [
        click.option(
            "--lang",
            required=True,
            type=click.Choice(["de", "en"]),
            help="The recorded (spoken) language; SLIDES must be that half of the pair.",
        ),
        click.option(
            "--transcript",
            "transcript_override",
            type=click.Path(exists=True, path_type=Path),
            default=None,
            help="Skip ASR and load a precomputed transcript from PATH "
            "(JSON from `clm harvest transcribe -o`). Single-video only.",
        ),
        click.option(
            "--alignment",
            "alignment_override",
            type=click.Path(exists=True, path_type=Path),
            default=None,
            help="Skip ASR, detection, and matching; load a precomputed alignment "
            "from PATH (cache artifact shape). Single-video only.",
        ),
        click.option(
            "--whisper-model",
            default="large-v3",
            show_default=True,
            help="Whisper model size for ASR.",
        ),
        click.option(
            "--backend",
            "backend_name",
            default="faster-whisper",
            show_default=True,
            help="Transcription backend.",
        ),
        click.option(
            "--device",
            default="auto",
            show_default=True,
            type=click.Choice(["auto", "cuda", "cpu"]),
            help="Device for ASR.",
        ),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _load_bundle_or_exit(slides: Path):
    """The v3 deck bundle: both languages + companions, the identity source."""
    from clm.slides.doc_lenses import DocLensError, load_bundle

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
    return bundle


def _run_pipeline_artifacts(
    ctx,
    slides: Path,
    videos: tuple[str, ...],
    lang: str,
    transcript_override: Path | None,
    alignment_override: Path | None,
    whisper_model: str,
    backend_name: str,
    device: str,
):
    """Run the cached deterministic tier; returns (slide_groups, artifacts)."""
    from clm.cli.commands._video_args import expand_video_args
    from clm.core.slide_text.slide_parser import parse_slides
    from clm.voiceover.cache import CachePolicy
    from clm.voiceover.harvest import HarvestUsageError, run_pipeline
    from clm.voiceover.overrides import (
        OverrideError,
        load_alignment_override,
        load_transcript_override,
    )

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())
    video_paths = expand_video_args(videos)

    # The recorded-language view the OCR matcher and aligner key on.
    slide_groups = parse_slides(slides, lang)

    try:
        transcript = load_transcript_override(transcript_override) if transcript_override else None
        alignment = load_alignment_override(alignment_override) if alignment_override else None
    except OverrideError as exc:
        raise click.UsageError(str(exc)) from exc
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
    return slide_groups, video_paths, artifacts


def _build_report_data(
    ctx,
    bundle,
    slides: Path,
    videos: tuple[str, ...],
    lang: str,
    transcript_override: Path | None,
    alignment_override: Path | None,
    whisper_model: str,
    backend_name: str,
    device: str,
) -> dict:
    from clm.voiceover.harvest import build_report

    slide_groups, video_paths, artifacts = _run_pipeline_artifacts(
        ctx,
        slides,
        videos,
        lang,
        transcript_override,
        alignment_override,
        whisper_model,
        backend_name,
        device,
    )
    return build_report(bundle, slide_groups, artifacts, lang=lang, video_paths=video_paths)


# ---------------------------------------------------------------------------
# report — the read-only default verb
# ---------------------------------------------------------------------------


@harvest_group.command("report")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=str)
@_pipeline_options
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON report envelope.")
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
    from clm.voiceover.harvest import report_exit_code

    bundle = _load_bundle_or_exit(slides)
    report = _build_report_data(
        ctx,
        bundle,
        slides,
        videos,
        lang,
        transcript_override,
        alignment_override,
        whisper_model,
        backend_name,
        device,
    )
    if as_json:
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human_report(report)
    sys.exit(report_exit_code(report))


# ---------------------------------------------------------------------------
# task — frame the judgment for the driving agent (read-only)
# ---------------------------------------------------------------------------


@harvest_group.command("task")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=str)
@_pipeline_options
@click.option(
    "--slide",
    default=None,
    help="Frame one slide (bare id or id:… handle). Omit to frame every actionable item.",
)
@click.option(
    "--kind",
    type=click.Choice(["curate", "translate"]),
    default="curate",
    show_default=True,
    help="curate = merge the recorded language; translate = frame the twin side.",
)
@click.pass_context
def harvest_task_cmd(
    ctx,
    slides: Path,
    videos: tuple[str, ...],
    lang: str,
    slide: str | None,
    kind: str,
    transcript_override: Path | None,
    alignment_override: Path | None,
    whisper_model: str,
    backend_name: str,
    device: str,
):
    """Frame one slide's judgment as a task document (read-only).

    Emits JSON task document(s): caller instructions (the curation rules the
    old embedded-model merge applied), structured inputs (baseline voiceover
    on both sides, the aligned transcript with its revisited groups, slide
    content), the bullet-list `answer_schema`, and the freshness tokens
    (`baseline_fingerprint`, `video_fingerprint`) that `accept` re-checks.

    \b
    Exit codes: 0 tasks emitted (possibly zero in the sweep) · 2 error /
    the named slide cannot be framed.
    """
    from clm.slides.agent_task import envelope
    from clm.voiceover.harvest_task import TaskUnavailable, build_tasks

    bundle = _load_bundle_or_exit(slides)
    report = _build_report_data(
        ctx,
        bundle,
        slides,
        videos,
        lang,
        transcript_override,
        alignment_override,
        whisper_model,
        backend_name,
        device,
    )
    deck = bundle.outcome.deck
    assert deck is not None
    try:
        tasks = build_tasks(report, deck, kind=kind, slide=slide)
    except TaskUnavailable as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    click.echo(
        json.dumps(
            envelope(
                1,
                tool="harvest",
                verb="task",
                body={"video_fingerprint": report["video_fingerprint"], "tasks": tasks},
            ),
            indent=2,
            ensure_ascii=False,
        )
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# accept — the only write path
# ---------------------------------------------------------------------------


@harvest_group.command("accept")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--answer",
    "answer_src",
    required=True,
    help="The answer document: a file path, or '-' for stdin.",
)
@click.option("--slide", default=None, help="Safety check: must match the answer's item.")
@click.option(
    "--record",
    is_flag=True,
    help="Bank the written member into the sync ledger under "
    "harvest:<video-fingerprint> provenance (one-sided trust semantics).",
)
@click.option("--dry-run", is_flag=True, help="Validate and report; write nothing.")
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON outcome envelope.")
def harvest_accept_cmd(
    slides: Path,
    answer_src: str,
    slide: str | None,
    record: bool,
    dry_run: bool,
    as_json: bool,
):
    """Validate a bullet-list answer and write it through the v3 model.

    Validates the answer against the task's schema and the LIVE deck
    (baseline-fingerprint freshness — a concurrent edit rejects, never
    overwrites), renders the bullets into the voiceover cell body, and lands
    the member edit atomically (both halves + companions as needed). A
    one-language answer writes that side only — the pair becomes a
    deliberate divergence the `clm slides sync` loop resolves as translation
    work. Writes nothing on any validation failure.

    \b
    Exit codes: 0 applied (and recorded, if requested) · 1 applied but the
    ledger record was refused by the structural gate · 2 rejected / error.
    """
    from clm.slides.agent_task import (
        EXIT_CLEAN,
        EXIT_ERROR,
        EXIT_WORK_PENDING,
        VALIDATORS,
        rejection_payload,
    )
    from clm.voiceover.harvest_accept import AcceptRejected, Answer, accept_answer
    from clm.voiceover.harvest_task import ANSWER_VALIDATOR

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

    bundle = _load_bundle_or_exit(slides)
    try:
        # The validator label the task documents announce resolves through
        # the shared kit's registry to the parser that judges the answer.
        parsed: Answer = VALIDATORS.get(ANSWER_VALIDATOR)(payload)
        if slide is not None:
            handle = slide if slide.startswith("id:") else f"id:{slide}"
            if handle != parsed.item:
                raise AcceptRejected(
                    f"--slide {handle} does not match the answer's item {parsed.item}"
                )
        outcome = accept_answer(bundle, parsed, record=record, dry_run=dry_run)
    except AcceptRejected as exc:
        if as_json:
            click.echo(
                json.dumps(
                    rejection_payload(1, tool="harvest", verb="accept", reason=str(exc)),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"rejected: {exc}", err=True)
        sys.exit(EXIT_ERROR)

    if as_json:
        click.echo(json.dumps(outcome.to_payload(), indent=2, ensure_ascii=False))
    else:
        state = "dry-run: would write" if dry_run else "wrote"
        touched = ", ".join(
            f"{m['member']}{' (new)' if m['created'] else ''}" for m in outcome.members
        )
        click.echo(f"accepted {outcome.item} → {touched} ({state})")
        for path in outcome.written_paths:
            click.echo(f"  {path}")
        if outcome.recorded:
            click.echo("  ledger: recorded")
        for message in outcome.record_refused:
            click.echo(f"  ledger withheld: {message}", err=True)
    sys.exit(EXIT_WORK_PENDING if outcome.record_refused else EXIT_CLEAN)


# ---------------------------------------------------------------------------
# verify — the structural post-check
# ---------------------------------------------------------------------------


@harvest_group.command("verify")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON verdict.")
def harvest_verify_cmd(slides: Path, as_json: bool):
    """Structural post-check on the pair (v3 lens + the deck-halves write gate).

    Runs the v3 lens gate (the whole bundle must parse back — refusals are
    corruption) plus the **deck-halves-only** structural gate
    (`gate_deck_halves`). One-sided narrative members are NOT failures: a
    harvest write to one language side is a representable pending state the
    `clm slides sync` loop resolves as translation work — they are listed as
    `pending_twin` items. (`sync verify`, and `sync record`/`apply`'s gate,
    project the companions and read that same state as an id-asymmetry error,
    which is exactly the "corruption" misreading harvest must avoid — §6.)

    \b
    Exit codes: 0 pass (pending twins allowed) · 2 structural errors.
    """
    from clm.slides.doc_lenses import DocLensError, load_bundle
    from clm.slides.sync_verify import gate_deck_halves

    try:
        bundle = load_bundle(slides)
    except DocLensError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    errors: list[str] = []
    pending: list[dict] = []
    if bundle.outcome.deck is None:
        refusal = bundle.outcome.refusal
        if refusal is not None:
            errors.extend(f"[{r.code}] {r.detail}" for r in refusal.reasons)
        else:  # pragma: no cover - a refusal always carries reasons
            errors.append("the bundle did not parse")
    else:
        errors.extend(
            f"[{v.kind}] {v.message}"
            # Deck halves only — the §6 exception, see `gate_deck_halves`. The
            # companion-projecting gate the sync verbs use (D8) reads a
            # one-sided narrative member as an id-asymmetry error, which is
            # exactly the "corruption" misreading harvest must avoid.
            for v in gate_deck_halves(
                bundle.de_path,
                bundle.en_path,
                bundle.comment_token,
            )
        )
        for member in bundle.outcome.deck.members():
            if member.role in ("voiceover", "notes") and member.is_one_sided:
                present = "de" if member.de is not None else "en"
                pending.append(
                    {
                        "member": member.key.render(),
                        "present": present,
                        "missing": "en" if present == "de" else "de",
                    }
                )

    ok = not errors
    if as_json:
        click.echo(
            json.dumps(
                {
                    "schema": 1,
                    "tool": "harvest",
                    "verb": "verify",
                    "de": str(bundle.de_path),
                    "en": str(bundle.en_path),
                    "ok": ok,
                    "errors": errors,
                    "pending_twins": pending,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        click.echo(f"{'PASS' if ok else 'FAIL'}  {bundle.de_path} / {bundle.en_path}")
        for message in errors:
            click.echo(f"  [error] {message}")
        for item in pending:
            click.echo(
                f"  [pending] {item['member']}: {item['missing']} twin awaits "
                "translation (run the `clm slides sync` loop)"
            )
    sys.exit(0 if ok else 2)


# ---------------------------------------------------------------------------
# align — reviewable pipeline alignment (#960)
# ---------------------------------------------------------------------------


@harvest_group.group("align")
def harvest_align_group():
    """Review and correct the pipeline's heuristic alignment decisions.

    \b
    report   frame uncertain segment assignments / slide matches (read-only)
    accept   apply segment reassignments; writes a full alignment file
             for `--alignment` on the next report/task run
    """


@harvest_align_group.command("report")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=str)
@_pipeline_options
@click.pass_context
def harvest_align_report_cmd(
    ctx,
    slides: Path,
    videos: tuple[str, ...],
    lang: str,
    transcript_override: Path | None,
    alignment_override: Path | None,
    whisper_model: str,
    backend_name: str,
    device: str,
):
    """Frame the pipeline's uncertain alignment decisions as items (read-only).

    Surfaces what the heuristics hid: boundary-straddling transcript
    segments (with overlap fractions and the runner-up slide), unassigned
    segments with their reason, and OCR slide matches that were weak or
    overruled by the sequential constraint. Each item carries the evidence;
    answer segment assignments with `harvest align accept`.

    \b
    Exit codes: 0 no uncertain items · 1 items framed · 2 error.
    """
    from clm.slides.agent_task import EXIT_CLEAN, EXIT_WORK_PENDING
    from clm.voiceover.harvest_align import build_align_report

    slide_groups, video_paths, artifacts = _run_pipeline_artifacts(
        ctx,
        slides,
        videos,
        lang,
        transcript_override,
        alignment_override,
        whisper_model,
        backend_name,
        device,
    )
    report = build_align_report(artifacts, slide_groups, video_paths)
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(EXIT_WORK_PENDING if report["items"] else EXIT_CLEAN)


@harvest_align_group.command("accept")
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=str)
@_pipeline_options
@click.option(
    "--answer",
    "answer_src",
    required=True,
    help="The align answer document: a file path, or '-' for stdin.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write the corrected alignment (default: "
    "<SLIDES-stem>.alignment.json beside the deck). Load it with "
    "`--alignment` on the next report/task run.",
)
@click.option("--dry-run", is_flag=True, help="Validate and report; write nothing.")
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON outcome envelope.")
@click.pass_context
def harvest_align_accept_cmd(
    ctx,
    slides: Path,
    videos: tuple[str, ...],
    lang: str,
    answer_src: str,
    output_path: Path | None,
    dry_run: bool,
    as_json: bool,
    transcript_override: Path | None,
    alignment_override: Path | None,
    whisper_model: str,
    backend_name: str,
    device: str,
):
    """Validate segment reassignments and write a corrected alignment file.

    The answer echoes the report's freshness tokens (`video_fingerprint`,
    `alignment_fingerprint`) — a stale answer is rejected, never merged.
    The result is a full alignment document: pass it via `--alignment` to
    `report`/`task` to build on it (iteration works).

    \b
    Exit codes: 0 written (or dry-run valid) · 2 rejected / error.
    """
    from clm.slides.agent_task import (
        EXIT_CLEAN,
        EXIT_ERROR,
        VALIDATORS,
        rejection_payload,
    )
    from clm.voiceover.harvest_align import (
        ALIGN_ANSWER_VALIDATOR,
        AlignAnswer,
        AlignRejected,
        alignment_fingerprint,
        apply_reassignments,
    )

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

    slide_groups, video_paths, artifacts = _run_pipeline_artifacts(
        ctx,
        slides,
        videos,
        lang,
        transcript_override,
        alignment_override,
        whisper_model,
        backend_name,
        device,
    )
    try:
        answer: AlignAnswer = VALIDATORS.get(ALIGN_ANSWER_VALIDATOR)(payload)
        from clm.voiceover.harvest import video_fingerprint

        current_video_fp = video_fingerprint(video_paths)
        if answer.video_fingerprint != current_video_fp:
            raise AlignRejected(
                f"the answer's video_fingerprint does not match the videos "
                f"({current_video_fp}) — re-run `harvest align report`"
            )
        current_fp = alignment_fingerprint(artifacts.alignment)
        if answer.alignment_fingerprint != current_fp:
            raise AlignRejected(
                "the alignment changed since the report was framed "
                "(alignment_fingerprint mismatch) — re-run `harvest align report`"
            )
        header_indices = {sg.index for sg in slide_groups if sg.slide_type == "header"}
        corrected = apply_reassignments(
            artifacts.alignment,
            answer,
            valid_slides={sg.index for sg in slide_groups},
            header_indices=header_indices,
        )
    except AlignRejected as exc:
        if as_json:
            click.echo(
                json.dumps(
                    rejection_payload(1, tool="harvest", verb="align-accept", reason=str(exc)),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"rejected: {exc}", err=True)
        sys.exit(EXIT_ERROR)

    out = output_path or slides.with_suffix(".alignment.json")
    if as_json:
        click.echo(
            json.dumps(
                {
                    "schema": 1,
                    "tool": "harvest",
                    "verb": "align-accept",
                    "applied": True,
                    "reassignments": len(answer.reassignments),
                    "written": None if dry_run else str(out),
                    "dry_run": dry_run,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        click.echo(
            f"{'dry-run: would write' if dry_run else 'wrote'} {out} "
            f"({len(answer.reassignments)} reassignment(s)) — load it with "
            "`--alignment` on the next report/task run"
        )
    if not dry_run:
        from clm.voiceover.cache import _encode_alignment

        out.write_text(
            json.dumps(_encode_alignment(corrected), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    sys.exit(EXIT_CLEAN)


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
# The video-side verbs (implementations live in clm.cli.commands.voiceover —
# the module keeps the code; this group is their only registration since the
# Phase-4 cutover, no aliases)
# ---------------------------------------------------------------------------


def _register_video_verbs() -> None:
    from clm.cli.commands.voiceover import (
        backfill_cmd,
        cache_group,
        compare_cmd,
        compare_from_inventory_cmd,
        debug_group,
        detect,
        extract_training_data,
        identify,
        identify_rev_cmd,
        port_voiceover_cmd,
        report_cmd,
        sync,
        sync_at_rev_cmd,
        trace_group,
        transcribe,
    )

    for command in (
        sync,  # @click.command("autopilot") — the embedded-model one-shot
        transcribe,
        detect,
        identify,
        identify_rev_cmd,
        sync_at_rev_cmd,
        port_voiceover_cmd,
        compare_cmd,
        report_cmd,  # @click.command("compare-report")
        compare_from_inventory_cmd,
        backfill_cmd,
        extract_training_data,
        cache_group,
        trace_group,
        debug_group,
    ):
        harvest_group.add_command(command)


_register_video_verbs()
