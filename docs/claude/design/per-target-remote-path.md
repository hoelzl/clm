# Per-Target Remote Path for GitLab Group Support

**Status**: Draft for discussion
**Author**: Claude (Opus 4.6)
**Date**: 2026-04-08
**Scope**: `src/clm/core/course_spec.py`, `src/clm/cli/commands/git_ops.py`, `src/clm/infrastructure/config.py`

---

## 1. Goals

1. **Support GitLab group-based access control** by allowing different output targets
   to push to different remote paths (groups/namespaces).
2. **Keep backward compatibility** with existing GitHub-style specs where
   `repository-base` contains both the host and the organization.
3. **Minimal spec change**: add one new element (`<remote-path>`) rather than
   requiring per-target templates.
4. **Work with both implicit and explicit output targets**, with the understanding
   that per-target remote paths require explicit targets.

## 2. Non-Goals

- Replacing the existing `remote-template` mechanism (it remains as an escape hatch).
- Automatic creation of GitLab groups or repositories.
- Multi-host setups (all targets push to the same `repository-base` host).
- Changing how output directories on the local filesystem are organized.

---

## 3. Background: Current State

### 3.1 How remote URLs are constructed today

`GitHubSpec.derive_remote_url()` (`course_spec.py:131-194`) builds URLs from:

```
{repository_base}/{slug}-{lang}[-{suffix}]
```

- **`repository_base`**: single value per course, contains both host and org
  (e.g., `https://github.com/Coding-Academy-Munich`)
- **`slug`**: from `<project-slug>` (e.g., `python-basics`)
- **`lang`**: `de` or `en`
- **`suffix`**: empty for first/public target; `-{target_name}` for others;
  `-speaker` for speaker targets

The `remote-template` field provides an escape hatch with placeholders
`{repository_base}`, `{repo}`, `{slug}`, `{lang}`, `{suffix}`.

### 3.2 Where `repository-base` is insufficient

With GitHub, all repositories live under a single organization. The org is baked
into `repository-base`, and target suffixes disambiguate repositories:

```
https://github.com/Coding-Academy-Munich/python-basics-de           (students)
https://github.com/Coding-Academy-Munich/python-basics-de-completed (teachers)
https://github.com/Coding-Academy-Munich/python-basics-de-speaker   (editors)
```

With self-hosted GitLab, we want to use **groups** for access control. Different
groups of users get access to different groups on the server:

```
https://gitlab.example.com/azav-students/python-basics-de            (students)
https://gitlab.example.com/azav-teachers/python-basics-de            (teachers)
https://gitlab.example.com/azav-editors/python-basics-de             (editors)
```

The variable part is no longer the repo name suffix but the **path between the
host and the repository name**. This is what `<remote-path>` captures.

### 3.3 Implicit vs explicit output targets

**Implicit targets** (no `<output-targets>` in spec): the system generates
"public" and optionally "speaker" targets. These use the shared
`repository-base` and a suffix-based naming scheme. This continues to work
unchanged.

**Explicit targets** (`<output-targets>` in spec): each target has a name,
path, and optional filters. These are the targets that can have per-target
`<remote-path>` overrides.

Per-target remote paths only make sense with explicit targets. Courses that
need GitLab group-based distribution must use explicit output targets. This is
a reasonable requirement — the access structure needs to be spelled out anyway.

---

## 4. Proposed Design

### 4.1 New `<remote-path>` element

Split the current `repository-base` (which conflates host + org) into two parts:

```xml
<!-- Current (still supported, backward compatible) -->
<github>
    <repository-base>https://github.com/Coding-Academy-Munich</repository-base>
</github>

<!-- New: host and path separated -->
<github>
    <repository-base>https://gitlab.example.com</repository-base>
    <remote-path>azav-editors</remote-path>
</github>
```

The `<remote-path>` element is optional. When absent, `repository-base` is used
exactly as today (the combined host+org form). When present, the default URL
pattern becomes:

```
{repository_base}/{remote_path}/{repo}
```

### 4.2 Per-target `<remote-path>` override

Each `<output-target>` can override `<remote-path>`:

```xml
<github>
    <repository-base>https://gitlab.example.com</repository-base>
    <remote-path>azav-editors</remote-path>   <!-- default for targets without override -->
</github>

<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <kinds><kind>code-along</kind></kinds>
        <remote-path>azav-students</remote-path>
    </output-target>

    <output-target name="teachers">
        <path>./output/teachers</path>
        <kinds><kind>code-along</kind><kind>completed</kind></kinds>
        <remote-path>azav-teachers</remote-path>
    </output-target>

    <output-target name="editors">
        <path>./output/editors</path>
        <kinds><kind>code-along</kind><kind>completed</kind><kind>speaker</kind></kinds>
        <!-- Uses default remote-path from <github>: azav-editors -->
    </output-target>
</output-targets>
```

### 4.3 Suffix suppression

Currently, non-first targets get a `-{target_name}` suffix appended to the
repository name. This exists because all repos live in the same GitHub org and
need unique names.

When a target has its own `<remote-path>` (different from the course-level
default), the suffix is **suppressed** — the group already distinguishes the
repository:

| Target   | Remote path      | Repo name           | Full URL                                              |
|----------|------------------|---------------------|-------------------------------------------------------|
| students | `azav-students`  | `python-basics-de`  | `https://gitlab.example.com/azav-students/python-basics-de` |
| teachers | `azav-teachers`  | `python-basics-de`  | `https://gitlab.example.com/azav-teachers/python-basics-de` |
| editors  | (default)        | `python-basics-de`  | `https://gitlab.example.com/azav-editors/python-basics-de`  |

Targets **without** their own `<remote-path>` that share the course-level
default keep the suffix to avoid collisions (backward compatible).

**Rule**: A target's suffix is suppressed when the target has an explicit
`<remote-path>` that differs from the course-level `<remote-path>`. When
`<remote-path>` is absent at both levels (legacy mode), or when a target
inherits the course-level path, suffix behavior is unchanged.

More precisely, the suffix determination for explicit targets becomes:

1. If the target has its own `<remote-path>` → no suffix (the path disambiguates).
2. If the target is the first target and has no own `<remote-path>` → no suffix
   (same as today).
3. Otherwise → `-{target_name}` suffix (same as today).

For implicit targets (no `<output-targets>` in spec), nothing changes.

### 4.4 Template integration

The `remote-template` gains a new `{remote_path}` placeholder:

| Placeholder         | Value                                       |
|---------------------|---------------------------------------------|
| `{repository_base}` | `<repository-base>` value                   |
| `{remote_path}`     | Effective remote path (per-target or course-level) |
| `{repo}`            | Full repo name: `{slug}-{lang}[-{suffix}]`  |
| `{slug}`            | Project slug only                           |
| `{lang}`            | Language code                               |
| `{suffix}`          | Target suffix (may be empty; includes leading dash) |

**Default templates** (chosen automatically when `remote-template` is empty):

- Without `remote_path`: `{repository_base}/{repo}` (backward compatible)
- With `remote_path`: `{repository_base}/{remote_path}/{repo}`

**SSH example**: For SSH-style GitLab URLs, use an explicit template:

```xml
<github>
    <repository-base>gitlab.example.com</repository-base>
    <remote-path>azav-editors</remote-path>
    <remote-template>git@gitlab.example.com:{remote_path}/{repo}.git</remote-template>
</github>
```

### 4.5 Environment variable and config support

A new config/env var `CLM_GIT__REMOTE_PATH` allows overriding the course-level
`<remote-path>` per machine. This mirrors the existing
`CLM_GIT__REMOTE_TEMPLATE` override.

| Config key              | Env var                   | Description                          |
|-------------------------|---------------------------|--------------------------------------|
| `git.remote_template`   | `CLM_GIT__REMOTE_TEMPLATE`| Template override (existing)         |
| `git.remote_path`       | `CLM_GIT__REMOTE_PATH`    | Course-level remote path override    |

The env var overrides the spec-level `<remote-path>` but does **not** override
per-target `<remote-path>` values. This matches the behavior of
`remote_template`: the env var is a machine-level default, not a per-target
override.

### 4.6 Backward compatibility

The design is fully backward compatible:

1. **No `<remote-path>` anywhere**: `repository-base` is used exactly as today.
   The default template is `{repository_base}/{repo}`. All existing specs work
   unchanged.

2. **`<remote-path>` on `<github>` only**: All targets use the same path. The
   default template becomes `{repository_base}/{remote_path}/{repo}`. This is
   the simple case for a single-group GitLab setup.

3. **`<remote-path>` on individual targets**: Targets with their own path get
   suffix-free repo names. Targets without inherit the course-level path and
   keep suffix behavior.

4. **Legacy `<github>/<de>` and `<github>/<en>` elements**: These older specs
   (visible in `test-spec-1.xml` through `test-spec-5.xml`) bypass the
   `derive_remote_url` path entirely — they use hardcoded per-language URLs.
   These are unaffected by this change.

### 4.7 `include-speaker` interaction

With explicit output targets, `include-speaker` becomes redundant — speaker
slides are just another `<kind>` that can appear in any target. However,
`include-speaker` remains necessary for implicit targets (no `<output-targets>`
in spec), where it controls whether the "speaker" implicit target is created.

No changes to `include-speaker` behavior are proposed.

---

## 5. Naming Discussion

The name `remote-path` was chosen as the most neutral option:

| Alternative       | Pro                            | Con                                    |
|-------------------|--------------------------------|----------------------------------------|
| `remote-path`     | Neutral, supports nesting      | Slightly generic                       |
| `namespace`       | GitLab's official term         | Meaningless to GitHub users            |
| `group`           | Simple, clear for GitLab       | GitHub calls them "organizations"      |
| `org`             | Clear for GitHub               | GitLab-centric users think "group"     |
| `remote-group`    | Explicit about remote          | Longer, still platform-specific        |

`remote-path` supports nested GitLab subgroups naturally
(`azav-students/2026-q2`) — it's just a path segment.

---

## 6. Example Configurations

### 6.1 GitHub (unchanged)

```xml
<course>
    <project-slug>python-basics</project-slug>
    <github>
        <repository-base>https://github.com/Coding-Academy-Munich</repository-base>
    </github>
    <!-- No output-targets: uses implicit public/speaker -->
</course>
```

Produces:
- `https://github.com/Coding-Academy-Munich/python-basics-de`
- `https://github.com/Coding-Academy-Munich/python-basics-en`

### 6.2 GitHub with separate org and remote-path (equivalent to 6.1)

```xml
<course>
    <project-slug>python-basics</project-slug>
    <github>
        <repository-base>https://github.com</repository-base>
        <remote-path>Coding-Academy-Munich</remote-path>
    </github>
</course>
```

Same output as 6.1. This shows that the split is optional for GitHub.

### 6.3 GitLab with AZAV access levels

```xml
<course>
    <project-slug>python-basics</project-slug>
    <github>
        <repository-base>https://gitlab.example.com</repository-base>
        <remote-path>azav-editors</remote-path>
    </github>

    <output-targets>
        <output-target name="students">
            <path>./output/students</path>
            <kinds><kind>code-along</kind></kinds>
            <remote-path>azav-students</remote-path>
        </output-target>

        <output-target name="teachers">
            <path>./output/teachers</path>
            <kinds>
                <kind>code-along</kind>
                <kind>completed</kind>
            </kinds>
            <remote-path>azav-teachers</remote-path>
        </output-target>

        <output-target name="editors">
            <path>./output/editors</path>
            <kinds>
                <kind>code-along</kind>
                <kind>completed</kind>
                <kind>speaker</kind>
            </kinds>
            <!-- Inherits remote-path azav-editors from <github> -->
        </output-target>
    </output-targets>
</course>
```

Produces (for each language de/en):
- `https://gitlab.example.com/azav-students/python-basics-de` (code-along only)
- `https://gitlab.example.com/azav-teachers/python-basics-de` (code-along + completed)
- `https://gitlab.example.com/azav-editors/python-basics-de` (everything)

### 6.4 GitLab with SSH and nested subgroups

```xml
<course>
    <project-slug>python-basics</project-slug>
    <github>
        <repository-base>gitlab.example.com</repository-base>
        <remote-path>azav-editors</remote-path>
        <remote-template>git@gitlab.example.com:{remote_path}/{repo}.git</remote-template>
    </github>

    <output-targets>
        <output-target name="students">
            <path>./output/students</path>
            <kinds><kind>code-along</kind></kinds>
            <remote-path>azav-students/2026-q2</remote-path>
        </output-target>

        <output-target name="editors">
            <path>./output/editors</path>
            <!-- Inherits remote-path azav-editors from <github> -->
        </output-target>
    </output-targets>
</course>
```

Produces:
- `git@gitlab.example.com:azav-students/2026-q2/python-basics-de.git`
- `git@gitlab.example.com:azav-editors/python-basics-de.git`

### 6.5 Non-AZAV workshop (two access levels)

```xml
<course>
    <project-slug>ml-workshop</project-slug>
    <github>
        <repository-base>https://gitlab.example.com</repository-base>
        <remote-path>workshop-editors</remote-path>
    </github>

    <output-targets>
        <output-target name="participants">
            <path>./output/participants</path>
            <kinds>
                <kind>code-along</kind>
                <kind>completed</kind>
            </kinds>
            <remote-path>workshop-participants</remote-path>
        </output-target>

        <output-target name="editors">
            <path>./output/editors</path>
            <!-- All kinds, inherits remote-path -->
        </output-target>
    </output-targets>
</course>
```

### 6.6 Mixed de/en in same group

Both languages go to the same group — access is controlled at the group level,
not per language:

```xml
<output-target name="students">
    <path>./output/students</path>
    <kinds><kind>code-along</kind></kinds>
    <!-- Both de and en repos land in azav-students -->
    <remote-path>azav-students</remote-path>
</output-target>
```

This naturally produces:
- `https://gitlab.example.com/azav-students/python-basics-de`
- `https://gitlab.example.com/azav-students/python-basics-en`

---

## 7. GitLab Group Organization Recommendations

Based on the discussion, here is a recommended group structure for AZAV and
non-AZAV courses:

### 7.1 AZAV courses (three access levels)

```
gitlab.example.com/
├── azav-students/              # Per-cohort access via subgroups
│   ├── python-basics-de
│   ├── python-basics-en
│   ├── software-engineering-de
│   └── ...
├── azav-teachers/              # All AZAV teachers get read access
│   ├── python-basics-de        # code-along + completed
│   ├── python-basics-en
│   └── ...
└── azav-editors/               # Course developers, read/write
    ├── python-basics-de        # code-along + completed + speaker
    ├── python-basics-en
    └── ...
```

**Access model**:
- Students: read access to `azav-students` (or per-cohort subgroup)
- Teachers: read access to `azav-teachers`
- Editors: read/write access to `azav-editors`

### 7.2 Non-AZAV courses (two access levels)

```
gitlab.example.com/
├── workshop-participants/      # Read access
│   ├── ml-workshop-de          # code-along + completed
│   └── ...
└── workshop-editors/           # Read/write access
    ├── ml-workshop-de          # everything
    └── ...
```

### 7.3 Language handling

Both de and en repos live in the same group. This:
- Simplifies access management (one grant per user per role)
- Allows German-course students to reference English slides if needed
- Keeps the `-de`/`-en` suffix on repo names for clarity

---

## 8. Implementation Plan

### Phase 1: Core data model changes

**Files**: `src/clm/core/course_spec.py`

1. Add `remote_path: str | None = None` field to `GitHubSpec`.
2. Parse `<remote-path>` in `GitHubSpec.from_element()`.
3. Add `remote_path: str | None = None` field to `OutputTargetSpec`.
4. Parse `<remote-path>` in `OutputTargetSpec.from_element()`.
5. Update `derive_remote_url()`:
   - Accept new `remote_path` parameter.
   - Choose default template based on whether `remote_path` is set.
   - Implement suffix suppression when target has its own `remote_path`.
   - Add `{remote_path}` to template formatting kwargs.

**Tests**: Unit tests for all new parsing and URL derivation paths.

### Phase 2: Config and CLI integration

**Files**: `src/clm/infrastructure/config.py`, `src/clm/cli/commands/git_ops.py`

1. Add `remote_path: str` field to `GitConfig` (default empty string).
2. Update `find_output_repos()` to:
   - Read per-target `remote_path` from `OutputTargetSpec`.
   - Fall back to course-level `remote_path` from `GitHubSpec`.
   - Fall back to config-level `remote_path` from `GitConfig`.
   - Pass effective `remote_path` to `derive_remote_url()`.

**Tests**: Integration tests for `find_output_repos()` with various
combinations of course-level and per-target remote paths.

### Phase 3: Documentation

**Files**:
- `docs/user-guide/spec-file-reference.md` — add `<remote-path>` documentation
- `src/clm/cli/info_topics/spec-files.md` — update for downstream agents
- `CLAUDE.md` — add environment variable, update architecture notes
- `CHANGELOG.md` — document the new feature

### Estimated scope

- ~80 lines of production code changes
- ~200 lines of test code
- Documentation updates in 4 files

---

## 9. Open Questions

1. **Naming**: Is `remote-path` the right name? Alternatives considered:
   `namespace`, `group`, `org`, `remote-group`. Current recommendation:
   `remote-path` for neutrality and support for nested paths.

2. **Renaming `<github>` to `<git>`**: The `<github>` element name is
   GitHub-specific. With GitLab support, should we introduce `<git>` as an
   alias (keeping `<github>` for backward compatibility)? This is orthogonal
   to the `remote-path` feature but worth considering.

3. **Validation**: Should we warn if a course uses `<remote-path>` on targets
   but no `<remote-path>` (or `<repository-base>`) at the `<github>` level?
   A course-level `<repository-base>` is still required for any remote URL
   generation.

4. **Suffix suppression edge case**: What if two targets share the same
   `<remote-path>`? They would produce identical remote URLs. Should we
   validate against this and produce an error?
