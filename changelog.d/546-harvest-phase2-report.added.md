- New `clm harvest` command group (epic #546 Phase 2) — the agent-first
  rebuild of the video→voiceover feature. Its read-only default verb
  `clm harvest report DECK VIDEO… --lang de|en` runs the cached
  deterministic pipeline (transcribe → transition detect → OCR match →
  align) and emits per-slide JSON keyed by the v3 member handle
  (`id:<slide_id>`), with the aligned transcript, the existing voiceover
  baseline on both language sides, and a structural novelty class
  (`no_existing_vo` | `transcript_adds_material` | `covered` |
  `unmatched_slide`, plus `unmatched_speech` per unassigned segment).
  Exit codes 0/1/2; `--transcript`/`--alignment` injection skips ASR. The
  diagnostics `transcribe`/`detect`/`identify`/`identify-rev`/`cache`/
  `trace` are re-homed under the group (the `clm voiceover` names remain
  until the Phase-4 cutover).
