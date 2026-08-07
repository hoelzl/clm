"""Merge/propagation flow behind ``clm harvest autopilot``.

This is the apply stage of the legacy all-in-one autopilot path: it takes
the per-slide transcript text produced by the deterministic tier, merges it
into the existing narration baseline with an embedded LLM call, optionally
propagates source-language changes to the other language, and writes the
result to the deck or its voiceover companion.

Extracted from ``clm.cli.commands.voiceover`` (Phase 8 A5, issue #802). The
Click command keeps flag parsing and converts :class:`MissingSlideIdError`
into a usage error; everything below the flag layer lives here.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


class MissingSlideIdError(ValueError):
    """Companion/propagation mode needs stable slide_ids that are absent."""


def require_slide_ids(
    slide_groups: list,
    notes_map: dict[int, str],
    slides: Path,
) -> dict[int, str]:
    """Return ``slide_index -> slide_id`` for every slide touched by the merge.

    Raises :class:`MissingSlideIdError` if any slide in ``notes_map`` lacks a
    stable ``slide_id`` on its slide-start cell — companion mode requires
    them so writes can round-trip through ``for_slide`` metadata.
    """
    slide_id_by_idx: dict[int, str] = {}
    missing: list[int] = []
    for sg in slide_groups:
        if sg.index not in notes_map:
            continue
        slide_id = sg.cells[0].metadata.slide_id if sg.cells else None
        if not slide_id:
            missing.append(sg.index)
        else:
            slide_id_by_idx[sg.index] = slide_id

    if missing:
        missing_list = ", ".join(str(i) for i in missing[:10])
        more = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
        raise MissingSlideIdError(
            "Companion mode requires a stable slide_id on every slide being "
            f"merged, but slides [{missing_list}]{more} have none.\n"
            f"Fix: run `clm voiceover extract {slides}` "
            "(which auto-generates slide_ids), or pass --no-companion to "
            "merge into the slide file directly."
        )
    return slide_id_by_idx


async def merge_notes(
    *,
    slides: Path,
    notes_map: dict[int, str],
    slide_groups: list,
    lang: str,
    tag: str,
    model: str | None,
    dry_run: bool,
    output: Path | None,
    multi_part: bool,
    alignment,
    use_companion: bool = False,
    companion_file: Path | None = None,
    propagate_to: str | None = None,
) -> None:
    """Merge transcript into existing voiceover cells."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from clm.infrastructure.llm.client import (
        flush_langfuse,
        langfuse_configured,
    )
    from clm.notebooks.slide_writer import write_narrative
    from clm.slides.voiceover_tools import (
        read_companion_baselines,
        update_companion_narrative,
    )
    from clm.voiceover.merge import (
        DEFAULT_MERGE_MODEL,
        MergeResult,
        PropagationResult,
        SlideInput,
        build_batches,
        merge_batch,
    )
    from clm.voiceover.trace_log import TraceLog

    merge_model = model or DEFAULT_MERGE_MODEL

    # Companion mode: require slide_ids up front and read baselines from companion.
    # Propagation also requires slide_ids (to match source slides to their
    # target-language counterparts); compute the mapping once if either is active.
    slide_id_by_idx: dict[int, str] = {}
    companion_baselines: dict[str, str] = {}
    if use_companion or propagate_to:
        slide_id_by_idx = require_slide_ids(slide_groups, notes_map, slides)
    if use_companion:
        assert companion_file is not None
        companion_baselines = read_companion_baselines(companion_file, lang, tag=tag)
        console.print(f"[bold]Companion mode:[/bold] baseline read from {companion_file.name}")

    # Read existing voiceover baseline per slide from the parsed slide groups
    slide_inputs: list[SlideInput] = []
    for idx in sorted(notes_map.keys()):
        transcript_text = notes_map[idx]

        # Find the slide group and read its existing notes
        baseline = ""
        slide_content = ""
        for sg in slide_groups:
            if sg.index == idx:
                slide_content = sg.text_content
                # Read existing notes matching the target tag
                if use_companion:
                    baseline = companion_baselines.get(slide_id_by_idx[idx], "")
                else:
                    baseline = _extract_baseline(sg, tag)
                break

        # Detect boundary hint: slide has segments from multiple parts
        boundary_hint = False
        if multi_part and idx in alignment.slide_notes:
            boundary_hint = _has_boundary(alignment, idx)

        slide_id = f"{slides.stem}/{idx}"

        # Skip slides where both baseline and transcript are empty
        if not baseline.strip() and not transcript_text.strip():
            continue

        slide_inputs.append(
            SlideInput(
                slide_id=slide_id,
                baseline=baseline,
                transcript=transcript_text,
                slide_content=slide_content,
                boundary_hint=boundary_hint,
            )
        )

    if not slide_inputs:
        console.print("[yellow]No slides to merge.[/yellow]")
        return

    # Create trace log
    trace = TraceLog.create(slides.name, base_dir=slides.parent)

    # Build batches and run merge
    batches = build_batches(slide_inputs)
    console.print(
        f"[bold]Merging {len(slide_inputs)} slides "
        f"({len(batches)} batch{'es' if len(batches) != 1 else ''}) "
        f"with {merge_model}...[/bold]"
    )

    # Langfuse session context (shared across all batches in this invocation)
    use_langfuse = langfuse_configured()
    session_id = (
        f"voiceover-sync-{slides.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )
    git_user = _get_git_user_name() if use_langfuse else None

    all_results: list[MergeResult] = []
    source_trace_id_by_slide_id: dict[str, str] = {}
    for batch_idx, batch in enumerate(batches):
        if len(batches) > 1:
            console.print(
                f"  Batch {batch_idx + 1}/{len(batches)} "
                f"({len(batch)} slide{'s' if len(batch) != 1 else ''})..."
            )

        # Build per-batch Langfuse context
        langfuse_ctx = None
        trace_id = None
        if use_langfuse:
            trace_id = str(uuid4())
            langfuse_ctx = {
                "name": "voiceover_merge_batch",
                "trace_id": trace_id,
                "metadata": {
                    "langfuse_session_id": session_id,
                    "langfuse_tags": ["voiceover-sync", lang, "merge"],
                    "langfuse_user_id": git_user,
                    "langfuse_metadata": {
                        "slide_ids": [s.slide_id for s in batch],
                        "language": lang,
                        "topic": slides.stem,
                        "batch_char_count": sum(
                            len(s.baseline) + len(s.transcript) + len(s.slide_content)
                            for s in batch
                        ),
                    },
                },
            }

        results = await merge_batch(
            batch,
            language=lang,
            model=merge_model,
            langfuse_context=langfuse_ctx,
        )
        all_results.extend(results)

        # Log each result to the trace log
        for slide_input, result in zip(batch, results, strict=True):
            trace.log_merge_call(
                slide_id=result.slide_id,
                language=lang,
                baseline=slide_input.baseline,
                transcript=slide_input.transcript,
                llm_merged=result.merged_bullets,
                rewrites=result.rewrites,
                dropped_from_transcript=result.dropped_from_transcript,
                langfuse_trace_id=trace_id,
            )
            if trace_id:
                source_trace_id_by_slide_id[result.slide_id] = trace_id

    # Build merged notes_map from results
    merged_map: dict[int, str] = {}
    rewrite_count = 0
    source_baseline_by_slide_id: dict[str, str] = {}
    source_rewrites_by_slide_id: dict[str, list[dict]] = {}
    for slide_input in slide_inputs:
        source_baseline_by_slide_id[slide_input.slide_id] = slide_input.baseline
    for result in all_results:
        # Extract slide index from slide_id (format: "stem/idx")
        try:
            idx = int(result.slide_id.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            logger.warning("Cannot parse slide index from %s", result.slide_id)
            continue
        if result.merged_bullets.strip():
            merged_map[idx] = result.merged_bullets
        if result.rewrites:
            rewrite_count += len(result.rewrites)
            source_rewrites_by_slide_id[result.slide_id] = result.rewrites

    # Display results
    _display_merge_summary(all_results, slide_groups)

    if rewrite_count:
        console.print(
            f"\n[yellow]Warning: {rewrite_count} baseline rewrite(s) detected. "
            f"Review the diff carefully.[/yellow]"
        )

    # Cross-language propagation (Item 2)
    propagation_results: list[PropagationResult] = []
    target_merged_map: dict[int, str] = {}
    target_merged_by_slide_id: dict[str, str] = {}
    target_slide_groups: list = []
    if propagate_to:
        (
            propagation_results,
            target_merged_map,
            target_merged_by_slide_id,
            target_slide_groups,
        ) = await _run_propagation(
            slides=slides,
            slide_groups=slide_groups,
            slide_id_by_idx=slide_id_by_idx,
            all_results=all_results,
            source_baseline_by_slide_id=source_baseline_by_slide_id,
            source_rewrites_by_slide_id=source_rewrites_by_slide_id,
            source_lang=lang,
            target_lang=propagate_to,
            tag=tag,
            use_companion=use_companion,
            companion_file=companion_file,
            companion_baselines_source=companion_baselines,
            model=merge_model,
            trace=trace,
            source_trace_id_by_slide_id=source_trace_id_by_slide_id,
            session_id=session_id,
            git_user=git_user,
            use_langfuse=use_langfuse,
        )

    # Flush Langfuse traces before exiting (best-effort)
    if use_langfuse:
        flush_langfuse()

    if dry_run:
        # Emit unified diff scoped to the file that would be written.
        if use_companion:
            assert companion_file is not None
            merged_by_slide_id = {
                slide_id_by_idx[i]: t for i, t in merged_map.items() if i in slide_id_by_idx
            }
            _emit_companion_dry_run_diff(companion_file, merged_by_slide_id, lang, tag, all_results)
        else:
            _emit_dry_run_diff(slides, merged_map, lang, tag, all_results)
        if propagate_to:
            console.print(f"\n[bold]Propagation diff ({lang} -> {propagate_to}):[/bold]")
            if use_companion:
                assert companion_file is not None
                _emit_companion_dry_run_diff(
                    companion_file,
                    target_merged_by_slide_id,
                    propagate_to,
                    tag,
                    [],
                )
            else:
                _emit_dry_run_diff(slides, target_merged_map, propagate_to, tag, [])
            _warn_propagation_overreach(propagation_results)
        console.print(f"\n[dim]Trace log: {trace.path}[/dim]")
        console.print("[yellow]Dry run — no changes written.[/yellow]")
        return

    # Write merged cells (source language)
    if use_companion:
        assert companion_file is not None
        merged_by_slide_id = {
            slide_id_by_idx[i]: t for i, t in merged_map.items() if i in slide_id_by_idx
        }
        dest = update_companion_narrative(companion_file, merged_by_slide_id, lang, tag=tag)
    else:
        dest = write_narrative(slides, merged_map, lang, tag=tag, output_path=output)
    console.print(f"\n[dim]Trace log: {trace.path}[/dim]")
    console.print(f"[green]{tag.capitalize()} cells written to {dest}[/green]")

    # Write propagated cells (target language) via the same routing
    if propagate_to and (target_merged_map or target_merged_by_slide_id):
        if use_companion:
            assert companion_file is not None
            target_dest = update_companion_narrative(
                companion_file, target_merged_by_slide_id, propagate_to, tag=tag
            )
        else:
            target_dest = write_narrative(
                slides, target_merged_map, propagate_to, tag=tag, output_path=output
            )
        console.print(
            f"[green]Propagated {tag} cells ({propagate_to}) written to {target_dest}[/green]"
        )
        _warn_propagation_overreach(propagation_results)


async def _run_propagation(
    *,
    slides: Path,
    slide_groups: list,
    slide_id_by_idx: dict[int, str],
    all_results: list,
    source_baseline_by_slide_id: dict[str, str],
    source_rewrites_by_slide_id: dict[str, list[dict]],
    source_lang: str,
    target_lang: str,
    tag: str,
    use_companion: bool,
    companion_file: Path | None,
    companion_baselines_source: dict[str, str],
    model: str,
    trace,
    source_trace_id_by_slide_id: dict[str, str],
    session_id: str,
    git_user: str | None,
    use_langfuse: bool,
) -> tuple[list, dict[int, str], dict[str, str], list]:
    """Run cross-language propagation for slides whose source merge changed.

    Returns a 4-tuple of:

    * list of PropagationResult (one per slide that was propagated);
    * target_merged_map: slide_index in target-language parsing → translated text;
    * target_merged_by_slide_id: slide_id → translated text (for companion writes);
    * target_slide_groups: the parsed target-language slide groups (for display).
    """
    from uuid import uuid4

    from clm.core.slide_text.slide_parser import SlideGroup, parse_slides
    from clm.slides.voiceover_tools import read_companion_baselines
    from clm.voiceover.merge import (
        PropagationInput,
        PropagationResult,
        build_propagation_batches,
        propagate_batch,
    )

    target_slide_groups = parse_slides(slides, target_lang)
    target_sg_by_slide_id: dict[str, SlideGroup] = {}
    for sg in target_slide_groups:
        sid = sg.cells[0].metadata.slide_id if sg.cells else None
        if sid:
            target_sg_by_slide_id[sid] = sg

    if use_companion:
        assert companion_file is not None
        target_companion_baselines = read_companion_baselines(companion_file, target_lang, tag=tag)
    else:
        target_companion_baselines = {}

    # Build propagation inputs only for slides with a meaningful source change.
    prop_inputs: list[PropagationInput] = []
    monolingual_skips: list[str] = []
    noop_skips: list[str] = []
    for result in all_results:
        source_slide_id = result.slide_id
        source_baseline = source_baseline_by_slide_id.get(source_slide_id, "")
        merged = result.merged_bullets
        if not merged.strip():
            noop_skips.append(source_slide_id)
            continue
        if merged.strip() == source_baseline.strip():
            noop_skips.append(source_slide_id)
            continue

        try:
            src_idx = int(source_slide_id.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            continue

        stable_id = slide_id_by_idx.get(src_idx)
        if not stable_id:
            continue

        target_sg = target_sg_by_slide_id.get(stable_id)
        if target_sg is None:
            monolingual_skips.append(stable_id)
            continue

        if use_companion:
            target_baseline = target_companion_baselines.get(stable_id, "")
        else:
            target_baseline = _extract_baseline(target_sg, tag)

        slide_content = ""
        for sg in slide_groups:
            if sg.index == src_idx:
                slide_content = sg.text_content
                break

        prop_inputs.append(
            PropagationInput(
                slide_id=source_slide_id,
                source_baseline=source_baseline,
                source_merged=merged,
                source_rewrites=source_rewrites_by_slide_id.get(source_slide_id, []),
                target_baseline=target_baseline,
                slide_content=slide_content,
                source_lang=source_lang,
                target_lang=target_lang,
            )
        )

    if monolingual_skips:
        logger.info(
            "Skipping propagation for %d monolingual slide(s) (no %s variant): %s",
            len(monolingual_skips),
            target_lang,
            ", ".join(monolingual_skips[:5]) + ("..." if len(monolingual_skips) > 5 else ""),
        )

    if not prop_inputs:
        console.print(
            f"[dim]Propagation ({source_lang} -> {target_lang}): nothing to propagate "
            f"({len(noop_skips)} slide(s) had no source-side change, "
            f"{len(monolingual_skips)} monolingual).[/dim]"
        )
        return [], {}, {}, target_slide_groups

    batches = build_propagation_batches(prop_inputs)
    console.print(
        f"[bold]Propagating {len(prop_inputs)} slide(s) "
        f"({source_lang} -> {target_lang}, "
        f"{len(batches)} batch{'es' if len(batches) != 1 else ''}) "
        f"with {model}...[/bold]"
    )

    propagation_results: list[PropagationResult] = []
    for batch_idx, batch in enumerate(batches):
        if len(batches) > 1:
            console.print(f"  Batch {batch_idx + 1}/{len(batches)} ({len(batch)} slide(s))...")

        langfuse_ctx = None
        trace_id = None
        if use_langfuse:
            trace_id = str(uuid4())
            langfuse_ctx = {
                "name": "voiceover_propagate_batch",
                "trace_id": trace_id,
                "metadata": {
                    "langfuse_session_id": session_id,
                    "langfuse_tags": [
                        "voiceover-sync",
                        "propagate",
                        source_lang,
                        target_lang,
                    ],
                    "langfuse_user_id": git_user,
                    "langfuse_metadata": {
                        "slide_ids": [s.slide_id for s in batch],
                        "source_language": source_lang,
                        "target_language": target_lang,
                        "topic": slides.stem,
                    },
                },
            }

        results = await propagate_batch(
            batch,
            model=model,
            langfuse_context=langfuse_ctx,
        )
        propagation_results.extend(results)

        for prop_input, result in zip(batch, results, strict=True):
            trace.log_propagate_call(
                slide_id=result.slide_id,
                source_language=source_lang,
                target_language=target_lang,
                source_baseline=prop_input.source_baseline,
                source_merged=prop_input.source_merged,
                target_baseline=prop_input.target_baseline,
                target_translated=result.translated_bullets,
                corresponded_changes=result.corresponded_changes,
                target_preserved_unchanged=result.target_preserved_unchanged,
                source_trace_id=source_trace_id_by_slide_id.get(result.slide_id),
                langfuse_trace_id=trace_id,
            )

    # Build write-ready maps
    target_merged_map: dict[int, str] = {}
    target_merged_by_slide_id: dict[str, str] = {}
    for result in propagation_results:
        translated = result.translated_bullets
        if not translated.strip():
            continue
        src_slide_id = result.slide_id
        try:
            src_idx = int(src_slide_id.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            continue
        stable_id = slide_id_by_idx.get(src_idx)
        if not stable_id:
            continue
        target_merged_by_slide_id[stable_id] = translated
        target_sg = target_sg_by_slide_id.get(stable_id)
        if target_sg is not None:
            target_merged_map[target_sg.index] = translated

    return propagation_results, target_merged_map, target_merged_by_slide_id, target_slide_groups


def _warn_propagation_overreach(results: list) -> None:
    """Print a warning for every propagation result that claims it rewrote
    target bullets without a direct source counterpart."""
    for result in results:
        if getattr(result, "target_preserved_unchanged", True):
            continue
        slide_id = getattr(result, "slide_id", "?")
        console.print(
            f"[yellow]  Warning: propagation for {slide_id} reported "
            f"target_preserved_unchanged=false — review the target diff.[/yellow]"
        )


def _extract_baseline(slide_group, tag: str) -> str:
    """Extract existing voiceover/notes text for the given tag from a slide group."""
    parts = []
    for cell in slide_group.notes_cells:
        # Only include cells matching the target tag
        if tag in cell.metadata.tags:
            text = cell.text_content()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _has_boundary(alignment, slide_idx: int) -> bool:
    """Check if a slide has transcript segments from multiple video parts.

    Conservative heuristic: in multi-part mode, always returns True.
    This means the merge prompt is extra suspicious of greeting/sign-off
    noise near all slides, which is the safe default.
    """
    if slide_idx not in alignment.slide_notes:
        return False
    return True


def _display_merge_summary(results: list, slide_groups: list):
    """Display a summary table of merge results."""
    table = Table(title="Merge Results")
    table.add_column("Slide", style="cyan")
    table.add_column("Title")
    table.add_column("Length", style="green")
    table.add_column("Rewrites", style="yellow")
    table.add_column("Preview")

    for result in results:
        try:
            idx = int(result.slide_id.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            idx = -1

        title = ""
        for sg in slide_groups:
            if sg.index == idx:
                title = sg.title[:30]
                break

        text = result.merged_bullets
        preview = text[:50].replace("\n", " ") + ("..." if len(text) > 50 else "")
        rewrites_str = str(len(result.rewrites)) if result.rewrites else ""

        table.add_row(
            str(idx),
            title,
            f"{len(text)} chars",
            rewrites_str,
            preview,
        )

    console.print(table)


def _emit_dry_run_diff(
    slides: Path,
    merged_map: dict[int, str],
    lang: str,
    tag: str,
    results: list,
):
    """Emit a unified diff of baseline -> merged for dry-run mode."""
    from clm.notebooks.slide_writer import update_narrative

    original_text = slides.read_text(encoding="utf-8")
    updated_text = update_narrative(original_text, merged_map, lang, tag=tag)

    _print_diff_and_rewrite_warnings(original_text, updated_text, slides.name, results)


def _emit_companion_dry_run_diff(
    companion_file: Path,
    merged_by_slide_id: dict[str, str],
    lang: str,
    tag: str,
    results: list,
):
    """Emit a unified diff scoped to the companion file for dry-run mode."""
    from clm.core.utils.prog_lang_utils import comment_token_for_path
    from clm.slides.voiceover_tools import render_companion_update

    original_text = companion_file.read_text(encoding="utf-8") if companion_file.exists() else ""
    updated_text = render_companion_update(
        original_text,
        merged_by_slide_id,
        lang,
        tag=tag,
        comment_token=comment_token_for_path(companion_file),
    )

    _print_diff_and_rewrite_warnings(original_text, updated_text, companion_file.name, results)


def _print_diff_and_rewrite_warnings(
    original_text: str,
    updated_text: str,
    filename: str,
    results: list,
):
    """Print a unified diff and any rewrite warnings (shared by both dry-run helpers)."""
    if original_text == updated_text:
        console.print("\n[dim]No changes — merged output matches baseline.[/dim]")
        return

    diff = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        updated_text.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )

    console.print()
    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(line)

    # Annotate rewrites
    for result in results:
        if result.rewrites:
            try:
                idx = int(result.slide_id.rsplit("/", 1)[-1])
            except (ValueError, IndexError):
                idx = -1
            for rw in result.rewrites:
                console.print(
                    f"[yellow]  Warning: slide {idx}: baseline rewrite: "
                    f"{rw.get('original', '?')} -> {rw.get('revised', '?')}[/yellow]"
                )


async def polish_notes(
    notes_map: dict[int, str],
    slide_groups: list,
    *,
    model: str | None = None,
    lang: str = "de",
    polish_level=None,
) -> dict[int, str]:
    """Polish all notes via LLM."""
    from clm.notebooks.polish import polish_text
    from clm.notebooks.polish_levels import PolishLevel

    kwargs: dict = {}
    if model:
        kwargs["model"] = model
    if polish_level is not None:
        kwargs["polish_level"] = polish_level
    else:
        kwargs["polish_level"] = PolishLevel.standard

    polished: dict[int, str] = {}
    for idx, text in notes_map.items():
        # Find slide content for context
        slide_content = ""
        for sg in slide_groups:
            if sg.index == idx:
                slide_content = sg.text_content
                break

        console.print(f"  Polishing slide {idx}...")
        polished[idx] = await polish_text(text, slide_content, **kwargs)

    return polished


def _get_git_user_name() -> str | None:
    """Return the git user.name, or None if unavailable."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def display_notes_summary(notes_map: dict[int, str], slide_groups: list):
    """Display a summary table of generated notes."""
    table = Table(title="Generated Notes")
    table.add_column("Slide", style="cyan")
    table.add_column("Title")
    table.add_column("Length", style="green")
    table.add_column("Preview")

    for idx in sorted(notes_map.keys()):
        text = notes_map[idx]
        title = ""
        for sg in slide_groups:
            if sg.index == idx:
                title = sg.title[:30]
                break
        preview = text[:60].replace("\n", " ") + ("..." if len(text) > 60 else "")
        table.add_row(str(idx), title, f"{len(text)} chars", preview)

    console.print(table)
