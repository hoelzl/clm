# CLM Core Package - Code Quality Audit Summary

## Overview
Analyzed 1,239 lines of code across 24 files in the `src/clm/core/` package.
Found **25+ quality issues** with significant code duplication and complexity concerns.

---

## Critical Issues Found

### 1. CODE DUPLICATION: `.notebooks` Property (Triple Duplication)
**Impact:** CRITICAL | **Frequency:** 3 locations | **Lines Wasted:** 10+

Identical implementation across:
- `course.py:99-102`
- `section.py:26-27`  
- `topic.py:46-49`

```python
@property
def notebooks(self) -> list["NotebookFile"]:
    return [file for file in self.files if isinstance(file, NotebookFile)]
```

**Usage:** Only used once in production (`section.py:30`)

---

### 2. CODE DUPLICATION: Image Conversion File Classes (100% Identical)
**Impact:** CRITICAL | **Files:** 2 | **Lines Duplicated:** 18

`PlantUmlFile` and `DrawIoFile` have **identical** implementations:

```python
# Both files contain:
@property
def img_path(self) -> Path:
    unsanitized = (self.path.parents[1] / "img" / self.path.stem).with_suffix(".png")
    return sanitize_path(unsanitized)

@property
def source_outputs(self) -> frozenset[Path]:
    return frozenset({self.img_path})
```

**Only Difference:** Class name and operation reference

---

### 3. CODE DUPLICATION: Conversion Operation Payloads (85% Identical)
**Impact:** HIGH | **Files:** 2 | **Duplicated Lines:** 12

`ConvertPlantUmlFileOperation.payload()` and `ConvertDrawIoFileOperation.payload()`:

```python
# Both do nearly identical:
data = self.input_file.path.read_text(encoding="utf-8")
correlation_id = await new_correlation_id()
payload = PlantUmlPayload/DrawioPayload(  # Only difference
    data=data,
    correlation_id=correlation_id,
    input_file=str(self.input_file.path),
    input_file_name=self.input_file.path.name,
    output_file=str(self.output_file),
    output_file_name=self.output_file.name,
)
await note_correlation_id_dependency(correlation_id, payload)
return payload
```

---

## High Complexity Issues

### 4. COMPLEX LOOP: `_build_topic_map()` (Course.py:161-188)
**Issue:** 5 levels of nesting with 4 early-exit conditions

```
for module in iterdir():  ─┐
    if is_ignored_dir():  │
        continue          ├─ 5 nesting levels
    if not is_dir():      │
        continue          │
    for topic_path:       │
        if not topic_id:  │
            continue      ┴─ High cyclomatic complexity
```

**Problems:**
- Difficult to understand control flow
- State accumulated during iteration
- Silently ignores duplicate topic IDs

---

### 5. TOPIC HIERARCHY: Premature Abstraction
**Impact:** HIGH | **Pattern:** Unnecessary ABC with 2 minimal subclasses

`DirectoryTopic` (trivial) vs `FileTopic` (15 lines, complex):

```python
# DirectoryTopic - trivial
class DirectoryTopic(Topic):
    def build_file_map(self):
        self.add_files_in_dir(self.path)  # 1 line!

# FileTopic - complex
class FileTopic(Topic):
    def build_file_map(self):
        # File I/O, regex parsing, 15 lines total
```

**Issues:**
- Only 2 implementations (not enough for ABC pattern)
- Huge complexity disparity
- Misleading debug messages (switched labels!)

---

### 6. PROCESS STAGE: Missing Type Hints & Mixed Concerns
**File:** `course.py:123-133`

```python
async def process_stage(self, stage, backend):  # ❌ No type hints!
    num_operations = 0
    async with TaskGroup() as tg:
        for file in self.files:
            if file.execution_stage == stage:
                op = await file.get_processing_operation(self.output_root)
                tg.create_task(op.execute(backend))
                num_operations += 1
    await backend.wait_for_completion()
    return num_operations
```

**Issues:**
- `stage` parameter type not specified (should be `int`)
- `backend` type not specified (should be `Backend`)
- Mixes file filtering + async orchestration + backend coordination

---

## Other Notable Issues

### 7. Inconsistent Error Messages
- Some use string variables, others use method calls
- Different message formats across similar operations
- Hard to parse logs consistently

### 8. Logging Message Errors
- DirectoryTopic: "Building file map for **file**..." (wrong!)
- FileTopic: "Building file map for **directory**..." (wrong!)

### 9. Performance: Redundant Property Access
- `relative_path` property calls `topic.path.is_file()` every access (filesystem I/O!)
- No caching of computed values
- Filesystem checks on every invocation

### 10. DRY Violations
- 2 different directory traversal implementations
- Path conversion logic duplicated
- Multiple `.notebooks` property definitions

---

## Quality Metrics

| Category | Count | Severity |
|----------|-------|----------|
| Code Duplication Issues | 4 | CRITICAL/HIGH |
| Complexity Hotspots | 3 | HIGH |
| Under-utilized Code | 3 | MEDIUM |
| Design Issues | 3 | HIGH |
| Logging Issues | 4 | MEDIUM |
| Performance Concerns | 2 | MEDIUM |
| Type Hint Gaps | 2+ | MEDIUM |
| **TOTAL** | **25+** | **Mixed** |

---

## Quick Fix Checklist

- [ ] Consolidate `.notebooks` → single utility function
- [ ] Merge `PlantUmlFile` + `DrawIoFile` → `ImageConversionFile` base class
- [ ] Consolidate `payload()` methods → template method pattern
- [ ] Fix Topic logging message labels (swap them!)
- [ ] Add type hints to `process_stage()` and related methods
- [ ] Extract directory traversal → utility function
- [ ] Fix `relative_path` property (add caching, move to method)
- [ ] Simplify Topic class hierarchy

---

## Files Analyzed

**Largest Files (by complexity):**
1. `course.py` (200 lines) - Processing orchestration, course building
2. `text_utils.py` (139 lines) - Path/string sanitization (mostly ok)
3. `topic.py` (130 lines) - Topic hierarchy, file mapping
4. `course_spec.py` (110 lines) - Spec parsing (mostly ok)
5. `course_file.py` (83 lines) - Base file class
6. `process_notebook.py` (75 lines) - Notebook operations

---

## Detailed Report

A comprehensive audit report with code examples, line-by-line analysis, and 
prioritized refactoring recommendations is available in:

**📄 `.claude/audit-core-package-quality-analysis.md`**

This includes:
- Detailed code examples for each issue
- Root cause analysis
- Impact assessment
- Concrete refactoring recommendations
- 3-phase refactoring roadmap

