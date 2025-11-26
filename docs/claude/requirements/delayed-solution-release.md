# Requirements: Delayed Solution Release (Multiple Output Directories)

**Status**: Draft
**Created**: 2025-11-26
**Author**: Claude (AI Assistant)
**Related Issue**: Feature request for delayed solution release

## Executive Summary

Course instructors often want to delay releasing completed solutions to students, giving them time to work on exercises before solutions become available. This requires the ability to specify multiple output directories in the course specification file, with each directory receiving a specific subset of output types (notebooks, HTML, code files) and kinds (completed, code-along, speaker).

This document defines requirements for adding multi-output-directory support to CLX course specifications.

---

## Current State Analysis

### What Works Well

1. **Single output directory**: Clear, simple configuration with `--output-dir` CLI flag
2. **Automatic structure**: `public/` and `speaker/` directories created automatically
3. **All formats generated**: HTML, notebooks, and code files generated together
4. **Multiple languages**: German and English outputs generated concurrently

### Current Limitations

1. **All-or-nothing output**: Cannot release some content types while withholding others
2. **Single release point**: All materials are generated to the same directory
3. **No selective deployment**: Cannot split public outputs into "immediate" and "delayed" groups
4. **Manual workaround required**: Instructors must manually copy files between directories

### Use Case: Delayed Solution Release

**Scenario**: A university instructor teaching a Python programming course:
1. At course start, releases **code-along notebooks** and **HTML slides** to students
2. After the exercise deadline (e.g., 1 week later), releases **completed notebooks** with solutions
3. **Speaker materials** stay private throughout the course

**Current Workaround**:
1. Run `clx build` with `--output-kinds code-along` first
2. Deploy to student-facing directory
3. After deadline, run `clx build` with `--output-kinds completed`
4. Deploy completed materials to the same directory

**Problems with Workaround**:
- Requires multiple build invocations
- Error-prone manual process
- No single source of truth for output configuration
- Cannot be automated with course spec file alone

---

## Requirements

### R1: Multiple Output Directories in Spec File

**Priority**: High
**Rationale**: Enable declarative configuration of multiple output destinations

#### R1.1: Output Targets Element

The course specification XML file **MUST** support an `<output-targets>` element containing one or more `<output-target>` definitions.

```xml
<course>
    <name>...</name>
    <prog-lang>python</prog-lang>
    <!-- ... other elements ... -->

    <output-targets>
        <output-target name="student-materials">
            <path>./output/students</path>
            <kinds>
                <kind>code-along</kind>
            </kinds>
            <formats>
                <format>html</format>
                <format>notebook</format>
            </formats>
        </output-target>

        <output-target name="solutions">
            <path>./output/solutions</path>
            <kinds>
                <kind>completed</kind>
            </kinds>
            <formats>
                <format>html</format>
                <format>notebook</format>
                <format>code</format>
            </formats>
        </output-target>

        <output-target name="instructor">
            <path>./output/private</path>
            <kinds>
                <kind>speaker</kind>
            </kinds>
            <formats>
                <format>html</format>
                <format>notebook</format>
            </formats>
        </output-target>
    </output-targets>
</course>
```

#### R1.2: Output Target Attributes

Each `<output-target>` element **MUST** support:

| Element | Required | Description |
|---------|----------|-------------|
| `name` attribute | Yes | Unique identifier for this output target |
| `<path>` | Yes | Output directory path (absolute or relative to course root) |
| `<kinds>` | No | List of output kinds to generate (default: all) |
| `<formats>` | No | List of output formats to generate (default: all) |
| `<languages>` | No | List of languages to generate (default: all configured) |

#### R1.3: Valid Kind Values

Valid values for `<kind>` elements:
- `code-along` - Workshop materials with cleared code cells
- `completed` - Full solutions with all code
- `speaker` - Instructor materials with notes

#### R1.4: Valid Format Values

Valid values for `<format>` elements:
- `html` - HTML slides/pages
- `notebook` - Jupyter notebook files (.ipynb)
- `code` - Source code files (.py, .cs, etc.)

#### R1.5: Valid Language Values

Valid values for `<language>` elements:
- `de` - German
- `en` - English
- Any other language code defined in the course

#### R1.6: Backward Compatibility

If no `<output-targets>` element is present, the system **MUST** behave exactly as current:
- Use `--output-dir` CLI argument (or default `./output`)
- Generate all kinds, formats, and languages

**Success Criteria**:
- Existing course specs continue to work unchanged
- New output-targets element provides fine-grained control

---

### R2: Selective Output Generation

**Priority**: High
**Rationale**: Enable generation of only specific content types per target

#### R2.1: Kind Filtering

When `<kinds>` is specified, the output target **MUST**:
- Generate only the listed kinds
- Skip generation of non-listed kinds
- Maintain correct directory structure for included kinds

#### R2.2: Format Filtering

When `<formats>` is specified, the output target **MUST**:
- Generate only the listed formats
- Skip generation of non-listed formats
- Handle format dependencies correctly (e.g., code format only for completed kind)

#### R2.3: Language Filtering

When `<languages>` is specified, the output target **MUST**:
- Generate only the listed languages
- Skip generation of non-listed languages

#### R2.4: Default Behavior

When filter elements are omitted:
- `<kinds>` omitted → Generate all kinds
- `<formats>` omitted → Generate all formats
- `<languages>` omitted → Generate all languages

#### R2.5: Format-Kind Dependencies

The system **MUST** enforce these constraints:
- `code` format is only valid with `completed` kind
- If `code` format is requested with `code-along` or `speaker`, it **SHOULD** be silently ignored (or warn)

**Success Criteria**:
- Each output target receives exactly the specified content
- Invalid combinations are handled gracefully

---

### R3: CLI Integration

**Priority**: High
**Rationale**: CLI must work seamlessly with new multi-target configuration

#### R3.1: Default CLI Behavior

When `--output-dir` is **not** specified and `<output-targets>` is present:
- **MUST** use the output targets defined in the spec file
- **MUST** generate outputs to all defined targets

When `--output-dir` **is** specified:
- **MUST** override spec file output targets
- **MUST** use the single specified directory
- **MUST** generate all kinds/formats (legacy behavior)

#### R3.2: Target Selection Flag

The CLI **SHOULD** support a `--targets` flag to select specific targets:

```bash
# Build only the student-materials target
clx build course.xml --targets student-materials

# Build multiple targets
clx build course.xml --targets student-materials,solutions

# Build all targets (default)
clx build course.xml
```

#### R3.3: Target Listing Command

The CLI **SHOULD** provide a command to list defined output targets:

```bash
$ clx targets course.xml

Output Targets:
  student-materials  ./output/students   [code-along] [html, notebook]
  solutions          ./output/solutions  [completed]  [html, notebook, code]
  instructor         ./output/private    [speaker]    [html, notebook]
```

#### R3.4: Compatibility with Existing Flags

Existing CLI flags **MUST** remain functional:
- `--language en` - Filter to specific language (applies to all targets)
- `--speaker-only` - Equivalent to `--targets` selecting only speaker targets

**Success Criteria**:
- CLI provides intuitive control over multi-target builds
- Backward compatibility maintained for existing scripts

---

### R4: Output Directory Structure

**Priority**: Medium
**Rationale**: Clear, consistent directory structure for each target

#### R4.1: Target Directory Structure

Each output target **MUST** maintain the standard structure:

```
{target-path}/
├── {Lang}/                    # e.g., De/, En/
│   └── {Course Name}/
│       └── Slides/            # or Folien/ for German
│           ├── Html/
│           │   ├── Code-Along/    (if code-along kind included)
│           │   ├── Completed/     (if completed kind included)
│           │   └── Speaker/       (if speaker kind included)
│           ├── Notebooks/
│           │   ├── Code-Along/
│           │   ├── Completed/
│           │   └── Speaker/
│           └── Python/            (if code format included)
│               └── Completed/
```

**Note**: Unlike the current structure (with `public/` and `speaker/` top-level directories), each output target becomes its own root. Speaker materials go directly into the target path without a `speaker/` subdirectory.

#### R4.2: Simplified Structure Option

The system **COULD** support a `simplified` attribute to flatten the structure:

```xml
<output-target name="solutions" simplified="true">
    <path>./solutions</path>
    <kinds><kind>completed</kind></kinds>
</output-target>
```

This **COULD** produce:
```
./solutions/
├── De/
│   └── Completed/
│       ├── Html/
│       └── Notebooks/
└── En/
    └── Completed/
        ├── Html/
        └── Notebooks/
```

**Note**: This is a nice-to-have feature for MVP.

**Success Criteria**:
- Each target has a clean, predictable directory structure
- Structure adapts appropriately based on included kinds/formats

---

### R5: Validation and Error Handling

**Priority**: High
**Rationale**: Clear feedback when configuration is invalid

#### R5.1: Duplicate Target Names

The system **MUST** fail with a clear error if two targets have the same name.

#### R5.2: Overlapping Paths

The system **SHOULD** warn if two targets have overlapping output paths:
- Exact same path: Error
- One path is parent of another: Warning

#### R5.3: Invalid Values

The system **MUST** fail with a clear error for:
- Unknown kind values
- Unknown format values
- Unknown language values

#### R5.4: Path Validation

The system **SHOULD**:
- Create output directories if they don't exist
- Warn if path is not writable
- Support both absolute and relative paths

**Success Criteria**:
- Configuration errors are caught early with clear messages
- Users can quickly identify and fix configuration problems

---

### R6: Watch Mode Support

**Priority**: Medium
**Rationale**: File watching should work with multiple targets

#### R6.1: Multi-Target Watch

In watch mode (`clx build --watch`), the system **MUST**:
- Rebuild affected files to all applicable output targets
- Only rebuild targets that include the changed file's output type

#### R6.2: Efficient Rebuilds

When a file changes, the system **SHOULD**:
- Share work across targets where possible (e.g., notebook execution)
- Not re-execute notebooks multiple times for the same kind

**Success Criteria**:
- Watch mode works correctly with multi-target configuration
- Performance remains acceptable

---

## Non-Functional Requirements

### NFR1: Performance

- Multi-target builds **SHOULD NOT** re-execute notebooks multiple times
- Shared execution cache **MUST** be used across targets where applicable
- Parallel target generation **SHOULD** be supported

### NFR2: Clarity

- Error messages **MUST** indicate which target has the problem
- Progress reporting **SHOULD** show per-target progress

### NFR3: Documentation

- User guide **MUST** be updated with multi-target examples
- Migration guide **SHOULD** be provided for existing users

---

## Success Metrics

### Quantitative
- Zero regressions in existing single-output builds
- Multi-target builds take <10% longer than single-target equivalent
- Configuration validation catches 100% of invalid specs before build starts

### Qualitative
- Instructors can easily configure delayed solution release
- Configuration is self-documenting and readable
- Error messages clearly identify the problem and solution

---

## Out of Scope (for MVP)

### Explicitly NOT in Scope
1. **Conditional output based on dates**: No automatic time-based release
2. **Access control**: No authentication/authorization for outputs
3. **Remote destinations**: No direct upload to S3, GCS, etc.
4. **Template per target**: Each target uses the same notebook templates
5. **Different course specs per target**: One spec file, multiple outputs
6. **Git-based deployment**: No automatic git commits/pushes

---

## Implementation Phases

### Phase 1: Core Multi-Target Support (MVP)
**Goal**: Basic multi-target configuration and generation

**Deliverables**:
1. Extend `CourseSpec` to parse `<output-targets>`
2. Add `OutputTarget` data class
3. Modify `Course` to handle multiple output roots
4. Update `output_specs()` to filter by target configuration
5. Update CLI to use spec file targets
6. Add `--targets` CLI flag

### Phase 2: CLI Enhancements
**Goal**: Better CLI integration and discoverability

**Deliverables**:
1. Add `clx targets` command
2. Improve progress reporting for multi-target builds
3. Add validation warnings in CLI output

### Phase 3: Watch Mode and Optimization
**Goal**: Efficient watch mode with multi-target support

**Deliverables**:
1. Update watch mode for multi-target
2. Implement shared execution cache across targets
3. Add performance optimizations

---

## Example Configurations

### Example 1: Standard Delayed Release

```xml
<course>
    <name>
        <de>Python Programmierung</de>
        <en>Python Programming</en>
    </name>
    <prog-lang>python</prog-lang>

    <output-targets>
        <!-- Immediate release: code-along only -->
        <output-target name="student-immediate">
            <path>./output/students</path>
            <kinds>
                <kind>code-along</kind>
            </kinds>
        </output-target>

        <!-- Delayed release: solutions -->
        <output-target name="student-solutions">
            <path>./output/solutions</path>
            <kinds>
                <kind>completed</kind>
            </kinds>
        </output-target>

        <!-- Private: instructor materials -->
        <output-target name="instructor">
            <path>./output/instructor</path>
            <kinds>
                <kind>speaker</kind>
            </kinds>
        </output-target>
    </output-targets>

    <sections>...</sections>
</course>
```

### Example 2: Language-Specific Outputs

```xml
<output-targets>
    <!-- German course materials -->
    <output-target name="de-materials">
        <path>./output/de</path>
        <languages>
            <language>de</language>
        </languages>
    </output-target>

    <!-- English course materials -->
    <output-target name="en-materials">
        <path>./output/en</path>
        <languages>
            <language>en</language>
        </languages>
    </output-target>
</output-targets>
```

### Example 3: HTML-Only for Students

```xml
<output-targets>
    <!-- Students get HTML only (no editable notebooks) -->
    <output-target name="students">
        <path>./output/students</path>
        <kinds>
            <kind>code-along</kind>
            <kind>completed</kind>
        </kinds>
        <formats>
            <format>html</format>
        </formats>
    </output-target>

    <!-- TAs get full materials -->
    <output-target name="teaching-assistants">
        <path>./output/tas</path>
        <kinds>
            <kind>code-along</kind>
            <kind>completed</kind>
        </kinds>
        <formats>
            <format>html</format>
            <format>notebook</format>
        </formats>
    </output-target>
</output-targets>
```

---

## Open Questions

### Q1: Should we support YAML as well as XML?

**Options**:
- A: XML only (current format)
- B: XML + YAML support
- C: Migrate entirely to YAML

**Recommendation**: A (XML only) for MVP. YAML migration is a separate initiative.

### Q2: What happens if both --output-dir and <output-targets> are specified?

**Options**:
- A: CLI flag wins, ignore spec file targets
- B: Error out, require explicit choice
- C: Merge behavior (unclear semantics)

**Recommendation**: A - CLI override for scripting flexibility

### Q3: Should speaker outputs have a separate public/speaker split?

**Options**:
- A: Speaker target goes directly to target path (no public/speaker split)
- B: Maintain public/speaker split within each target
- C: Configurable per-target

**Recommendation**: A - Cleaner semantics, each target is self-contained

### Q4: How should we handle the `code` format for non-completed kinds?

**Options**:
- A: Silently ignore (current behavior)
- B: Warn but continue
- C: Error out

**Recommendation**: B - Warn so users know their config may not do what they expect

---

## References

1. CLX Documentation: https://github.com/hoelzl/clx
2. Current course spec format: `src/clx/core/course_spec.py`
3. Output path utilities: `src/clx/infrastructure/utils/path_utils.py`
4. Related: [improved-build-output.md](./improved-build-output.md)
