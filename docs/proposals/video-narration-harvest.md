# Proposal: Agent-First Video Narration Harvest

**Status**: proposal (pre-design exploration; open questions resolved by
maintainer 2026-07-04, see §8)
**Date**: 2026-07-04
**Compatibility**: none required — the feature currently has a single user;
no deprecated aliases, no legacy write paths, direct cutover.
**Related**: #366 (agent-first sync pivot), #520 (sync-engine-v3), #501
(separated voiceover companions), `docs/claude/voiceover-design.md` (original
video pipeline design)

## 1. Problem

CLM has a feature that extracts spoken narration from recorded videos and
integrates it into slide decks (`clm voiceover sync` and its satellites). It
serves three real needs:

1. **No written voiceover yet** — a video was recorded free-form; the
   transcript should become the written voiceover so the next revision of the
   video has a script.
2. **Recording added material** — the trainer improvised clarifications during
   recording that should flow back into the existing written voiceover.
3. **Not everything spoken belongs in the script** — asides ("oh, I skipped a
   slide"), live-coding dictation ("def… my-function… don't forget the
   colon…"), greetings, and operator remarks must be curated out. Deciding
   what to keep, what to merge, and what to drop is subtle judgment work.

Three problems with the current implementation:

**(a) One-shot architecture.** `clm voiceover sync` is an autonomous solver:
it transcribes, aligns, and then makes all curation/merge decisions in a
single embedded LLM call (OpenRouter → Sonnet, batched at 20k chars, prompts
in `src/clm/voiceover/prompts/merge_*.md`). In practice the task is too
subtle for one shot. The actual workflow today is: a driving agent (Claude
Code) is told to do the job, cannot get at the pipeline's intermediate state,
and resorts to throw-away scripts poking at `clm.voiceover` internals. This
is exactly the architecture the sync redesign (#366) abandoned, for the same
reason.

**(b) Write-back predates the v3 identity model.** The write path *is*
split/companion-aware (see §2), but inline writes are keyed by **slide index
within one language** (`_map_slides_to_cells`), only one language side is
written per run, and the DE/EN twin is left divergent for a *different*
engine (`clm slides sync`) to notice later. The two pipelines share no
identity model and no trust store: a harvest shows up in the next
`slides sync report` as an unexplained divergence.

**(c) Naming.** "Voiceover" means two unrelated things: the *written
narration text layer* in decks (extract/inline/companions — `clm voiceover
extract`, MCP `voiceover_extract`) and the *video-recovery pipeline*
(`clm voiceover sync`, `transcribe`, `backfill`, …). The verbs collide:
`extract` means "move text cells to a companion file" while the video feature
is what a human would call "extracting voiceover". `sync` and `report` each
mean two different things across `clm voiceover` and `clm slides sync`. This
hurts human and agent discoverability alike.

## 2. Current state (facts)

Mapped 2026-07-04 against `master` (v1.19.0).

### The video pipeline (`src/clm/voiceover/`, CLI `clm voiceover`)

Deterministic stages: `transcribe.py` (Whisper/Cohere/Granite ASR behind a
`TranscriptionBackend` protocol, ffmpeg audio extraction, subprocess
isolation), `keyframes.py` (transition detection), `matcher.py` (Tesseract
OCR + rapidfuzz slide matching with monotonic alignment), `aligner.py`
(timestamp-overlap assignment of transcript segments to slides),
`timeline.py` (multi-part stitching), `cache.py` (artifact cache under
`.clm/voiceover-cache/` keyed by video fingerprint + slide hash).

Judgment stage: `merge.py` — one embedded LLM call per batch merges baseline
voiceover with transcript, returning `{merged_bullets, rewrites,
dropped_from_transcript}`. All curation rules (drop self-corrections,
dictation, greetings…) live in `prompts/merge_{de,en}.md` and are executed by
the embedded model, not by the driving agent. `--overwrite --polish-level
verbatim` bypasses curation entirely.

Write-back (in `clm.notebooks.slide_writer` and `clm.slides.voiceover_tools`,
not in `clm.voiceover`): inline route keyed by slide **index** per language;
companion route keyed by `slide_id` via `for_slide`, auto-detected
(`resolve_companion`), layout-aware, split-filename-safe
(`slides_x.de.py` → `voiceover_x.de.py`). Companion mode requires stamped
slide ids. Cross-language: `--propagate-to {de,en}` translates via a second
embedded LLM call (`propagate_{src}_to_{tgt}.md` prompts) and writes the
other side through the same two routes.

CLI surface: one-shot writers (`sync`, `backfill`, `sync-at-rev`, `port`,
`compare-from-inventory`) plus composable diagnostics (`transcribe`,
`detect`, `identify`, `identify-rev`, `compare`, `report`, `cache`,
`trace`). MCP exposes only read-only pieces (`voiceover_transcribe`,
`voiceover_identify_rev`, `voiceover_compare`, `voiceover_backfill_dry`,
`voiceover_cache_list`, `voiceover_trace_show`); writes are intentionally
CLI-only.

### The sync-v3 internal deck model (`#520`, Phases 0–3 merged)

Five modules under `src/clm/slides/` with a clean dependency DAG:

```
bilingual_doc.py  ←  doc_lenses.py  ←  sync_diff.py  ←  doc_ledger.py  ←  doc_apply.py
   (model)           (parse/project)     (baselines+differ)  (trust store)     (write executor)
```

- `bilingual_doc.py` — imports only attrs. One deck bundle (up to 4 files:
  DE/EN deck halves + DE/EN voiceover companions) is one `BilingualDeck`. A
  `Member` unifies both language sides (`de`/`en` `SideCell`s) and inline vs.
  companion placement (`layout` field) under a single `MemberKey` identity
  (`id:<slide_id>` or `pos:<group>/<kind>/<ordinal>`). Mismatches are
  first-class `Observation`s; non-normalized input yields a framed
  `NormalizeRefusal`, never a crash.
- `doc_lenses.py` — `parse_bundle`/`project` with byte-identity round-trip
  laws (`project(parse(x)) == x`). Reads split pairs + optional companions
  (inline layout too); does **not** read pre-split unified bilingual single
  files (that stays `split.py`'s job).
- Sync-specific concepts (baselines, 3-way differ, ledger, apply executor)
  are layered strictly above; the model and lenses import nothing sync.

The model and lens layers are already sync-agnostic. The only structural
snags for reuse: the fingerprint/snapshot helpers (`content_fingerprint`,
`baseline_from_deck`, `_iter_with_groups`) live inside the ~2200-line
`sync_diff.py`, and `doc_apply.py` imports ledger + differ, so today any
consumer of "parse, edit, write back atomically" drags in the whole sync
stack.

### The agent-first pattern to copy (`clm slides sync`, #366/#440)

Verb group with read-only default: `report` (tiered JSON, no model, no key,
exit 0/1/2) / `verify` / `diagnose` / `apply` (deterministic tier-1 writes
only) / `task` (frames a judgment item: instructions + inputs +
`answer_schema` + named validator) / `accept` (validates the answer, writes
both halves atomically or nothing, `--record` banks to the ledger) /
`record` (v3) / `autopilot` (the only path that constructs embedded model
clients; human one-shot; never in CI). Canonical agent loop documented in
`clm info sync-agents`. Principles: *read by default, every write is an
explicit verb; the engine emits, it does not invoke.*

## 3. Decision 1: promote the v3 model to CLM's general internal deck representation

**Proposal: yes.** Declare `bilingual_doc` + `doc_lenses` (+ a sync-free
write surface) the internal representation for *any tool that mutates deck
narration or structure*, serializable to the supported on-disk layouts
(split DE/EN, inline or companion voiceover). Concretely:

1. **Extract identity/snapshot utilities out of `sync_diff.py`** into a small
   sync-free module (working name `doc_identity.py`): `content_fingerprint`,
   `baseline_from_deck` (as a general "structural snapshot"), and the group
   iteration helpers that `doc_apply`/`doc_ledger` currently import from
   differ privates. The differ keeps importing them; non-sync consumers stop
   importing the differ.
2. **Extract a sync-free write surface from `doc_apply.py`**: "given a
   `BilingualDeck` and a set of member edits, emit and atomically write the
   ≤4 files" — without requiring a ledger or a diff. The sync executor
   becomes one consumer of it.
3. **Accept the model's preconditions as the contract**: input must be
   normalized (stamped ids; id-less content → `NormalizeRefusal` with the
   "run `clm slides normalize --stamp-ids`" hint), and the representation
   covers modern-layout decks only — `split.py`/`unify` remain the migration
   boundary for pre-split unified files.

**Explicit non-goals**: replacing `clm.notebooks.slide_parser` (lossy,
single-language, ~40 consumers incl. build, OCR matching, polish, web
studio) or `clm.slides.raw_cells` (the lossless primitive both engines sit
on). Read-only consumers migrate opportunistically or never. The rule is
narrow: **mutation goes through `BilingualDeck`**.

The video harvest write-back (§4) is the ideal first non-sync consumer: it
replaces index-keyed inline writes with id-keyed members, gains atomic
multi-file writes, becomes twin-aware structurally, and can finish by
recording to the consistency ledger so a harvest never surfaces as a mystery
divergence in the next `slides sync report`.

Timing note: this makes harvest a second consumer of the v3 core. It should
not land before v3 has survived its dogfood week; conversely it strengthens
the case for Phase 4 (flip default, delete v2).

## 4. Decision 2: rebuild the video feature as an agent-first toolkit

Split the pipeline along the deterministic/judgment line and move the
judgment from the embedded model to the driving agent, mirroring the sync
verbs exactly.

- **Deterministic tier (engine-owned)**: audio extraction, ASR, transition
  detection, OCR matching, alignment, revision identification, caching. No
  model, no key, cacheable, reproducible.
- **Judgment tier (agent-owned)**: everything `merge.py`'s prompt does today
  — is this passage new material, an aside, dictation noise? Extend or
  replace the existing bullet? Translate for the twin (§6)? A frontier agent
  in-session (Opus/Fable in Claude Code) beats a batched embedded call: it
  holds the whole deck in context, can inspect neighboring slides, can ask
  the trainer when genuinely ambiguous, and iterates instead of one-shotting
  20k-char batches.

### Verb set (group name per §5; `harvest` used here)

| Verb | Behavior | Analogue |
|---|---|---|
| `clm harvest report DECK VIDEO…` | Runs the full deterministic tier (cached), emits per-slide JSON: matched slide (`MemberKey`), video language, transcript segment(s), existing VO baseline (both languages, inline or companion), and a novelty classification: `no_existing_vo` \| `transcript_adds_material` \| `covered` \| `unmatched_speech` \| `unmatched_slide`. Read-only; no model; no key. Also the human dry-run. | `sync report` |
| `clm harvest task DECK --slide ID [--kind curate\|translate]` | Emits the framed judgment for one slide: baseline VO + aligned transcript + the curation rules (today's merge prompt content, restated as instructions **to the caller**) + `answer_schema` + named validator. Omitting `--slide` frames every non-`covered` item. Read-only. | `sync task` |
| `clm harvest accept DECK --slide ID --answer FILE\|-` | Runs the validator; on pass writes through the v3 model (id-keyed member edit, atomic ≤4-file write, companion/inline per deck layout) — both languages if the answer carries both, else the recorded language only (§6). `--record` banks the touched member(s) to the sync consistency ledger. Writes nothing on validation failure. The only write path. | `sync accept` |
| `clm harvest verify DECK` | Structural post-check; delegates to `clm slides sync verify` on the pair. | `sync verify` |
| `clm harvest autopilot DECK VIDEO…` | The current one-shot `voiceover sync` behavior: embedded model for curate + propagate, key-gated, for humans without an agent. Explicitly never in CI. | `sync autopilot` |

The existing diagnostics survive unchanged under the same group:
`clm harvest transcribe / detect / identify / identify-rev / cache / trace`.
`backfill` (identify-rev → sync-at-rev → port) becomes a mode or companion
verb operating on historical revisions; its `--dry-run`-by-default behavior
already matches the philosophy.

### Answer schema: bullet lists

Voiceovers are written as bullet lists so the speaker can keep track of
where they are while recording. The `answer_schema` makes that the primary
shape: an answer is, per language side, an ordered list of bullet strings
(markdown inline formatting allowed within a bullet, no nested block
structure), plus a `dropped` list echoing transcript passages the agent
chose to discard (the audit trail that replaces today's
`dropped_from_transcript`). A structured list is also the simplest thing
for the agent to produce and for the validator to check — bullet-level
identity survives JSON round-trips, whereas free markdown would need
re-parsing and invites formatting drift. Rendering bullets into the member
body (comment prefixes, blank-line policy) is the engine's job at `accept`
time, reusing the existing cell-spacing rules.

### Validators (deterministic, engine-owned)

`accept` validates at minimum: answer matches the schema; the target member
exists and its baseline hash matches what `task` framed (no lost concurrent
edit); bullets non-empty for `no_existing_vo` slides; language sides
well-formed; the write preserves `de_id == en_id` and byte-identity of
shared cells (reuse the sync validators). Curation *quality* is not
validated — that is the agent's job and the trainer's review; the trace log
(`dropped_from_transcript` equivalent) is kept so decisions are auditable.

### MCP surface

Read-only half only, mirroring sync: `harvest_report`, `harvest_task`
(plus existing `voiceover_transcribe`/`identify_rev` renamed into the
namespace). `accept` stays CLI-only, like every other write.

### Documentation

A new `clm info harvest-agents` topic, modeled on `sync-agents.md`,
documenting the canonical loop:

```
harvest report → branch → (task → judge → accept [--record])* → verify → slides sync report
```

## 5. Decision 3: naming

**"Voiceover" keeps exactly one meaning: the written narration text layer in
decks** (extract/inline/companions/anchoring). The video-recovery feature
gets its own group named for what it does — recover narration from a
recording.

Recommended name: **`clm harvest`**. Alternatives considered:
`clm transcript` (undersells the integration half), `clm backport` (collides
conceptually with git terminology and the `port` verb), anything under
`recordings` (already means the OBS/Auphonic audio production workflow).

Migration: **direct cutover, no deprecated aliases.** The feature has a
single user, so the video-side verbs move to `clm harvest` in one release
and the old names are deleted outright. `port` and `compare` (and
`compare-from-inventory`) move into the harvest group as well — they are
video-side tooling and keeping them under `voiceover` would perpetuate the
overload. The text-layer verbs (`voiceover extract/inline/inline-notes`)
stay put for now; folding them under `clm slides` is a separate, optional
cleanup.

## 6. Single-language recordings and translation

**Scenario**: videos exist in only one language (typically DE); the harvested
narration must also appear, translated, in the other half of the split pair.
The design must handle this without duplicating translation machinery.

### Principle

Harvest is **recorded-language-authoritative**. The transcript is ground
truth only for the language that was actually spoken. Translation of the
twin side is a *judgment* task like curation — so it belongs to the agent
tier, not to an embedded model call (today's `--propagate-to` prompts).

### Mechanics

1. `report` states the video language per item and, for each slide, whether
   the twin side's VO is absent, present-but-stale, or present-and-current
   (it can see both sides — the v3 `Member` holds both).
2. `task --kind curate` frames the recorded-language merge (baseline +
   transcript → curated bullets). `task --kind translate` frames the twin
   side: the *accepted* curated text + the twin's existing VO (if any) +
   the deck's authoring rules. Two task kinds, because curation and
   translation are different judgments and the agent should be able to do
   them in sequence — or batch translations across slides for consistency of
   terminology.
3. `accept` takes either a single-language answer or a bilingual one:
   - **Bilingual answer** (agent curated and translated in one pass): both
     `SideCell`s of the member are updated; the pair stays clean;
     `--record` banks both.
   - **Recorded-language-only answer**: the engine writes that side and the
     member becomes a deliberate, *representable* divergence — exactly the
     state the v3 model was built to hold (a localized member with one side
     newer). The item is then picked up as ordinary tier-3 work by the next
     `clm slides sync report`, and the **same driving agent** resolves it
     through the existing `sync task --kind translate` / `sync accept` loop.
4. `autopilot` keeps today's embedded `propagate_{src}_to_{tgt}` behavior for
   the no-agent fallback.

### Why delegate the twin to `slides sync` instead of making harvest own it

- One translation/reconciliation machine, not two. Harvest editing one half
  of a split pair is *definitionally* the situation `slides sync` exists
  for; reusing it means harvest gets watermark/ledger semantics, conflict
  handling, and validators for free — forever.
- It keeps harvest's contract small: "put the recording's narration into the
  recorded language, correctly attributed and curated."
- The bilingual-answer path in `accept` still allows the one-stop workflow
  when the agent prefers it (small decks, terminology already settled).

The one hard requirement this imposes: an `accept` that writes one side must
leave the pair in a state `slides sync report` classifies correctly as
"DE edited, EN needs translation" rather than as corruption — i.e. harvest
writes must go through the v3 model and (when `--record` is used) mark only
the written side's trust, never blessing the stale twin. Harvest-originated
ledger entries carry a **harvest-specific provenance**,
`harvest:<video-fingerprint>` (the same fingerprint that keys the artifact
cache), so a later `sync report` can explain *why* the sides diverge and
trace the write back to the exact recording.

## 7. Sequencing

1. **v3 utility extraction** (§3.1–3.2): `doc_identity` + sync-free write
   surface. Small; also benefits sync Phase 4 cleanup. No behavior change.
2. **`harvest report`**: repackage the deterministic pipeline behind a
   read-only verb with novelty classification and video-language detection.
   No writes; can land while v3 dogfooding continues.
3. **`task` / `accept`** writing through the v3 model, with validators,
   ledger `--record`, and the single-side/bilingual answer contract (§6).
   Gated on confidence in v3 (after the dogfood week).
4. **Rename + cut over**: `clm harvest` group (including `port`/`compare`/
   `compare-from-inventory`), old video-side `voiceover` verbs deleted,
   `voiceover sync` → `harvest autopilot`, MCP renames, `clm info
   harvest-agents`, user-guide update. No deprecated aliases.

## 8. Resolved decisions (maintainer, 2026-07-04)

Formerly the open-questions list; all four are settled:

- **Answer schema = bullet lists** (§4). Voiceovers are authored as bullet
  lists so the speaker can track their position while recording; the schema
  is a per-language ordered list of bullet strings plus a `dropped` audit
  list. Rendering into the cell body is the engine's job. Remaining detail
  for the design phase: how `**[Revisited]**` transcript segments are
  presented inside a `curate` task's inputs.
- **Novelty classification stays purely structural** (VO present/absent +
  transcript matched/unmatched). No textual-similarity heuristic: it would
  have unexpected failure modes and grow ever more complex chasing them.
  "Does the transcript add material?" is wholly the agent's judgment.
- **Harvest-specific ledger provenance**: `harvest:<video-fingerprint>`
  (§6).
- **`port`, `compare`, and `compare-from-inventory` move into the harvest
  group** (§5) — the cleaner cut, and with no backward-compatibility
  obligation there is no reason to stage it.

Additionally: **no backward compatibility anywhere in this proposal.** The
feature has one user; direct cutover, no deprecated aliases, no legacy
write-path retention beyond what `autopilot` deliberately keeps.
