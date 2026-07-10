# Driving `clm harvest` as an Agent (CLM {version})

`clm harvest` is an **agent toolkit, not an autonomous solver**: the engine
owns everything deterministic about a recording (ASR, slide-transition
detection, OCR matching, transcript alignment, caching, validators), and
**you** own the judgment — what the spoken narration means for each slide's
voiceover, and how the twin language follows. The engine emits; it never
invokes a model. The one exception is `autopilot` (the legacy embedded-LLM
one-shot for agent-less humans; never use it as an agent, never in CI).

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

## verify — the structural post-check

`clm harvest verify DECK` runs the v3 lens gate plus the deck-half
structural gate. One-sided narrative members are listed as
`pending_twins`, **not** failures — they are the translation work you
handed to the sync loop. Exit `2` means real corruption; stop and diagnose.

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
