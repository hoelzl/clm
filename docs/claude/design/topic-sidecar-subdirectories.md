# Topic Sidecar Subdirectories (`voiceover/`, `cassettes/`)

**Status:** Design — not yet implemented
**Author:** AI assistant (with M. Hölzl)
**Date:** 2026-06-04
**Related:** `cassette-language-fallback.md`, `split-voiceover-hardening.md`,
`sync-content-anchor-identity.md`, `http-replay.md`

## 1. Problem

The recent landing of HTTP-replay cassettes, DE/EN split decks, and
separated voiceover companions has cluttered each topic directory with files
that are *not* core authoring source. A topic such as
`module_550_ml_azav/topic_070_rag_introduction/` now looks like:

```
drawio/                                                       # output companion
img/                                                          # output companion
slides_010_what_is_rag.de.http-cassette.yaml                 # sidecar (cassette)
slides_010_what_is_rag.de.http-cassette.yaml.staging-…completed  # transient
slides_010_what_is_rag.de.py                                 # CORE
slides_010_what_is_rag.en.http-cassette.yaml                 # sidecar
slides_010_what_is_rag.en.http-cassette.yaml.staging-…completed  # transient
slides_010_what_is_rag.en.py                                 # CORE
```

Once voiceovers are extracted to companions, two more files
(`voiceover_010_what_is_rag.de.py` / `.en.py`) join the pile. The author edits
two files (`slides_*.de.py`, `slides_*.en.py`) but wades through six-plus.

**Goal.** Let a topic directory contain only **core source**
(`slides_*.de.py` / `slides_*.en.py`) plus genuine **output companions**
(`img/`, `drawio/`, loose data files copied verbatim to output), by relocating
the **authoring sidecars** into subdirectories:

- cassettes → `cassettes/` (with `_cassettes/` still accepted)
- voiceover companions → `voiceover/`

Target layout:

```
topic_070_rag_introduction/
├── cassettes/
│   ├── slides_010_what_is_rag.de.http-cassette.yaml
│   └── slides_010_what_is_rag.en.http-cassette.yaml
├── voiceover/
│   ├── voiceover_010_what_is_rag.de.py
│   └── voiceover_010_what_is_rag.en.py
├── drawio/
├── img/
├── slides_010_what_is_rag.de.py
└── slides_010_what_is_rag.en.py
```

**Constraints (non-negotiable).**

1. The current flat layout must keep working unchanged (backward compatible).
2. The extraction/inlining tools (`extract`, `inline`, `sync`, `split`,
   `unify`, `validate`) and the build must support both layouts.
3. No data loss; round-trip invariants (`extract→inline`, `split→unify`,
   `extract→build-merge`) preserved in either layout.

## 2. Three categories of topic file

The design rests on classifying every non-core file precisely:

| Category | Examples | In course file map? | Copied to output? | Reaches worker? |
|---|---|---|---|---|
| **Core source** | `slides_*.de.py`, `slides_*.en.py` | yes (as `NotebookFile`) | processed | yes (merged) |
| **Output companion** | `img/`, `drawio/`, loose data files | yes (`DataFile`/`ImageFile`) | **yes** | as needed |
| **Runtime sidecar** | `*.http-cassette.yaml` (+ `*.staging-*`) | yes (output-suppressed) | no | **yes — kernel reads at runtime** |
| **Authoring sidecar** | `voiceover_*.py` | host-side only | no | **no — merged host-side** |

The two sidecar categories differ in one load-bearing way that dictates how
the build must treat their subdirectories (see §4).

## 3. How things work today (verified)

### 3.1 Cassettes already support a subdirectory — `_cassettes/`

`NotebookFile` resolves cassette paths with a nested-first, sibling-fallback
rule, auto-detected by **directory presence** (no flag):

- `cassette_path` (`notebook_file.py:117-133`): `<topic>/_cassettes/<stem>.http-cassette.yaml`
  if it exists, else the sibling `<topic>/<stem>.http-cassette.yaml`, else `None`.
- `expected_cassette_path` (`:136-152`): write target — `_cassettes/` if that
  dir `is_dir()`, else sibling.
- `replay_cassette_path` (`:166-195`): split `.de`/`.en` deck falls back to the
  base (bilingual) cassette via `_base_cassette_stem(self.path.stem)`, searching
  `_cassettes/` then sibling. **`_base_cassette_stem` operates on the *notebook*
  stem, not the cassette filename**, so it is unaffected by where cassettes live.
- `cassette_relative_name` (`:155-163`): `cassette.relative_to(self.path.parent).as_posix()`
  → yields `_cassettes/slides_….http-cassette.yaml`, which the worker bootstrap
  uses as the kernel-cwd-relative path.

Staging files (`.staging-<id>` + `.staging-<id>.completed` markers) are written
**sibling to the canonical cassette** (`http_replay_cassette.py` `resolve_paths`),
so they already live inside `_cassettes/` when the canonical does. The pre-build
orphan sweep (`course.py _sweep_orphan_cassette_staging_files`) and the mitmproxy
staging merge both glob `canonical.parent/…staging-*`, which is correct in either
layout because they key off the canonical's own parent.

Build discovery treats `_cassettes/` as a **runtime sidecar**:
`SKIP_DIRS_FOR_OUTPUT = SKIP_DIRS_FOR_COURSE | {"pu","drawio","_cassettes"}`
(`path_utils.py:61`) — so cassette files **stay in the course map** (the kernel
needs them at runtime) but are **suppressed from output**, reinforced by the
`*.http-cassette.yaml` regex in `SKIP_OUTPUT_FILE_PATTERNS`.

> The user's premise was right but slightly mis-named: the existing folder is
> `_cassettes/` (leading underscore), not `cassettes/`. The example topic simply
> never opted in.

### 3.2 Voiceover companions are hard-wired as siblings

`companion_path(slide_path)` (`voiceover_tools.py:174-188`) is a pure
*sibling* derivation: strip a known prefix (`slides_`/`topic_`/`project_`) and
return `slide_path.with_name(f"voiceover_{suffix}.py")`. For a split half
`slides_010_intro.de.py` → `voiceover_010_intro.de.py` — **the `.de`/`.en` tag
is preserved** (critical: the two halves' companions must be distinct files).

Callers of `companion_path` (all assume sibling):

| Site | Purpose | Read/Write |
|---|---|---|
| `voiceover_tools._plan_extraction` / `extract_voiceover` / `extract_voiceover_pair` | compute write target + clobber check | **W** |
| `voiceover_tools.inline_voiceover` | locate companion, then delete/rewrite | **R+W** |
| `core/course_files/notebook_file.companion_voiceover_path` | build-merge discovery probe | **R** |
| `cli/commands/voiceover.py sync` | read baseline / write merged narrative | **R+W** |
| `slides/split.py _plan_companion_split` / `_plan_companion_unify` | split/unify companions in lockstep | **R+W** |
| `slides/validator.py validate_companion_parity` | `for_slide` parity across halves | **R** |

`pairing.py` (`derive_split_twin`, `derive_split_pair_from_stem`,
`_is_split_slide_file`) excludes companions from deck-pairing by the filename
test `name.startswith("voiceover_")`. Output suppression is by the
`voiceover_*.py` regex/glob in `path_utils.py` (`SKIP_OUTPUT_FILE_PATTERNS:100`,
`SKIP_OUTPUT_FILE_GLOBS:110`).

### 3.3 The enabling fact: voiceover merge is host-side

`ProcessNotebookOperation.payload` (`process_notebook.py:332-339`) reads the
slide text **on the host**, probes `companion_voiceover_path`, and if present
calls `merge_voiceover_text(data, companion_text)` — the **worker receives
already-merged notebook text**. `compute_other_files` (`:159-206`) explicitly
*excludes* the companion (line 182) and everything matching
`is_ignored_file_for_output`, so the raw companion never reaches the kernel cwd.
The authoritative comment at `path_utils.py:91-99` documents exactly this.

**Consequence:** unlike a cassette, a voiceover companion has **no downstream
consumer** — not output, not the kernel `other_files`, not runtime. It is found
solely by the host-side `companion_voiceover_path` *direct probe*, which does
**not** depend on the topic directory walk. Therefore a `voiceover/` subdirectory
can be **fully excluded from course discovery** and the merge still works.

## 4. Design

### 4.1 Two resolution concerns, mirrored for both sidecar types

Today `companion_path` conflates *naming* with *location*. Split it (mirroring
the cassette `cassette_path` vs `expected_cassette_path` split):

```
companion_name(slide_path)        -> str        # pure filename rule (unchanged)
resolve_companion(slide_path)     -> Path|None  # READ: folder-first, sibling fallback, else None
expected_companion(slide, layout) -> Path       # WRITE target (see precedence §4.4)
```

- **Read** (`inline`, build-merge probe, `sync` baseline, `validate` parity,
  `split`/`unify` sources): always `resolve_companion` —
  `<topic>/voiceover/<name>` if it exists, else sibling `<name>` if it exists,
  else `None`. **Location-config-free**: works for any topic regardless of how
  it is organized.
- **Write** (`extract`, `sync` create, `split`/`unify` targets, `tidy`): always
  `expected_companion`, resolved by the precedence in §4.4.

`companion_path` is retained as a thin backward-compatible alias for
`expected_companion(slide, layout=AUTO)` so external importers keep working;
internal call sites move to the explicit read/write functions.

Cassettes gain the symmetric change in `notebook_file.py`: `cassette_path`,
`expected_cassette_path`, and `replay_cassette_path` learn the new
**`cassettes/`** directory, preferring it, then legacy **`_cassettes/`**, then
sibling. A single helper centralizes the candidate order:

```
_sidecar_dirs("cassettes") == ("cassettes", "_cassettes")   # read order
_sidecar_dirs("voiceover") == ("voiceover",)                # + sibling fallback always
```

### 4.2 Filenames are unchanged inside the folder

Files keep their current names inside the subdirectory:
`voiceover/voiceover_010_intro.de.py`, `cassettes/slides_010_intro.de.http-cassette.yaml`.
Rationale:

- **Collision safety.** The `.de`/`.en` tag stays in the name, so split halves
  never overwrite each other (`split.py` writes both companions in lockstep and
  relies on distinct paths).
- **Safety nets stay armed.** The `voiceover_*.py` output-suppression regex and
  the `name.startswith("voiceover_")` pairing-exclusion keep matching by
  basename, independent of directory — defense in depth even though §4.3 makes
  them moot for the foldered case.
- **One naming rule everywhere.** `companion_name` is identical in both layouts;
  only the parent directory changes. (The cosmetic `voiceover/voiceover_…`
  redundancy is acceptable and self-describing.)

### 4.3 Build discovery: the asymmetry is intentional

| Dir | Added to | Effect |
|---|---|---|
| `cassettes/` | `SKIP_DIRS_FOR_OUTPUT` (alongside `_cassettes`) | in course map (kernel needs it), suppressed from output |
| `voiceover/` | `SKIP_DIRS_FOR_COURSE` | **fully excluded from the walk** — never a `DataFile`, never output, never in source mounts |

`voiceover/` can be walk-excluded because the merge is host-side (§3.3); the
build still finds companions via the direct `companion_voiceover_path` probe.
This is *cleaner* than the cassette case — foldered companions stop leaking into
Docker source mounts entirely (they currently do, harmlessly). Cassettes cannot
be walk-excluded: the kernel reads them at runtime, so they must remain in the
map and reach the worker; `cassettes/` therefore mirrors `_cassettes/` exactly.

`add_files_in_dir` (`topic.py:336-340`) already gates subdir recursion on
`is_ignored_dir_for_course`, so adding `voiceover` to `SKIP_DIRS_FOR_COURSE`
needs **no walker change** — the existing condition skips it.

The `SKIP_OUTPUT_FILE_PATTERNS`/`GLOBS` regexes are filename-based and already
match inside subdirectories, so they need no change. `_base_cassette_stem`
needs no change (operates on the notebook stem).

> **Trade-off of the non-underscore choice.** A topic that legitimately contains
> a *content* directory literally named `voiceover/` or `cassettes/` would now be
> treated as a sidecar dir. In the course-materials domain this is highly
> unlikely, and it is documented. If it ever bites, the course-wide config
> (§4.4) can carry a custom directory name. (The underscore-prefixed
> `_cassettes/` exists precisely to avoid this class of collision; it remains
> accepted.)

### 4.4 Opt-in: per-topic presence **and** course-wide default

**Read** never needs configuration — it dual-probes (folder-first, sibling
fallback) always. Configuration only chooses the **write target** for new
sidecars. Precedence (highest wins):

1. **Explicit CLI flag** on the authoring command — `--layout {subdir,sibling}`
   (and/or `--into <dir>`).
2. **Per-topic directory presence** — if the relevant sidecar dir already exists
   in the topic, write there. (Identical to how `_cassettes/` works today.)
3. **Course-wide default** — `[tool.clm]` key in the course repo's
   `pyproject.toml`, discovered by walking up from the target path
   (precedent: `tool.clm.cache_dir`). Env override `CLM_SIDECAR_LAYOUT`.

   ```toml
   [tool.clm]
   sidecar-layout = "subdir"   # or "sibling" (default)
   ```
4. **Built-in default** — `sibling` (backward compatible).

A single resolver implements this:

```
resolve_sidecar_layout(target_path, *, cli_override=None) -> "subdir" | "sibling"
```

Because the **build only reads** (it never creates a companion or cassette), the
course-wide default does **not** affect build output — no course-spec change is
required. The default is purely an authoring/`tidy` write-time convenience.
"Both" mechanisms thus compose with zero ambiguity: presence (rule 2) overrides
the course default (rule 3) per topic, and a flag (rule 1) overrides everything
for one invocation.

### 4.5 The `clm tidy` reorganize command

A new command moves a topic/section/whole course between layouts — the actual
"declutter" button.

```
clm tidy <path> [--layout subdir|sibling] [--dry-run]
                [--cassettes/--no-cassettes] [--voiceover/--no-voiceover]
```

- **Scope** = a file, a topic dir, a section, or a course root (recursive).
- **`--layout subdir`** (default for the command): create `cassettes/` and
  `voiceover/` as needed and move matching files in. **`--layout sibling`**
  flattens back.
- **Git-aware:** uses `git mv` when the file is tracked (falls back to a plain
  move otherwise), so history follows the file.
- **Transient hygiene:** `*.http-cassette.yaml.staging-*` and `…​.completed`
  markers are **deleted**, not moved — they are regenerated, and the orphan
  sweep keys off `canonical.parent`, so a stale sibling marker after the
  canonical moves would be orphaned. Deleting them also clears the example
  topic's `.staging-…completed` cruft.
- **`--dry-run`** prints the planned moves/deletes without touching disk.
- **Empty-dir cleanup:** removes a sidecar dir that becomes empty after a
  `--layout sibling` flatten; creates it lazily on `subdir`.
- **Idempotent:** re-running on an already-foldered topic is a no-op.

### 4.6 Ambiguity guard (both layouts present)

The dual-probe prefers the folder. If a companion/cassette exists in **both**
the folder and the sibling location, the sibling is silently ignored — a latent
data-divergence footgun. Mitigations:

- **`extract` clobber check** considers **both** candidate locations: it refuses
  (absent `--force`) if a companion exists in *either* the folder *or* the
  sibling slot, so a normal extract never creates the ambiguous state.
- **`clm validate`** gains a check that warns when a slide has a companion (or
  cassette) resolvable in more than one location.
- **`clm tidy --dry-run`** reports such duplicates so they can be reconciled.

## 5. Change-point inventory

| # | File | Change |
|---|---|---|
| 1 | `slides/voiceover_tools.py` | Split `companion_path` → `companion_name` / `resolve_companion` (read) / `expected_companion` (write). Keep `companion_path` as back-compat alias. Update `_plan_extraction`, `extract_voiceover`, `extract_voiceover_pair` to write via `expected_companion` and clobber-check **both** locations. Update `inline_voiceover` to read via `resolve_companion` and delete/rewrite at the resolved path; remove emptied `voiceover/`. |
| 2 | `core/course_files/notebook_file.py` | `companion_voiceover_path` → `resolve_companion`. `cassette_path`/`expected_cassette_path`/`replay_cassette_path` → prefer `cassettes/`, then `_cassettes/`, then sibling. |
| 3 | `infrastructure/utils/path_utils.py` | Add `"voiceover"` to `SKIP_DIRS_FOR_COURSE`; add `"cassettes"` to `SKIP_DIRS_FOR_OUTPUT`. Add `resolve_sidecar_layout()` + `_sidecar_dirs()` helpers. (Regexes/globs unchanged.) |
| 4 | `slides/split.py` | `_plan_companion_split`/`_plan_companion_unify`: read sources via `resolve_companion`, write targets via `expected_companion` (split into a foldered topic lands both halves in `voiceover/`, collision-free). |
| 5 | `slides/validator.py` | `validate_companion_parity` → `resolve_companion`. Add the §4.6 "both layouts present" warning (companion + cassette). |
| 6 | `cli/commands/voiceover.py`, `cli/commands/voiceover_tools.py`, `cli/commands/slides_sync.py` | Add `--layout`/`--into`; thread to `expected_companion`. `sync` reads baseline via `resolve_companion`, writes to the existing location or `expected_companion`. |
| 7 | `core/operations/process_notebook.py`, `core/course.py` | No logic change — `_resolve_cassette_name`, the orphan sweep, and the mitmproxy staging merge are all path-property-driven and inherit `cassettes/` automatically. **Audit** the mitmproxy staging path writes for `canonical.parent` correctness. |
| 8 | `cli/` new command | `clm tidy` (§4.5). |
| 9 | `mcp/tools.py`, `mcp/server.py` | Any MCP tool that locates a companion uses `resolve_companion`; expose layout where extract/inline are surfaced. |
| 10 | Docs / info topics | §7. |
| 11 | Tests | §6. |

## 6. Test plan

- **Resolution matrix:** {sibling, folder, *both-present*} × {cassette,
  voiceover} × {bilingual, split `.de`/`.en`} → correct read resolution and
  write target; both-present warns.
- **Round-trips in foldered layout:** `extract → build-merge → inline`;
  `split → unify`; paired `extract_voiceover_pair` lands both halves in
  `voiceover/` with distinct names and agreeing `for_slide` sets.
- **Build discovery:** a `voiceover/` dir is absent from the course file map and
  from output; a `cassettes/` dir is in the map, suppressed from output, and its
  cassette is shipped in `other_files` for replay. Split-deck base-cassette
  fallback still resolves under `cassettes/`.
- **`clm tidy`:** dry-run plan accuracy; apply (subdir) then apply (sibling)
  returns byte-identical files; staging markers deleted; `git mv` used for
  tracked files; idempotent re-run.
- **Backward compatibility:** every existing flat-layout test passes unchanged.
- **Precedence:** CLI flag > dir presence > `[tool.clm] sidecar-layout` /
  `CLM_SIDECAR_LAYOUT` > sibling.

## 7. Documentation (Info Topics Maintenance Rule)

| File | Update |
|---|---|
| `src/clm/cli/info_topics/spec-files.md` | Topic-dir layout convention: core + output companions as children; `cassettes/`/`voiceover/` as optional sidecars; course-wide `[tool.clm] sidecar-layout`. |
| `src/clm/cli/info_topics/commands.md` | `clm tidy`; `--layout`/`--into` on `voiceover extract`/`inline`/`sync` and `slides split`/`unify`; where new sidecars are written. |
| `src/clm/cli/info_topics/migration.md` | Adoption: `clm tidy <course> --layout subdir`; `_cassettes/` → `cassettes/` (both accepted). |
| `docs/user-guide/http-replay.md` | Cassettes now also `cassettes/`; `_cassettes/` legacy alias; dual-probe order. |
| `docs/user-guide/configuration.md` | `[tool.clm] sidecar-layout`, `CLM_SIDECAR_LAYOUT`. |
| `docs/developer-guide/architecture.md` | One line on the sidecar-vs-output-companion classification + the host-side voiceover merge that allows `voiceover/` to be walk-excluded. |

## 8. Rollout phases

1. **Resolver core (read).** `companion_name`/`resolve_companion`/`expected_companion`;
   cassette `cassettes/` acceptance in `notebook_file.py`; `_sidecar_dirs`. Pure
   read-path + build-merge probe. *No behavior change for flat topics.*
2. **Build discovery.** `SKIP_DIRS_FOR_COURSE += voiceover`,
   `SKIP_DIRS_FOR_OUTPUT += cassettes`. Tests for walk-exclusion + output
   suppression.
3. **Authoring writes.** Thread `expected_companion` + layout precedence through
   extract/inline/sync/split/unify/validator; clobber-both-locations; ambiguity
   warning.
4. **`clm tidy`.** Reorg command (both directions, dry-run, git-mv, staging
   hygiene).
5. **Course-wide default.** `[tool.clm] sidecar-layout` + `CLM_SIDECAR_LAYOUT`
   resolver.
6. **Docs + info topics.**

Phases 1–2 are independently shippable and unlock a manual `mkdir voiceover && git mv …`
workflow before `clm tidy` exists.

## 9. Rejected / deferred alternatives

- **Underscore-prefixed `_voiceover/` (uniform with `_cassettes/`).** Rejected
  per author preference for `voiceover/`/`cassettes/`; `_cassettes/` retained as
  a legacy alias. (Underscore would have sidestepped the §4.3 content-dir
  collision risk.)
- **Single parent sidecar dir `_clm/{cassettes,voiceover}/`.** Cleaner main
  folder but larger change (cassette resolution must learn a two-level parent)
  and a sharper break from the shipped `_cassettes/`. Deferred; could be added
  as a third accepted location later without disturbing this design.
- **Rename companions inside the folder** (drop `voiceover_` prefix, or mirror
  the slide stem). Rejected: loses the collision-safe `.de`/`.en` distinction
  and/or the filename safety nets; `slides_*`-named files risk slide
  misclassification if ever walked.
- **Course-spec attribute as the course-wide default.** Unnecessary: the build
  only reads (dual-probe), so the default is a write-time concern reachable via
  `[tool.clm]`; keeping it out of the spec avoids parse/validation churn.
```
