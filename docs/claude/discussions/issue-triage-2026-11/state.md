---
status: active
owner: maintainers
updated: 2026-11-17
review-by: 2027-05-17
---

# State: open-issue triage and fix batch, November 2026

**S25.** The owner asked for a triage of the open clm issues: which can be
resolved now, which need design, which bring the most value. The agent then
fixed them in parallel with subagents, and settled the remaining decisions
with the owner.

**S26** resumed the thread. It covered #1280, #1270, the #955 follow-up, the
design and implementation of #1140 (`clm release adopt`), and the migration
of the CppCourses student repos to release channels.

Task state is in the PRs and issues below; there is no handover. The adopt
design and its field run are in `docs/claude/design/release-adopt.md`.

## Settled (S25)

- **Order of work.** Sync groups 1 and 2 were done in S25. Group 3, the
  positional-engine defects, waits until after phase D, because
  positional-only fixes would be thrown away in phase E.
- **Agent corrections.**
  - #1228 was already largely done, which the owner remembered.
  - #1193: `uv.lock` ships to students on purpose; closed as intended.
- **Decisions, all as the agent recommended.**
  - #1216: lift the `idless_anchor` refusal in phase D.
  - #1195: keep the epoch bump.
  - #1090: closed, with follow-up #1270.
- **Whisper (#1105).** A measured A/B made
  `condition_on_previous_text=False` the default. The old cache key was kept
  (option A), so there is no mass re-transcription. Shipped in #1279; the
  numbers are on #1105.
- **#1280.** Narration reaches the model as logical bullets.

## Settled (S26)

- **#1140, the seven decisions D1 to D7**, all as the agent recommended:
  - a separate `adopt` command;
  - adopt files that differ, with the digest taken from the destination;
  - refuse a topic with missing files unless `--deliver-missing`;
  - freeze `code/`, without making it evergreen;
  - an explicit selector is required;
  - guard the old push path;
  - `source_commit = published:<sha>`.
- **The owner's two extensions.**
  - **D6:** adopt removes the output tree's `origin`. The agent added that
    `clm git init` would re-add it from `<remote-path>`, so `init`, `push`
    and `sync` also refuse a channel's origin unless overridden.
  - **D4:** file the owning-topic question for exports. It became #1288,
    with the owner's remark that ownership must be optional for top-level
    material.
- **The implementation's two deviations**, both confirmed by the owner:
  - a run that adopts nothing records nothing, and does not freeze the
    skeleton;
  - guard refusals exit 1.
- **The CppCourses migration.**
  - **What happened:** all 114 topics were adopted in both languages, as
    students had them. Of the 8 topics with real unpublished edits, the
    owner shipped the generics decks (`function_templates`,
    `class_templates`, `concepts`) and `my_vector_stl` now.
  - **Deferred:** `exceptions_details`, `stack_unwinding`, `unique_ptr`,
    `destruction`, and the six topics whose HTML differs only in printed
    pointer addresses wait until the current cohort is done. The owner put
    that one or two weeks after 2026-11-17.
- **Agent correction, the "other checkout".** The agent read the student
  repos being 4 commits ahead of the build tree as a second checkout. The
  owner explained that an agent on this machine had pushed selected changes,
  evidently from a temporary clone; no other clone with that origin exists.
- **Agent correction, #1293.** The agent filed #1293 saying `push` and
  `sync` exit 0 on failure. `sync` (and `release sync --push`) already
  exited 1; the affected commands were `push`, `commit`, `reset` and `init`.
- **#1293, fixed now.** The owner asked whether to fix it straight away;
  it was small, so it was fixed in #1295.

## Shipped

- **S25:** #1253–#1265, #1267–#1269, #1271, #1279, #1281; saves #1282 and
  #1283.
- **S26 clm PRs:**

  | PR | What |
  |---|---|
  | #1285 | #1280 |
  | #1291 | #1270; item 4 is recorded on the issue as not measured |
  | #1290 | #1286, the `//` parity check |
  | #1287, #1289 | the adopt design |
  | #1292 | `clm release adopt`; a Linux-only test assertion was fixed in CI |
  | #1294 | the `--refreeze` synopsis |
  | #1295 | #1293 |

- **S26 issues filed:** #1284 (#955 follow-up), #1286, #1288, #1293.
- **CppCourses:** #242 (the channels), #243 (the ledgers), #244
  (`session-knowledge.md` rewritten for the release-channel workflow).
- **Not yet in a PyPI release.** clm 1.40.0 reads the adopted freeze
  record, so the deferred refreeze does not wait for a release.

## Open

- **The deferred CppCourses refreeze**, after the cohort. The commands are
  in CppCourses `session-knowledge.md`. Inferred but not exercised: a deck
  added to a section changes that section's frozen `CMakeLists.txt`,
  which then needs `--refreeze-skeleton`.
- **Open issues:**
  - #1284: reconcile the GitLab default branch after `git push --channel`;
  - #1288: an optional owning topic for global dir-group subdirs;
  - #1216 and sync group 3: phase D.
- **The "needs design first" list from S25:** #1140 is done. The rest
  (#1050, #1094/#1091, #1100/#1104 and the lower-urgency items) is
  unchanged and is listed in the S25 transcript.

## Next boundary

Either the deferred CppCourses refreeze, once the owner says the cohort has
finished, or phase D after the dogfood window, whichever comes first.
