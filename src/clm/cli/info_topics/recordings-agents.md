# Driving the Re-recording Backlog as an Agent (CLM {version})

`clm recordings report` / `clm recordings ack` is a **read-by-default agent
toolkit** over the committed recordings ledger
(`<topic>/.clm/recordings-ledger.json`, see `clm info recordings`). The
engine owns the mechanics — reading every ledger, parsing each recorded deck
as it is in the working tree, diffing member fingerprints, counting commits
since the anchor — and **never decides whether to re-record**. You (or the
author) own that judgment; `ack` banks it at member granularity so the
report stops re-raising decided items. The shared rules every clm agent
toolkit follows (envelope shape, load-bearing exit codes, diagnostics on
stderr) live in `clm info agent-tasks`; the field-by-field command reference
is the `clm recordings` section of `clm info commands`.

## The mental model

- A **recorded part** is a ledger entry: which commit the recording was
  made at (`anchor`), and the recorded language's per-member content
  fingerprints (`members`, keyed `id:<slide_id>` / `pos:<group>/<kind>/<n>`;
  a single-file bilingual deck keys its id-less cells `cell:<kind>/<n>`).
  The machine-local state file holds the workflow (raw files, takes) and is
  never needed here.
- **Severity is a diff, not a verdict.** Per part, the ledger's fingerprints
  are compared with the deck now; the highest class among the changed
  members names the row (`structural` > `visible` > `narration` > `notes`
  > `none`). The tracker vocabulary the author used by hand — "structural",
  "reword only", "narration only", "trainer-notes only" — is exactly this
  set. A `visible` row with `1/40` changed members is a reword; the same
  class with `20/40` is a rewrite. The counts are on the row; you decide.
- **Acknowledged means "seen at these fingerprints".** `ack` stores the
  current fingerprints; the deck reads `acknowledged` until it moves
  further, then `drifted-since-ack` with the severity of the change *since
  the ack* — an edit of a different slide re-surfaces the deck, the
  acknowledged edit stays acknowledged.
- **The ledger travels with the deck.** A deck edit, its `report` outcome
  and the `ack` land in one commit. Course repos ignore `.clm/*`, so the
  ledger needs its `!**/.clm/recordings-ledger.json` exception (the report
  and `ack` warn while it is missing).

## The canonical loop

```bash
# after editing decks under a course root or one topic:
clm recordings report PATH --json        # 1. what changed since the recordings? (read-only)
# 2. per row: re-record, or decide the change does not need a new video
clm recordings ack DECK --note "reword only, covered verbally"   # 3. bank the decision
clm recordings report PATH --json        # 4. the deck is `acknowledged`; exit 0 when clean
git add -A && git commit                 # 5. ledger + deck edit travel together
```

`PATH` is a course root, a topic directory, or one deck file (an existing
`.de`/`.en` half). `DECK` is a deck file.

Exit codes are load-bearing: `report` **0** nothing needs attention, **1**
at least one row does (a recorded deck with severity other than `none` that
is not acknowledged, an `orphaned` entry, an `unverifiable` part, or a
`drifted-since-ack` deck), **2** a ledger could not be read (listed in
`ledger_errors`, diagnostics on stderr). `ack` **0** written, **2** the
deck has no recorded part or cannot be fingerprinted (nothing written).

## Reading the report

`report --json` emits a schema-1 envelope:

```json
{"schema": 1, "tool": "recordings", "verb": "report",
 "root": "…", "is_clean": false,
 "needs_attention": ["slides/module_550/topic_0400_x/slides_010_y"],
 "counts": {"visible": 3, "none": 40, "orphaned": 1},
 "ledger_errors": [],
 "decks": [ … ]}
```

Branch on the booleans, not the prose:

- `is_clean` — nothing to do; **stop**.
- `needs_attention` — the rows to work through, as `<topic-dir>/<deck>`.

Each deck row:

| field | meaning |
|---|---|
| `deck`, `topic_dir`, `deck_files` | the deck stem, its topic directory (root-relative) and the `{lang: path}` halves found on disk |
| `status` | `recorded` (has ledger parts) · `orphaned` (ledger entry, deck gone — a rename or deletion; re-point or drop the entry) · `unrecorded` (only with `--all`: a split deck with no entry — retires "confirm whether a recording exists") |
| `severity` | the highest class over the deck's parts, or `unverifiable` |
| `ack_state` | `unacknowledged` · `acknowledged` · `drifted-since-ack` · `stale-ack` (ack written under an older fingerprint version — re-`ack` after checking) |
| `ack_at`, `ack_note`, `severity_since_ack` | the ack's timestamp and note; for `drifted-since-ack`, the class of the drift since the ack |
| `needs_attention` | the row-level boolean behind the top-level list |
| `parts[]` | one entry per `(course_id, part, lang)` — see below |

Each part:

| field | meaning |
|---|---|
| `course_id`, `part`, `lang`, `recorded_at` | the recording's identity (one cohort's run of that part in that language) |
| `anchor` | `{kind, commit, dirty}` — `commit` (clean tree), `commit-dirty` (uncommitted edits at record time; the fingerprints are exact, the commit is approximate), `time` (seeded by timestamp), `unanchored` |
| `members_status` | `recorded` (stored fingerprints, current hash version) · `recomputed` (older hash version, recomputed from a clean anchor commit) · `approximate` (recomputed from a *dirty* anchor commit) · `unverifiable` (nothing trustworthy) |
| `severity`, `changed`, `total` | the part's class and `changed/total` member counts |
| `changed_members` | `{member-key: class}` for every member that differs — the slides to look at |
| `commits_since_anchor` | newest-first commits after the anchor that touched the deck halves or their voiceover companions; `null` when the anchor is not a commit or git cannot answer |
| `built_output_changed` | secondary flag (only with a manifest, see below): whether the topic's *built output* digest differs from the one stamped in the local state file. `null` when unavailable. Never a prerequisite, never decides attention |

The severity rules (design §4.2): `structural` when an id-bearing member was
added or removed, the id-bearing member order changed, or a **code**
member's fingerprint changed; `visible` when a presentation (markdown / j2)
member changed; `narration` when only voiceover / companion members
changed; `notes` when only trainer-notes members changed; `none` otherwise.
A one-sided edit of the language that was **not** recorded does not count.
An id-less member that vanished or appeared is classified from its handle
alone (`pos:<group>/<kind>/<n>`): `code` → `structural`, anything else →
`visible` — conservatively one class high for an id-less notes or
narration cell, since the recorded map carries no roles. Stamp ids on
such cells to get the exact class.

## Deciding

There is no threshold. Typical readings of the rows, in the tracker's own
words:

- `structural` with a code change or a new slide → usually re-record; if
  the recording still covers it ("covered verbally"), `ack` with that note.
- `visible` with few changed members → "reword only": `ack --note "reword
  only"`. Many changed members → treat as structural.
- `narration` → the video is fine; the narration text moved. `ack` unless
  the voiceover is what the video plays.
- `notes` → trainer notes never reach the audience: `ack`.
- `orphaned` → the deck was renamed or deleted. Until the rename migration
  lands, re-record under the new name or leave the entry as the truthful
  record of a recording that has no deck.
- `unverifiable` / `stale-ack` → re-establish the evidence: re-record, or
  `ack` after checking the video against the deck by hand.

The secondary `built_output_changed` flag needs a build manifest
(`--manifest FILE`, `--source DIR` or `--spec-file SPEC`) **and** the
machine that recorded (it joins the ledger part to the local state file by
`course_id` + `recorded_at`). It says whether templates, includes or a clm
upgrade changed the rendered bytes; it is topic-granular and spec-bound, and
was measured to read `changed` for a quarter of untouched decks, so treat it
as a hint to look, never as a reason to re-record.

## What is not here

- **No automatic re-record judgment** — a non-goal reaffirmed in the design
  (`docs/claude/design/recordings-rerecord-backlog.md` §6).
- **No writes from `report`.** Only `ack` (and the dashboard at record time,
  and `seed-ledger`) writes the ledger.
- **`drift` is gone** — it compared build-output digests, needed a build, and
  was wrong in the "changed" direction for a quarter of the answerable
  parts; `report` replaces it (`clm info migration`).
- **Recordings made before the ledger existed** have no rows until
  `clm recordings seed-ledger COURSE_ID` has been run once on that
  machine's state file (the machine that recorded; see `clm info commands`).
  Seeded parts anchor on the stamped commit, or on the last commit before
  `recorded_at` (`anchor.kind = time`) for the oldest ones.
- **No MCP mirror yet** for `report`; the CLI `--json` is the agent surface.
