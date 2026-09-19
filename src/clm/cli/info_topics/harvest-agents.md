# Driving `clm harvest` as an Agent (CLM {version})

`clm harvest` is an **agent toolkit, not an autonomous solver**: the engine
owns everything deterministic about a recording (ASR, slide-transition
detection, OCR matching, transcript alignment, caching, validators), and
**you** own the judgment — what the spoken narration means for each slide's
voiceover, and how the twin language follows. The engine emits; it never
invokes a model. The one exception is `autopilot` (the legacy embedded-LLM
one-shot for agent-less humans; never use it as an agent, never in CI).

The shared rules every clm agent toolkit implements (envelope shape,
freshness tokens, validator registry, exit codes) live in
`clm info agent-tasks`; this topic is the harvest-specific half.

## The canonical loop

```
clm harvest report DECK VIDEO… --lang de --json     # what did the recording say?
→ for each actionable slide:
    clm harvest task DECK VIDEO… --lang de --slide ID [--kind curate|translate]
    … judge: curate bullets (you) …
    clm harvest accept DECK --answer answer.json --record
→ clm harvest verify DECK
→ clm slides sync report DECK                        # twin translation continues there
```

Work on a branch; every `accept` is atomic (all files or nothing).

## Reading the report

`report` runs the cached deterministic tier and emits one item per slide,
keyed by the v3 member handle (`id:<slide_id>`), with the aligned
transcript, the existing voiceover baseline on **both** language sides, and
a purely structural class:

| Class | Meaning | You do |
|---|---|---|
| `no_existing_vo` | speech assigned, no voiceover on the recorded side | `task` → curate → `accept` |
| `transcript_adds_material` | speech assigned AND a voiceover exists | `task` → judge whether the speech adds anything → `accept` (or nothing) |
| `covered` | voiceover exists, recording contributed no speech | nothing |
| `unmatched_slide` | no voiceover, no speech | usually nothing (check `in_timeline`) |
| `unmatched_speech` | transcript segments assignable to no slide | read them; if they belong to a slide, fold them in during that slide's task |

Exit codes: `0` nothing to harvest · `1` actionable items · `2` error
(non-normalized bundle → run `clm slides normalize` first).

## task — the framed judgment

A task document gives you: `instructions` (the curation rules), `inputs`
(every narrative cell of the slide with its `member` handle and both
language sides, in document order; the aligned transcript with
`revisited_segments`; the slide content), the `answer_schema`, and two
freshness tokens you must echo back: `baseline_fingerprints` (per narrative
member) and `video_fingerprint`.

- `--kind curate` (default): merge the recording's speech into the recorded
  language's bullets.
- `--kind translate`: frame the twin side from the already-curated source
  (you may prefer letting `clm slides sync` drive translation — see below).

Slides routinely carry **several narrative cells** (one per code cell).
Your answer is a list of per-member `updates`: address each existing cell
by its `member` handle; create a new cell with `"member": null` (place it
with `"after": "<member>"`, default = end of the slide group).

## accept — the write you own

```
clm harvest accept DECK --answer answer.json [--record] [--dry-run] [--json]
```

Validation is strict and all-or-nothing: schema shape, per-member
fingerprint freshness against the **live** deck (a concurrent edit — or a
narrative cell added/removed since the task — rejects; re-run `task`),
single-cell body guards, and the v3 re-parse gate. Bullets render into the
deck's narrative cell style; new members get a minted `<owner>-vo` id and
the deck's companion/inline convention.

**One-language vs bilingual answers.** Harvest is recorded-language-
authoritative. A one-language answer writes that side only — the pair
becomes a *deliberate, representable divergence* that the next
`clm slides sync report` frames as translation work (`translate_new` /
`translate_edit`), which you resolve through the ordinary sync loop. A
bilingual answer (you translated in the same pass) lands both sides and
the pair stays clean.

**`--record`** banks the touched members into the sync consistency ledger
with provenance `harvest:<video_fingerprint>` — so a later `sync report`
can explain *why* the sides diverge and trace the write to the exact
recording. It never blesses the stale twin. Use it whenever you accept.

Exit codes: `0` applied · `1` applied but the ledger record was withheld by
the structural gate (fix the pair, then `clm slides sync record`) · `2`
rejected (nothing written).

## port/compare — revision history without a video

The revision-history half of the domain (carrying voiceover from an older
git revision of a deck onto HEAD, auditing what changed) is agent-first too:

```
clm harvest task DECK --lang de --kind port --source slides-at-<rev>.py
→ answer each framed slide pair (same harvest-bullets shape)
→ clm harvest accept DECK --answer answer.json      # the ordinary write path

clm harvest task DECK --lang de --kind compare --source slides-at-<rev>.py
→ answer with verdicts (one per framed pair: covered|rewritten|added|dropped|manual_review)
→ clm harvest compare-accept OLDER DECK --lang de --answer verdicts.json -o report.json
```

`--kind port` pairs the source's slides with the current deck by the
deterministic matcher (`slide_id`, then title, then content similarity) and
frames each pair whose source slide carries voiceover: the target slide's
baseline (both sides, with the usual `baseline_fingerprints`), the source's
`prior_bullets`, and both slide contents when the slide changed. The answer
is the standard bullet document — `accept` writes it id-keyed and
companion-aware. (A port answer has no `video_fingerprint`, so `--record`
is refused: ledger provenance is keyed by video.)

`--kind compare` is **auditing**: it frames bullet-relation labeling per
matched pair and `compare-accept` writes the canonical report JSON (the
shape `compare-report` re-renders). Freshness is by file content
of each deck and its selected voiceover companion (`source_fingerprint` /
`target_fingerprint` in the task envelope, echoed in the answer). Decks are
never touched. Deterministic buckets
(`new_at_head` / `removed_at_head` / both-sides-empty pairs) row in the
report without needing a verdict. A pair the matcher flags
`manual_review` (duplicate titles too close to disambiguate) is not framed
— fix the duplicate slide ids/titles first.

The typical source file comes from `clm harvest export-at-rev`; both port
and compare read its inline narration **and** selected voiceover companion.
Unplaceable companion narration is an error, not silently discarded. Over
MCP, the framing half is mirrored: `harvest_task` accepts `kind="port"` / `kind="compare"`
with a `source` argument (no videos); the answer/accept side stays CLI-only.

## Backfill — the agent-driven history loop

There is no top-level `harvest backfill`, `port`, `compare`,
`compare-from-inventory`, or `sync-at-rev`. The legacy model execution lives
only under `harvest autopilot`; agents use this loop:

1. **Identify:** `clm harvest identify-rev DECK VIDEO… --lang de --json`.
   Inspect the scored candidates and select the recorded revision yourself;
   a close score is evidence to review, not authority to choose automatically.
2. **Export:** `clm harvest export-at-rev DECK --rev SHA -o SCRATCH --json`.
   `SCRATCH` must be a new directory. The JSON supplies the full `revision`,
   the exported `deck` path, and `files`. Original names are preserved; a
   `.de`/`.en` twin and voiceover companions are read from that revision,
   even if absent in the working copy. Companion lookup prefers the historical
   `voiceover/` copy over its sibling, as ordinary reads do. No rename tracking,
   transcription, polish, merge, or automatic normalization happens here.
3. **Recover narration on the exported deck (`OLD`):** export preserves only
   the narration already in git. When the recording contributes more, run
   `clm harvest report OLD VIDEO… --lang de --json`, review uncertain
   alignment through `align report`/`align accept`, then use
   `task OLD VIDEO… --lang de --kind curate --slide ID` → judge →
   `accept OLD --answer answer.json`. Reuse any accepted `--alignment` on
   subsequent report/task calls. Normalize the scratch bundle first if the
   v3 gate refuses it; a pre-split deck must be prepared as a normalized pair
   before this curate/accept step. Verify `OLD` after accepting.
4. **Port to the current deck:**
   `clm harvest task DECK --lang de --kind port --source OLD` → judge each
   framed pair → `clm harvest accept DECK --answer answer.json`.
   Do not use `--record` on port answers. Inspect unmatched slides separately;
   the matcher cannot decide where removed or ambiguous content belongs.
5. **Verify and audit:** `clm harvest verify DECK`; optionally frame
   `task --kind compare --source OLD` and bank verdicts via `compare-accept`.
   Continue twin translation with `clm slides sync report DECK`.

Repeat for the relevant historical recordings. Keep video parts in recording
order. For inventory-driven work, resolve the inventory's video paths first,
then run the same loop for each deck; there is no model-invoking inventory
shortcut on the agent surface. `harvest_backfill_dry` was removed from MCP:
its old dry run still called embedded models. Use `harvest_identify_rev`,
`harvest_report`, and `harvest_task`; export and acceptance remain CLI writes.

## verify — the structural post-check

`clm harvest verify DECK` runs the v3 lens gate plus the **deck-halves**
structural gate. One-sided narrative members are listed as
`pending_twins`, **not** failures — they are the translation work you
handed to the sync loop. Exit `2` means real corruption; stop and diagnose.

**Deliberately weaker than `clm slides sync record`'s gate**, which since CLM
{version} projects the voiceover companions and therefore reads a one-sided
narrative member as an `id-asymmetry` error. That is the correct reading *there*
(nothing may be banked as verified while the narration diverges) and the wrong
one *here* (a one-sided harvest write is the pending state this toolkit
produces). So a deck you have just harvested one language for will pass
`harvest verify` and be refused by `sync record` until the twin exists — expected,
and the sync loop is how you clear it.

## align — reviewing the pipeline's guesses

Before you curate, check **how confidently** the deterministic pipeline
assigned speech to slides:

```
clm harvest align report DECK VIDEO… --lang de
→ clm harvest align accept DECK VIDEO… --lang de --answer align-answer.json
→ re-run report/task with --alignment <written-file>
```

`align report` frames every uncertain heuristic decision as an item with its
evidence: boundary-straddling segments (`overlap_fraction` vs the runner-up
slide and its fraction), unassigned segments with their reason
(`no_overlap` / `header_slide`), and OCR slide matches that were weak or
**overruled by the sequential constraint** (`overridden_by_sequential` —
the raw best OCR match lost to forward-progress ordering). Exit codes:
`0` nothing uncertain · `1` items framed · `2` error.

`align accept` takes reassignments — `{"segment_index": N, "to_slide": M}`
(`to_slide: null` unassigns) — validates them against the live alignment
(echo both freshness tokens: `video_fingerprint`, `alignment_fingerprint`),
rebuilds the per-slide notes (the `[Revisited]` grouping is re-derived,
never hand-edited), and writes a **full alignment file**. Load it with
`--alignment` on the next `report`/`task`; iteration works. A wrong
`slide_match` item is fixed indirectly: reassign the segments in its window.
An alignment cached before the assignment trail existed carries no records —
re-run once with `--refresh-cache`.

## The `dropped` audit list

Everything you filter out of the transcript (greetings, self-corrections,
live-coding dictation, tangents) goes into the answer's `dropped` list.
That is the audit trail replacing the old embedded-model
`dropped_from_transcript` — never silently discard speech.

## Caching and fingerprints

All deterministic stages cache in a **shared, deck-independent** root:
`<shared-cache-dir>/voiceover/`, where the shared cache dir resolves like
the LLM cache (`$CLM_CACHE_DIR` → `tool.clm.cache_dir` → `<project-root>/
.clm-cache/`; group flags `--no-cache` / `--refresh-cache` / `--cache-root`).
Video-keyed entries (transcripts, transitions) are therefore computed once
per recording and shared by every deck — forking or moving a deck does NOT
re-run ASR. Entries under the older per-deck
`<deck dir>/.clm/voiceover-cache/` are found on a miss and promoted into the
shared root automatically. Re-running `report`/`task` after the first pass
is cheap. Multi-part recordings
(`VIDEO…` in order, or a quoted glob) share one composite fingerprint —
the same value that keys the cache and the ledger provenance. For tests
and replays, `--alignment FILE` injects a precomputed alignment (works for
multi-part too); `--transcript FILE` skips only ASR (single video).

## Quick reference

| You see… | Do… |
|---|---|
| `report` exit 1, `no_existing_vo` items | `task` → curate → `accept --record` |
| `transcript_adds_material` | judge; only accept if the speech truly adds |
| `unmatched_speech` entries | read; fold into the right slide's task or ignore noise |
| `accept` exit 2 "changed since the task" | re-run `task`, re-judge on fresh inputs |
| `verify` lists `pending_twins` | expected — run the `clm slides sync` loop next |
| twin translation work | `clm slides sync report` → its `task`/`accept` (see `clm info sync-agents`) |

## Principles

Read by default; every write is an explicit verb. The engine emits, it
never invokes a model. Never guess identity — everything is keyed by
member handles. Freshness tokens exist so you never overwrite a concurrent
edit. The stale twin is never silently blessed.
