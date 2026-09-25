# CLM {version} — Recording Provenance Reference

Version-accurate reference for how CLM records **what a video showed** and
answers "which recordings went stale after these slide edits?". It covers the
two stores (the machine-local state file and the committed recordings
ledger), the ledger schema and anchor kinds, the severity classes of the
re-recording report, the recordings config, and the transition from `drift`
to `report`.

The workflow itself (dashboard, processing, takes, backends) is documented in
`docs/user-guide/recordings.md`; the command surface in `clm info commands`
(section `clm recordings`). Design: `docs/claude/design/recordings-rerecord-backlog.md`.

## Two stores, two questions

| Store | Location | Scope | Answers |
|---|---|---|---|
| **Local state file** | `<user-config>/clm/recordings/<course-id>.json` (`%LOCALAPPDATA%\clm\recordings\` on Windows, `~/.config/clm/recordings/` on Linux/macOS) | one machine, one `(course, language)` | *workflow*: which raw file is which lecture part, take history, processing status, the build-output digest stamped at record time |
| **Recordings ledger** | `<topic>/.clm/recordings-ledger.json`, committed with the course | the course, every editing agent | *provenance*: which commit and which member fingerprints each recorded part showed; the acknowledgement state |

The split is deliberate. The state file names files on one disk and is never
committed, so nothing course-level may live only there: a re-record backlog
that one machine can see is a private note, not a backlog. The ledger holds
exactly the fields the report needs and travels with the deck it describes —
a deck edit and its ledger change land in the same commit, and a renamed or
deleted deck shows up as `orphaned` instead of vanishing.

Both are written by the recordings dashboard at record time: the local state
file first, then the ledger entry with the same `recorded_at`. A ledger
failure is logged and never blocks a recording.

## The recordings ledger

### Location and gitignore

Same directory and convention as the sync ledger (`.clm/sync-ledger.json`).
Course repos ignore `.clm/*` with explicit exceptions, so each course repo's
`.gitignore` needs one more line:

```
**/.clm/*
!**/.clm/sync-ledger.json
!**/.clm/recordings-ledger.json
```

CLM checks `git check-ignore` after every ledger write and warns when the file
would be ignored (a ledger that silently never gets committed is the failure
mode this guards against). An already-tracked ledger never warns.

The build skips `.clm/` entirely, so the ledger is never a build input.

### Schema

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
          "members": {"id:intro": "<sha256>", "pos:intro/code/0": "<sha256>", "…": "…"},
          "hash_version": 1
        }
      ],
      "ack": null
    }
  }
}
```

- **`decks`** is keyed by the deck's language-free stem (`slides_x.de.py` and
  `slides_x.en.py` → `slides_x`), exactly as the sync ledger keys its
  sections. A split deck has **one** entry; `lang` on each part records which
  side was shown and therefore which side's members are stored.
- **`parts`** hold one entry per recorded part of that deck in that language.
  A retake replaces the entry for the same `(part, lang)`; the take history
  stays in the local state file.
- **`recorded_at`** matches the local state file's stamp (local time,
  seconds).
- **`course_id`** is the state-file course id the part was recorded under
  (`<spec slug>-<lang>`).
- **`ack`** is the deck-level acknowledgement block: `{"at", "note",
  "members", "hash_version"}` — the member fingerprints at which someone
  decided *not* to re-record. Written by `clm recordings ack`; `null` until
  then.

The file is canonical JSON (sorted keys, two-space indent, trailing newline),
so a merge conflict is a genuine same-part conflict and stays line-local.

### Anchors

`anchor.kind` records **how confident** the source anchor is, from best to
worst:

| kind | meaning | written by |
|---|---|---|
| `commit` | the tree was clean at record time; the commit *is* what was shown | dashboard |
| `commit-dirty` | the commit under-describes what was shown (uncommitted edits at record time); the stored `members` are exact, the commit is the nearest committed state | dashboard |
| `time` | no commit was stamped; the anchor is the last commit before `recorded_at` | `seed-ledger` |
| `mtime` | anchored by a video file's modification time (legacy inventory) | `import-inventory` (planned) |
| `identified` | the revision was identified from the video itself (`clm harvest identify-rev`) | re-anchoring (planned) |
| `unanchored` | no commit could be captured at all (not a git checkout); the members are the only evidence | dashboard |

`anchor.commit` is the full sha or `null`; `anchor.dirty` is `true` only for
`commit-dirty`.

### Members and `hash_version`

`members` maps each member of the recorded side to its **content
fingerprint** — the same function the sync ledger records
(`clm.slides.doc_identity.content_fingerprint`: every byte of the cell except
the `slide_id` attribute), keyed by the member's handle (`id:<slide_id>` for
id-bearing cells, `pos:<group>/<kind>/<ordinal>` for id-less ones). They are
stored **always**, not only for dirty trees, so the report never depends on
git history being available (shallow clones, squashed cohorts).

An empty `members` map means the fingerprints could not be computed at
record time (the deck was not a parseable `.de`/`.en` split pair); such an
entry reports as `unverifiable`.

`hash_version` is the sync ledger's fingerprint-function version, stamped
per part (and per `ack`) and repeated on the envelope. The rule is the sync
ledger's: an entry recorded under an **older** version is never trusted. It
is recomputed from the deck as committed at `anchor.commit` when one exists,
otherwise it reports as `unverifiable`. A part without its own
`hash_version` inherits the envelope's.

## Severity classes (the report vocabulary)

`clm recordings report` compares the deck at `HEAD` with the ledger entry's
members and assigns one class per deck, highest wins:

| class | rule |
|---|---|
| `structural` | an id-bearing member was added or removed, the member order changed, or a **code** member's fingerprint changed |
| `visible` | a presentation (markdown slide/subslide) member's fingerprint changed |
| `narration` | only voiceover companion / narration members changed |
| `notes` | only trainer-notes members changed |
| `none` | every member matches |

Every row also carries `changed/total` member counts, the commits since the
anchor that touched the deck path (when the anchor is a commit), and the ack
state: `acknowledged` while `HEAD` matches the ack'ed fingerprints,
`drifted-since-ack` (with the new severity) once it moves further. Rows
whose ledger entry names a deck that no longer exists are `orphaned`;
`--all` adds `unrecorded` decks (on disk, no entry). With a build manifest
present, an optional `built_output_changed` flag adds the build-output
digest comparison as a secondary signal — never a prerequisite.

There is **no automatic re-record judgment**: the classes are the evidence,
the author decides, and `ack` records the decision at member granularity so
a later edit of a different slide re-surfaces the deck while the acknowledged
edit stays acknowledged.

The classes are documented here from {version}; the `report` and `ack`
verbs that compute them ship with the remaining half of #965.

## Recordings configuration

Courses are declared in CLM's TOML config under `[recordings]` (full field
list in `docs/user-guide/configuration.md`):

```toml
[recordings]
obs_output_dir = "/path/to/obs/recordings"
active_course = "python-basics"

[[recordings.courses]]
id = "python-basics"
name = "Python for Beginners"
spec_file = "/path/to/python-basics/course.xml"
course_repo = "/path/to/python-basics"
input_dir = "/path/to/obs/recordings"
output_dir = "/path/to/processed/python-basics"
```

`spec_file` is what lets the dashboard resolve a recorded deck's source file
and git commit at arm time; without a spec the state file still records the
workflow fields but no anchor and no ledger entry can be written. The state
file's `course_id` is the course's `id` plus the recording language
(`python-basics-de`).

## From `drift` to `report`

`clm recordings drift` compares the build-output digest stamped in the local
state file with the topic's digest in the current `.clm-manifest.json`. It is
topic-granular, bound to whichever spec the manifest was built from, needs a
build, and answers `unknown` for every part that predates digest stamping.
Measured on a real course it was wrong in the "changed" direction for a
quarter of the answerable parts.

`report` replaces it with the source comparison above: per deck, no build,
the tracker's own severity vocabulary, and an acknowledgement state. `drift`
retires without an alias when `report` lands (see `clm info migration`); the
build-output digest survives only as the secondary `built_output_changed`
flag.
