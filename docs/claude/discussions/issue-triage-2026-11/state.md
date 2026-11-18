---
status: active
owner: maintainers
updated: 2026-11-18
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

**S27** resumed it. It covered #1288 (owning topics for global dir-group
subdirs), #1284 (GitLab default branch after `git push --channel`), the 1.41.0
release, and the owning-topic annotation of CppCourses with two follow-up
issues there. After the first save it ranked the "needs design" items and
designed and shipped #1104 (harvest addressed through the course spec).

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

## Settled (S27)

- **#1288, five decisions** (recorded on the issue):
  1. one owner per subdir. The owner's extension: the value is a candidate
     list, and the **first candidate in course order whose section is enabled**
     owns the files, so a project still ships when its earliest section is
     disabled. The agent judged that freezing raised no complication (the
     owner is resolved per build, and frozen topics are never re-read);
  2. a subdir with no enabled candidate is not copied (a warning);
  3. an unknown topic id is a spec error;
  4. frozen channels: turning ownership on moves nothing by itself; owned
     files follow their topic's `--refreeze` (documented in `releases.md`);
  5. annotate CppCourses `completed` and `starter_kits`.
- **The rule for projects** (owner): a shipped project the slides don't
  point to is a bug; the slides must at least say where students find it.
  Some shipped projects may be unused.
- **CppCourses mapping** (owner):
  - keep the 16 unreferenced projects for now, unowned; the C++ course
    revision before the next cohort settles them;
  - the IDE topics (`vs vscode clion`) are candidate owners of
    `hello_world` (ahead of `docker_images`), `hello_world_sk` and
    `fizz_buzz_sk`;
  - track it as one CppCourses issue, plus a revision issue that also covers
    more modern C++.
- **Release 1.41.0** at the owner's request, after #1288 and #1284.
- **Agent correction, the C++ toolchain.** In #246 the agent claimed the
  container's GCC 12 lacks `<format>`/`<print>`, from a CMake comment. During
  this save it found that the course image ships g++ 15 (clm
  `docker/README.md`), so both headers already work in the notebooks; the
  constraint is the students' local compilers. It posted a correction comment
  on #246 and, at the owner's request, fixed the issue body too.

- **Design priorities (S27, second part).** The agent ranked the open
  design items; the owner picked the first. The ranking, for a later pick:
  1. #1104 with #1100 and #1056, harvest over a whole course (done: #1104);
  2. #1050 configurable output root, with #1252 (`clm cache relocate` now,
     root-relative keys decided together with #1050);
  3. #1002 `clm course rename-topic`, timed by the C++ revision (#246);
  - #1163 (agenda Word export) moves up if an AZAV certification is near;
    #1094/#1091 are discussion-sized; #964, #1197, #1006, #919, #800 low.
  Sync phase D is not a design item but remains the highest-leverage work.
- **#1104, eight decisions D1–D8**, all as the agent recommended (recorded
  on the issue): videos only from the recordings layout computed from the
  spec as the dashboard does (not the per-machine state file, not globs);
  `--spec` addressing on `report`/`task`/`accept`/`verify`, batch only for
  `report`; exact topic ids, sections by id or 1-based position; parts by
  number, gaps are notes, unnumbered-plus-numbered is a deck error; one
  envelope, a failing deck never stops the batch, exit 2 if any failed;
  `--resolve-only`; `--unharvested` from the sync ledger's
  `harvest:<fingerprint>` provenance (replacing `--since`); docs.

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
- **S27 clm PRs:** #1298 (#1288), #1299 (#1284), #1300 (5 mitmproxy
  replay tests marked `load_fragile`: they missed the 30 s `mitmdump` start
  under the full pre-release run and passed alone), #1301 (release 1.41.0,
  published to PyPI and GitHub 2026-11-17; one CI worker crash on an
  already-`load_fragile` test passed on rerun).
- **S27 CppCourses:** issue #245 (owner mapping) with PR #247 (open, for
  owner review; no CI checks run on it): 10 `<subdir topic>` entries and a
  `session-knowledge.md` note. Issue #246: the course revision (unreferenced
  projects, modern C++).
- **S27, second part:** PR #1302 and #1303 (the first save and its state
  fix); PR #1304 (#1104, merged 2026-11-18, not yet in a release): the
  batch `report --spec`, `--spec --topic [--deck]` on the single-deck verbs,
  18 tests, field-checked on cpp-for-programmers EN (63 of 114 decks mapped
  to all 89 videos; a real `strings` report, exit 1). The MCP harvest tools
  stay per-deck (a possible follow-up).
- All nine owner topics were already frozen in both channels, so #247
  changes nothing for students today. A future `--refreeze` of `vs`/
  `vscode`/`clion`, `catch2` or `program_structure` now also replaces
  starter kits students write code in.

## Open

- **The deferred CppCourses refreeze**, after the cohort. The commands are
  in CppCourses `session-knowledge.md`. Inferred but not exercised: a deck
  added to a section changes that section's frozen `CMakeLists.txt`,
  which then needs `--refreeze-skeleton`.
- **CppCourses PR #247** awaits the owner's review and merge.
- **CppCourses #246**, the course revision before the next cohort. It also
  holds the idea of a `clm validate` check that an owning topic's slides
  mention its project; not filed in clm yet, depends on #246.
- **Open issues:** #1216 and sync group 3: phase D.
- **Design items:** the S27 ranking above; #1104 is done, #1050 (with
  #1252) and #1002 are next in the agent's order.
- **#1296 and #1278** (master-red and nightly trackers) are stale: master
  and the nightly have been green since. The agent asked whether to close
  them; not answered.
- **Release:** #1104 ships with the next clm release; until then the
  `--spec` options work only from a clm checkout.

## Next boundary

S27 ended with #1104 merged. Next: the next design item (#1050 with #1252,
or #1002), the deferred CppCourses refreeze once the cohort has finished,
or phase D after the dogfood window.
