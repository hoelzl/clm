"""The read-only harvest report engine (#546 Phase 2).

``clm harvest report`` = the deterministic tier of the video→voiceover
pipeline (transcribe → transition detect → OCR match → align, cached via
:mod:`clm.voiceover.cache`) joined with the v3 deck model: every slide of
the recorded-language deck becomes one report item keyed by its
:class:`~clm.slides.bilingual_doc.MemberKey`, carrying the video's
transcript segment(s), the existing voiceover baseline on **both** language
sides (inline or companion), and a *structural* novelty class. No model, no
key, no writes — this is the input a driving agent (or a human dry-run)
reads before deciding what to curate.

Novelty classification is purely structural (proposal §8 / epic #546
decision 6): it combines only *VO present/absent on the recorded side* with
*transcript speech assigned/unassigned* — never textual similarity:

* ``no_existing_vo`` — speech was assigned to the slide and the recorded
  side has no voiceover yet: harvest material, nothing to merge into.
* ``transcript_adds_material`` — speech assigned AND a voiceover exists:
  candidate additions; whether the speech truly adds anything is wholly
  the agent's judgment.
* ``covered`` — a voiceover exists and the recording contributed no
  speech for this slide.
* ``unmatched_slide`` — no voiceover and no speech (the slide was shown
  silently, or never shown).
* ``unmatched_speech`` — transcript segments the aligner could not assign
  to any slide; reported per segment, not per slide.

The identity seam: the deterministic stages key everything by positional
``SlideGroup.index`` (the OCR matcher and the aligner know nothing about
ids); this module maps each index to the slide's ``slide_id`` and renders
the v3 handle ``id:<slide_id>``. A slide without an id gets a ``null`` key
and a normalize hint — never a guessed identity.

This module is engine-only: it emits, it never invokes a model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clm.core.slide_text.slide_parser import SlideGroup
    from clm.slides.bilingual_doc import Lang, Member
    from clm.slides.doc_lenses import LoadedBundle
    from clm.voiceover.aligner import AlignmentResult
    from clm.voiceover.cache import CachePolicy
    from clm.voiceover.matcher import TimelineEntry
    from clm.voiceover.transcribe import Transcript

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIONABLE_CLASSES",
    "NOVELTY_CLASSES",
    "HarvestUsageError",
    "PipelineArtifacts",
    "build_report",
    "classify_slide",
    "report_exit_code",
    "run_pipeline",
    "video_fingerprint",
]

#: The closed per-slide vocabulary (``unmatched_speech`` is per segment).
NOVELTY_CLASSES = (
    "no_existing_vo",
    "transcript_adds_material",
    "covered",
    "unmatched_slide",
)

#: Classes that mean there is harvest work for an agent to judge.
ACTIONABLE_CLASSES = frozenset({"no_existing_vo", "transcript_adds_material"})

#: The narrative member roles of the v3 model (voiceover/notes cells).
_NARRATIVE_ROLES = ("voiceover", "notes")

_SIDES: tuple[Lang, Lang] = ("de", "en")


class HarvestUsageError(ValueError):
    """A caller-input problem (the CLI renders it as a usage error)."""


@dataclass(frozen=True)
class PipelineArtifacts:
    """What the deterministic tier produced for one report run.

    ``timeline`` is ``None`` when an alignment override skipped the
    match stage; ``transcript_language`` is ``None`` in the same case.
    """

    alignment: AlignmentResult
    timeline: list[TimelineEntry] | None
    transcript_language: str | None


def video_fingerprint(video_paths: list[Path]) -> str:
    """The fingerprint keying the artifact cache — and the ledger provenance
    ``harvest:<fingerprint>`` (proposal §6).

    Single video: the cache's :class:`~clm.voiceover.cache.VideoKey` hash
    (path + mtime + size). Multiple parts: the composite
    :class:`~clm.voiceover.cache.MultiVideoKey` hash over the ordered
    per-part hashes — the same key the timeline/alignment caches use, so
    cache identity and provenance stay one thing.
    """
    from clm.voiceover.cache import video_key_for

    return video_key_for(video_paths).hash


def run_pipeline(
    slides_path: Path,
    video_paths: list[Path],
    lang: str,
    slide_groups: list[SlideGroup],
    *,
    policy: CachePolicy,
    backend_name: str = "faster-whisper",
    whisper_model: str = "large-v3",
    device: str = "auto",
    transcript_override: Transcript | None = None,
    alignment_override: AlignmentResult | None = None,
) -> PipelineArtifacts:
    """Run the deterministic tier (cached) up to the alignment.

    Per part: transcribe + detect (cached per part), offset and merge; then
    OCR match and align, cached under the composite multi-part key when the
    recording has several parts. An ``alignment_override`` short-circuits
    everything (it encodes the final stitched alignment, so it applies to
    multi-part recordings too); a ``transcript_override`` skips only ASR
    and stays single-video only (it encodes one part's ASR output).
    """
    from clm.voiceover.aligner import align_transcript
    from clm.voiceover.asr_cleanup import clean_transcript
    from clm.voiceover.cache import (
        cached_alignment,
        cached_detect,
        cached_timeline,
        cached_transcribe,
    )
    from clm.voiceover.keyframes import TransitionEvent, detect_transitions
    from clm.voiceover.matcher import match_events_to_slides
    from clm.voiceover.timeline import (
        build_parts,
        merge_transcripts,
        offset_events,
        offset_transcript,
    )
    from clm.voiceover.transcribe import transcribe_video

    multi_part = len(video_paths) > 1
    if alignment_override is not None:
        # The override is the final per-slide alignment — it already encodes
        # the stitched timeline, so it applies to multi-part recordings too.
        return PipelineArtifacts(
            alignment=alignment_override, timeline=None, transcript_language=None
        )
    if transcript_override is not None and multi_part:
        raise HarvestUsageError(
            "--transcript is incompatible with multi-part videos; "
            "supply a precomputed single-part transcript only."
        )

    parts = build_parts(video_paths)
    total_duration = sum(p.duration for p in parts)

    all_transcripts = []
    all_events: list[TransitionEvent] = []
    for part in parts:
        if transcript_override is not None:
            transcript = transcript_override
        else:

            def _do_transcribe(part=part):
                return transcribe_video(
                    part.path,
                    language=lang,
                    backend_name=backend_name,
                    model_size=whisper_model,
                    device=device,
                )

            transcript, tx_hit = cached_transcribe(
                part.path,
                policy=policy,
                base_dir=slides_path.parent,
                transcribe_fn=_do_transcribe,
                backend_name=backend_name,
                model_size=whisper_model,
                language=lang,
                device=device,
            )
            logger.info("transcript for %s: cache %s", part.path.name, "hit" if tx_hit else "miss")
        # Read-time clean-up (#1105): applies to fresh, cached, and injected
        # transcripts alike; the transcript cache keeps the raw ASR output.
        transcript = clean_transcript(transcript)

        def _do_detect(part=part):
            return detect_transitions(part.path)[0]

        events, det_hit = cached_detect(
            part.path,
            policy=policy,
            base_dir=slides_path.parent,
            detect_fn=_do_detect,
        )
        logger.info("transitions for %s: cache %s", part.path.name, "hit" if det_hit else "miss")

        all_transcripts.append(offset_transcript(transcript, part))
        all_events.extend(offset_events(events, part))

    merged_transcript = merge_transcripts(all_transcripts)

    def _run_match():
        return match_events_to_slides(
            all_events,
            slide_groups,
            video_paths[0],
            video_paths=video_paths if multi_part else None,
            total_duration=total_duration,
            lang=lang,
        ).timeline

    # Timeline/alignment caching covers multi-part recordings via the
    # composite MultiVideoKey (any part change/reorder invalidates).
    timeline, _ = cached_timeline(
        video_paths,
        slides_path,
        policy=policy,
        base_dir=slides_path.parent,
        timeline_fn=_run_match,
        cfg={"lang": lang, "frame_offset": 1.0, "multi_part": multi_part},
    )

    def _run_align():
        return align_transcript(merged_transcript, timeline)

    alignment, _ = cached_alignment(
        video_paths,
        slides_path,
        policy=policy,
        base_dir=slides_path.parent,
        alignment_fn=_run_align,
        cfg=alignment_cache_config(lang, multi_part=multi_part),
    )

    return PipelineArtifacts(
        alignment=alignment,
        timeline=timeline,
        transcript_language=merged_transcript.language,
    )


def alignment_cache_config(lang: str | None, *, multi_part: bool) -> dict[str, Any]:
    """The alignment cache config shared by ``harvest`` and ``autopilot``.

    It names the ASR clean-up version (#1105): an alignment computed from a
    differently cleaned transcript misses once and is recomputed (cheap),
    while the expensive transcript cache entry is reused unchanged.
    """
    from clm.voiceover.asr_cleanup import ASR_CLEANUP_VERSION

    return {"lang": lang, "multi_part": multi_part, "asr_cleanup": ASR_CLEANUP_VERSION}


def segment_payload(segment) -> dict[str, Any]:
    """A transcript segment as harvest reports show it: span, text, part,
    and the ASR suspect flags / quality metrics that are known (#1105)."""
    return {
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "source_part_index": segment.source_part_index,
        **segment.quality_dict(),
    }


def classify_slide(*, has_transcript: bool, vo_present: bool) -> str:
    """The structural novelty class of one slide (see the module docstring)."""
    if has_transcript:
        return "transcript_adds_material" if vo_present else "no_existing_vo"
    return "covered" if vo_present else "unmatched_slide"


def _narrative_by_group(deck) -> dict[str, list[Member]]:
    """The narrative (voiceover/notes) members of each slide group."""
    result: dict[str, list[Member]] = {}
    for group in deck.groups:
        members = [m for m in group.members if m.role in _NARRATIVE_ROLES]
        if members:
            result[group.anchor_id] = members
    return result


def _voiceover_payload(members: list[Member], side: Lang) -> dict[str, Any]:
    from clm.slides.doc_identity import content_fingerprint

    cells = []
    for member in members:
        cell = member.side(side)
        if cell is None:
            continue
        cells.append(
            {
                "key": member.key.render(),
                "role": member.role,
                "layout": member.layout,
                "text": cell.body,
                # The freshness token `task` frames and `accept` re-checks.
                "fingerprint": content_fingerprint(cell),
            }
        )
    present = any(c["text"].strip() for c in cells)
    return {"present": present, "cells": cells}


def build_report(
    bundle: LoadedBundle,
    slide_groups: list[SlideGroup],
    artifacts: PipelineArtifacts,
    *,
    lang: str,
    video_paths: list[Path],
) -> dict[str, Any]:
    """Join the pipeline artifacts with the v3 deck into the report envelope.

    ``bundle.outcome.deck`` must be set (the CLI turns a refusal into exit 2
    before calling this).
    """
    deck = bundle.outcome.deck
    assert deck is not None
    alignment = artifacts.alignment
    timeline = artifacts.timeline
    narrative = _narrative_by_group(deck)
    # Suspect ASR segments per slide (#1105). Only an alignment with the
    # assignment trail carries segment objects; without it flags are unknown.
    suspects: dict[int, list[dict[str, Any]]] | None = None
    if alignment.assignments:
        suspects = {}
        for a in alignment.assignments:
            if a.slide_index is not None and a.segment.flags:
                suspects.setdefault(a.slide_index, []).append(segment_payload(a.segment))

    items: list[dict[str, Any]] = []
    counts: dict[str, int] = dict.fromkeys(NOVELTY_CLASSES, 0)
    known_indices: set[int] = set()
    for sg in slide_groups:
        if sg.slide_type == "header":
            known_indices.add(sg.index)
            continue
        known_indices.add(sg.index)
        slide_id = sg.cells[0].slide_id if sg.cells else None
        notes = alignment.slide_notes.get(sg.index)
        transcript_text = alignment.get_notes_text(sg.index)
        vo_members = narrative.get(slide_id, []) if slide_id else []
        voiceover: dict[str, dict[str, Any]] = {
            side: _voiceover_payload(vo_members, side) for side in _SIDES
        }
        cls = classify_slide(
            has_transcript=transcript_text is not None,
            vo_present=voiceover[lang]["present"],
        )
        counts[cls] += 1
        item: dict[str, Any] = {
            "key": f"id:{slide_id}" if slide_id else None,
            "slide_index": sg.index,
            "title": sg.title,
            "class": cls,
            "voiceover": voiceover,
        }
        if timeline is not None:
            item["in_timeline"] = any(e.slide_index == sg.index for e in timeline)
        if notes is not None and transcript_text is not None:
            item["transcript"] = {
                "text": transcript_text,
                "segments": list(notes.segments),
                "revisited_segments": [list(r) for r in notes.revisited_segments],
            }
            if suspects is not None:
                item["transcript"]["suspect_segments"] = suspects.get(sg.index, [])
        if slide_id is None:
            item["note"] = (
                "slide has no slide_id — run `clm slides normalize --stamp-ids` "
                "before harvesting into it"
            )
        items.append(item)

    unmatched_speech: list[dict[str, Any]] = [
        segment_payload(seg) for seg in alignment.unassigned_segments
    ]
    # Aligned notes pointing at indices outside the parsed deck (a stale
    # injected alignment, or slides removed since the recording) are speech
    # without a slide — surface them, never drop them silently.
    for idx in sorted(set(alignment.slide_notes) - known_indices):
        text = alignment.get_notes_text(idx)
        if text is not None:
            entry: dict[str, Any] = {"slide_index": idx, "text": text}
            if suspects and suspects.get(idx):
                entry["suspect_segments"] = suspects[idx]
            unmatched_speech.append(entry)

    # Count only what the report surfaces, so every counted suspect is findable.
    suspect_count = sum(len(i.get("transcript", {}).get("suspect_segments", [])) for i in items)
    suspect_count += sum(
        len(e["suspect_segments"]) if "suspect_segments" in e else int(bool(e.get("flags")))
        for e in unmatched_speech
    )

    actionable = bool(unmatched_speech) or any(counts[c] for c in ACTIONABLE_CLASSES)
    return {
        "schema": 1,
        "tool": "harvest",
        "verb": "report",
        "deck": {
            "de": str(bundle.de_path),
            "en": str(bundle.en_path),
            "de_companion": str(bundle.de_companion_path) if bundle.de_companion_path else None,
            "en_companion": str(bundle.en_companion_path) if bundle.en_companion_path else None,
        },
        "videos": [str(p) for p in video_paths],
        "video_language": lang,
        "transcript_language": artifacts.transcript_language,
        "video_fingerprint": video_fingerprint(video_paths),
        "summary": {
            "slides": len(items),
            "classes": counts,
            "unmatched_speech": len(unmatched_speech),
            "suspect_segments": suspect_count,
            "actionable": actionable,
        },
        "items": items,
        "unmatched_speech": unmatched_speech,
    }


def report_exit_code(report: dict[str, Any]) -> int:
    """0 = nothing to harvest, 1 = actionable items (2 = error, CLI-side)."""
    return 1 if report["summary"]["actionable"] else 0
