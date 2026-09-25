# Recordings: from `drift` to a re-recording backlog

**Status**: DECIDED 2026-09-25 (design only — nothing here is implemented;
the implementation issues are listed in §8) | **Created**: 2026-09-25
**Issues**: #907 (this design), #965 (the `report` / `ack` verbs it unlocks),
hoelzl/PythonCourses#360 (the hand-maintained tracker this retires)
**Related**: `docs/claude/design/recordings-parts-and-takes.md` (the local
state model), `docs/claude/design/sync-total-identity-document-model.md`
§5 (the ledger and fingerprint conventions reused here),
`docs/claude/discussions/recordings-rerecord-backlog/state.md` (the argument)

---

## 1. The question

`clm recordings drift` answers "did this topic's *built output* change since
the video was shot?" with `changed` / `current` / `unknown`. The course author
does not use it; PythonCourses keeps a hand-maintained GitHub issue instead
(#360), with ~70 entries at the time of writing. #907 lists four gaps —
(a) no stamps on legacy recordings, (b) a binary verdict, (c) a built
manifest as a prerequisite, (d) no acknowledgement state — and asks which of
them are prerequisites for retiring the tracker.

This note decides: **anchor provenance in the source, in a committed
ledger, and compute severity from the source diff.** That reorders the four
gaps: (c) is the foundation, (b) and (d) are cheap on top of it, and (a)
splits into a prerequisite half (seed from the local state files, no video
analysis) and a follow-up half (the 402-video legacy inventory).

## 2. What exists (verified 2026-09-25)

- **Local state files** under `%LOCALAPPDATA%\clm\recordings\<course-id>.json`
  (`clm.recordings.state`). Per recorded part: `raw_file`, `recorded_at`,
  `git_commit` + `git_dirty` (stamped at arm time since #208 step 5),
  `section_id` (always `None` in practice — the AZAV specs carry no section
  ids), `topic_id`, and `slide_digest` = the topic's rolled-up output
  digest from `.clm-manifest.json`. The lecture key is
  `"<section display name>::<deck display name>"`
  (`recordings/web/routes.py:_resolve_lecture_id`). **Machine-local, never
  committed, one file per (course, language).**
- **`drift`** (`clm.recordings.provenance`): compares `slide_digest` with
  `manifest_topic_digest(manifest, topic_id)`. Topic-granular (a topic with
  three decks flags all three), spec-bound (the manifest is whatever
  `output/shared/` was last built from), and needs a build.
- **The tracker** (#360). Every entry is keyed `topic / deck-stem`, cites the
  commit(s), and carries a human severity word — **structural**,
  **cosmetic** ("reword only", "one extra code line"), **narration only**,
  **trainer-notes only** — plus a second kind of annotation: *which recording*
  ("Recording: Woche 10 / 02 …, 2026-04 cohort"), *whether one exists at all*
  ("confirm whether a recording exists", ×14), *identity moves* ("moved
  verbatim to a new topic — recording still valid, re-point"), *orphans*
  ("deck DELETED, its recording is orphaned"), and the ack ("covered verbally
  in the existing recording", "re-record opportunistically, never on its
  own"). The course-repo rule (`AGENTS.md` row "Which recorded decks need
  re-recording?") makes every editing agent append to it.
- **The legacy inventory** `planning/video_to_slide_mapping.json` in
  PythonCourses: 402 videos across 9 courses (AZAV SE 143, AZAV ML 114,
  Clean Code 44, …; OBS 299, Descript 59, Camtasia 44), each with a
  `matched_slide` path, `video_mtime`, `slide_last_commit`, and a coarse
  mtime-vs-commit `freshness` (stale 238, likely-outdated 142, archived 13,
  current 9). Built by `tools/match_videos_to_slides.py` in the course repo.
- **`clm harvest identify-rev`**: OCR keyframes → rank the slide file's git
  revisions by similarity. Per deck, minutes of work, needs the video.

## 3. The measurement that decides it

Cross-tab of `drift` (against `output/shared/.clm-manifest.json`, built
2026-09-25 from the 2026-08 spec) versus the git evidence for the 197 parts
in `machine-learning-azav-de.json` — "touched" meaning at least one commit
touched the topic directory between the stamped `git_commit` and `HEAD`:

| drift verdict | git evidence | parts | reading |
|---|---|---|---|
| unknown | no `git_commit` / `topic_id` stamped (recorded 2026-04-18 … pre-#208) | 74 | the (a) population inside the dashboard era |
| changed | touched | 62 | agree |
| **changed** | **untouched** | **22** | **false positive**: no commit touched the topic since the recording |
| **unknown** | touched | **30** | topic absent from the manifest — the stamp came from a build of `machine-learning-azav.xml`, the comparison from the 2026-08 spec |
| changed | topic id resolves to two directories (`module_545_…cohort_2026_04` and `module_550_…`) | 6 | topic id alone is not a source anchor |
| unknown | untouched, commit stamped, no digest | 3 | answerable from source: current |

Of the 84 parts where both sides are answerable, 22 (26 %) are wrong in the
"changed" direction, and 30 further parts are `unknown` only because of the
spec binding. The same pass, from the repo alone, answers 117 of 123 parts
that carry a commit (`git diff --numstat <commit> HEAD -- <topic-dir>`,
split into slide files / voiceover companions / other): 90 with slide-file
changes, 25 untouched, 2 with only non-slide files. That is the severity
signal the tracker's humans write by hand, computed in seconds, with no
build.

The 22 false "changed" verdicts were not root-caused in this session. The
plausible mechanisms are all inherent to the digest, not bugs to fix: it
rolls up every output file of the topic across languages, formats and
audiences, so anything outside the topic directory that changes rendered
bytes (templates, a clm upgrade, executed outputs, includes) flips it; and
a stamp and a comparison can come from builds of different specs.

## 4. Decision

### 4.1 A committed recordings ledger, source-anchored

Per topic, `<topic>/.clm/recordings-ledger.json` — the same directory and
gitignore convention as the sync ledger (`!**/.clm/recordings-ledger.json`
must be added to each course repo's `.gitignore`; clm warns when the ledger
exists but `git check-ignore` says it is ignored — the "`.clm/` gitignore
hides ledgers" landmine, pre-empted). Keyed by deck stem (the sync ledger's
`deck_key_for`), one entry per recorded part:

```json
{
  "schema": 1,
  "hash_version": 1,
  "decks": {
    "slides_20_copilot_chat": {
      "parts": [
        {
          "part": 1,
          "recorded_at": "2026-09-25T04:44:38",
          "course_id": "machine-learning-azav-2026-08-de",
          "lang": "de",
          "anchor": {"kind": "commit", "commit": "1cfeeb23dd…", "dirty": false},
          "members": {"<member-key>": "<content-fingerprint>", "…": "…"}
        }
      ],
      "ack": {
        "at": "2026-09-25", "note": "covered verbally in the existing recording",
        "members": {"<member-key>": "<content-fingerprint>", "…": "…"}
      }
    }
  }
}
```

- `anchor.kind` records confidence: `commit` (clean tree at record time),
  `commit-dirty` (the commit under-describes what was shown — 43 of 197
  parts in the ML-AZAV state file were recorded dirty), `time` (commit
  chosen by `recorded_at`, seeding), `mtime` (legacy inventory), `identified`
  (`harvest identify-rev`).
- `members` are the recorded language's per-member content fingerprints
  (`clm.slides.doc_identity.content_fingerprint`, same function and
  `hash_version` gating as the sync ledger). They are stored **always**, not
  only for dirty trees: the report then never depends on git history being
  present (shallow clones, squashed cohorts), and a `hash_version` bump
  falls back to recomputing from `anchor.commit` when one exists.
- The ledger is written by the dashboard at record time (beside the local
  state, which keeps the workflow fields: raw file, takes, processing
  status) and by the verbs in §4.4. The local state file stays what it is;
  nothing course-level lives there any more.

### 4.2 Severity from the source diff

`report` parses the deck at `HEAD` (`BilingualDeck`, member keys by
`slide_id`) and compares member fingerprints with the ledger entry:

| class | rule (highest wins) | tracker word |
|---|---|---|
| `structural` | an id-bearing member added or removed, member order changed, or a **code** member's fingerprint changed | "structural" |
| `visible` | a presentation (markdown) member's fingerprint changed | "cosmetic … reword only" at low counts, structural at high counts — the count is reported, the human decides |
| `narration` | only voiceover companion / narration members changed | "narration only, slides unchanged" |
| `notes` | only trainer-notes members changed | "trainer-notes-only edits (no visible drift)" |
| `none` | every member matches | not on the list |

Each row also carries `changed/total` member counts and, when the anchor is
a commit, the commits since the anchor that touched the deck path. **No
re-record judgment** (the #907 non-goal stands): the reasoning_effort
one-liner in 20 decks is `structural` by rule and cosmetic by the author —
that is what `ack` is for.

### 4.3 Acknowledgement

`ack` stores the current member fingerprints and a note on the deck entry.
`report` shows a deck as `acknowledged` while `HEAD` matches the ack'ed
fingerprints and as `drifted-since-ack` (with the new severity) once it
moves further — the "seen, decided not to re-record at digest X" semantics
of #907(d), at member granularity so that a later edit of a different slide
re-surfaces the deck while the acknowledged edit stays acknowledged.

### 4.4 Verbs

Under `clm recordings`, following the agent-toolkit shaping rule (keep the
feature's name and home, standard verb set, absorb report-ish siblings,
retire superseded verbs without shims):

| verb | does |
|---|---|
| `report [--all] [--json]` | the backlog: one row per recorded deck with severity, counts, commits since anchor, ack state; plus `orphaned` (ledger entry whose deck no longer exists) and, with `--all`, `unrecorded` (deck on disk with no entry — retires "confirm whether a recording exists"). With a manifest present, an optional `built_output_changed` flag from today's digest comparison. Default verb of the group is **not** changed (`recordings` has too many peers). |
| `ack DECK [--note …]` | write the ack at the current fingerprints |
| `seed-ledger COURSE_ID` | seed entries from the local state file (§5, the prerequisite half of (a)) |
| `import-inventory FILE` | seed low-confidence entries from a video inventory (§5, follow-up) |
| `drift` | **retires**, absorbed by `report` (`clm info migration` entry) |

`--json` everywhere; failure diagnostics on stderr; exit codes load-bearing
(the #965 surface contract). The `recordings-agents` info topic documents
the loop: edit → `report --json` → `ack` or re-record → the ledger commit
travels with the deck edit.

### 4.5 The manifest digest, demoted

The build-output digest stays available as the secondary
`built_output_changed` signal in `report` when a manifest is at hand. It is
no longer the primary verdict and never a prerequisite. `slide_digest` keeps
being stamped in the local state for that purpose.

## 5. Prerequisites versus follow-ups — the (a)–(d) verdict

| gap | verdict | why |
|---|---|---|
| (c) source-anchored provenance | **prerequisite, first** | everything else keys on it; the commit is already stamped on 123/197 parts, so the new cost is the deck path, the member fingerprints and the committed home |
| (b) severity | **prerequisite, cheap form** | a pure function of (ledger members, HEAD deck); the five classes above are exactly the tracker's vocabulary. Finer prose-similarity ranking: not needed to retire the tracker |
| (d) acknowledgement | **prerequisite** | without it the report is the noise the author already refused; and the ack must be *shared*, which the machine-local state file cannot be |
| (a) backfill, dashboard era | **prerequisite** | the tracker's subject *is* the 2026-04-cohort recordings, all in `machine-learning-azav-de.json`. Seeding needs no video: anchor = stamped `git_commit`, else `git rev-list -1 --before=<recorded_at>`; deck = the lecture's display names resolved through the course at that commit (`Course.resolve_deck_topic` is the existing inverse); members = fingerprints of `git show <anchor>:<deck path>` (the `revision_slides` reader harvest already has) |
| (a) backfill, legacy inventory | **follow-up** | 402 videos, 9 courses, mostly superseded (AZAV ML was re-recorded in 2026-04) or already "stale"; import at `mtime` confidence, then re-anchor individual decks with `harvest identify-rev` (`anchor.kind = identified`) where the mtime anchor is doubtful (Descript/Camtasia exports carry export time, not recording time) |

The minimum that retires #360: §4.1 + §4.2 + §4.3 + `report`/`ack` +
`seed-ledger`, run once on the ML-AZAV state file and committed. The
tracker then closes, and the course-repo `AGENTS.md` rule becomes "run
`clm recordings report` and `ack` what you decide to keep".

## 6. Rejected alternatives

- **Keep the build-output digest as the primary verdict** and add severity
  from the manifest's per-file hashes. Measured: 26 % false "changed",
  spec-bound `unknown`s, topic-granular, needs a build, and topic ids are
  ambiguous across cohort-archive modules. The manifest was designed for the
  release pipeline (which files, which topic), not for "what did the
  audience see".
- **Store nothing but the commit** (recompute fingerprints from git on every
  report). Fails for dirty-tree recordings (22 % of parts) and for any
  checkout without full history; storing ~100 short hashes per deck is
  cheaper than the special cases.
- **Acknowledgement in the local state file.** It is per machine and per
  (course, language); the tracker is shared by every editing agent. An ack
  nobody else can see is a private note, not a backlog state.
- **`identify-rev` as the default backfill.** It needs the video, OCR and
  minutes per deck, and it answers a question the dashboard era already
  answered by stamping the commit. Keep it as the upgrade path for
  low-confidence anchors.
- **A generator that writes the GitHub tracker** from `drift`. The report
  *is* the backlog; a second copy in an issue reintroduces the hand-edited
  list.
- **Automatic re-record judgment** (a threshold that says "re-record").
  Non-goal in #907, reaffirmed: the author's cosmetic/structural calls
  contradict any rule the fingerprints can express (the reasoning_effort
  one-liner).

## 7. Landmines and residues

- **Gitignore.** `.clm/*` is ignored in every course repo with explicit
  exceptions; a ledger that silently never gets committed is the #520-era
  landmine again. The `git check-ignore` warning in §4.1 is load-bearing.
- **Renames.** `clm slides rename` (#991) migrates sync-ledger sections and
  cache rows; the recordings ledger must ride the same hook, and the
  topic-rename half (#1002) too. Until then a renamed deck reports
  `orphaned` — truthful, and the tracker's "moved verbatim, re-point" entries
  show the case is real.
- **Split decks.** A `.de`/`.en` pair shares one stem and one entry; the
  recorded `lang` selects which side's members are stored. Companion
  narration lives in `voiceover/` files that the sync engine already maps to
  members, so `narration` severity needs no extra plumbing.
- **Hash version.** Reuse the sync ledger's rule: entries under an older
  `hash_version` are recomputed from `anchor.commit` when present, otherwise
  reported `unverifiable`, never trusted.
- **The local state's `section_id` is `None`** everywhere today; the ledger
  does not depend on it. Seeding resolves decks by display names at the
  anchor commit, which is the only identity the pre-#208 parts carry.
- **The 22 false verdicts** are not root-caused; if the secondary flag turns
  out to be noise in practice, drop it rather than chase it.

## 8. Sequencing and issues

1. **Ledger + record-time stamping** (schema, `hash_version`, dashboard
   write path, gitignore warning) — **#1004**.
2. **`report` + `ack` + `recordings-agents` topic; `drift` retires** — the
   remaining half of **#965**, now unblocked.
3. **`seed-ledger`** from the local state files; run on
   `machine-learning-azav-de` and `-2026-08-de`; commit; close #360; update
   the course-repo `AGENTS.md` rule — **#1005**.
4. **Follow-ups**: `import-inventory` + `identify-rev` re-anchoring
   (**#1006**); rename migration of the recordings ledger (**#1007**).

Each step is small enough for one resolve-issue pass; 1 → 2 → 3 in order,
4 after 1. #907 stays open as the umbrella until step 3 closes #360.

## 9. Amendments

| Date | Change | Sections | Nature |
|---|---|---|---|
| 2026-09-25 | Initial decision (discussion session S8) | all | — |
