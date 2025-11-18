# E2E Test Consolidation Proposal

## Problem Statement

The e2e test suite has multiple tests that perform expensive `course.process_all(backend)` operations on the same course, adding 20-50 seconds per duplicate build. This significantly slows down test runs without providing additional coverage.

## Analysis

### Current Duplicate Builds

**test_e2e_course_conversion.py:**

1. **Course 1 with all workers** (2 tests, ~40-100 seconds total):
   - `test_course_1_notebooks_native_workers` (line 594): Validates notebooks, multilingual outputs, Jupyter structure
   - `test_course_dir_groups_copy_e2e` (line 699): Validates directory groups (Bonus, root files) are copied

   **Both tests:**
   - Use same fixture: `e2e_course_1`, `sqlite_backend_with_all_workers`
   - Process same course: `await course.process_all(backend)`
   - Validate different aspects of the same output

**Savings: 20-50 seconds per test run by combining these 2 tests**

### Tests to Keep Separate

**test_e2e_lifecycle.py** - All tests remain separate:
- These test worker lifecycle management (startup, shutdown, reuse, health)
- They require multiple builds to test worker reuse and persistence
- Cannot be consolidated without losing test coverage

**test_e2e_course_conversion.py** - Other tests:
- `test_course_2_notebooks_native_workers` - Different course (course_2)
- Single-file edge case tests (course_3, 4, 5) - Different courses, different backends
- Structure validation tests - Use DummyBackend (no actual builds)

## Proposed Solution

### Consolidate Course 1 Tests

**Replace:**
- `test_course_1_notebooks_native_workers` (line 594-644)
- `test_course_dir_groups_copy_e2e` (line 699-739)

**With:**
- `test_course_1_full_e2e` - Single test that validates both aspects

### Implementation Strategy

Use helper functions to group assertions logically:

```python
@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Notebook worker module not available"
)
async def test_course_1_full_e2e(
    e2e_course_1,
    sqlite_backend_with_all_workers
):
    """Full E2E test: Convert course 1 and validate all outputs.

    This test combines validation of:
    1. Notebook conversion (multilingual outputs, Jupyter structure)
    2. Directory group copying (Bonus materials, root files)

    By combining these validations in a single test, we avoid duplicate
    course processing and reduce test time by 20-50 seconds.
    """
    course = e2e_course_1
    backend = sqlite_backend_with_all_workers

    # Verify we have notebooks to process
    notebooks = course.notebooks
    assert len(notebooks) == 3, f"Should have 3 notebooks, found {len(notebooks)}"

    # Process all course files
    logger.info("Starting course processing with native workers...")
    await course.process_all(backend)

    # Wait for all jobs to complete
    logger.info("Waiting for job completion...")
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    # === Validation: Notebook Outputs ===
    validate_notebook_outputs(course)

    # === Validation: Directory Groups ===
    validate_directory_groups(course)

    logger.info("Course 1 full E2E test completed successfully")


def validate_notebook_outputs(course):
    """Validate notebook conversion outputs (multilingual, structure)."""
    output_dir = course.output_root

    # German output
    de_dir = validate_course_output_structure(output_dir, "De", "Mein Kurs")
    de_notebook_count = count_notebooks_in_dir(de_dir)
    assert de_notebook_count > 0, "No German notebooks generated"
    logger.info(f"Found {de_notebook_count} German notebooks")

    # English output
    en_dir = validate_course_output_structure(output_dir, "En", "My Course")
    en_notebook_count = count_notebooks_in_dir(en_dir)
    assert en_notebook_count > 0, "No English notebooks generated"
    logger.info(f"Found {en_notebook_count} English notebooks")

    # Validate at least one notebook has correct Jupyter structure
    de_notebooks = list(de_dir.rglob("*.ipynb"))
    if de_notebooks:
        first_notebook = de_notebooks[0]
        notebook_data = validate_notebook_structure(first_notebook)
        assert len(notebook_data["cells"]) > 0, "Notebook should have cells"

    logger.info("✓ Notebook outputs validated")


def validate_directory_groups(course):
    """Validate directory groups (Bonus, root files) are copied correctly."""
    output_dir = course.output_root

    # === German outputs ===
    de_course_dir = output_dir / "public" / "De" / "Mein Kurs"

    # Check Bonus directory group
    bonus_dir = de_course_dir / "Bonus"
    assert bonus_dir.exists(), "Bonus directory should exist"
    assert (bonus_dir / "workshops-toplevel.txt").exists(), "workshops-toplevel.txt should be copied"
    assert (bonus_dir / "Workshop-1" / "workshop-1.txt").exists(), "Workshop subdirectory should be copied"

    # Check root files directory group (empty name)
    assert (de_course_dir / "root-file-1.txt").exists(), "root-file-1.txt should be in course root"
    assert (de_course_dir / "root-file-2").exists(), "root-file-2 should be in course root"

    # === English outputs ===
    en_course_dir = output_dir / "public" / "En" / "My Course"
    assert en_course_dir.exists(), "English course directory should exist"
    assert (en_course_dir / "Bonus").exists(), "English Bonus directory should exist"
    assert (en_course_dir / "root-file-1.txt").exists(), "English root files should be copied"

    logger.info("✓ Directory groups validated")
```

## Benefits

1. **Performance**: Saves 20-50 seconds per test run
2. **Maintainability**: Single source of truth for course 1 validation
3. **Readability**: Clear separation of validation concerns via helper functions
4. **Coverage**: No loss of test coverage - all assertions preserved

## Implementation Steps

1. Add helper functions `validate_notebook_outputs()` and `validate_directory_groups()`
2. Create new test `test_course_1_full_e2e` combining both validations
3. Remove old tests: `test_course_1_notebooks_native_workers`, `test_course_dir_groups_copy_e2e`
4. Run tests to verify coverage is maintained

## Test Count Impact

**Before:** 49 e2e tests
**After:** 47 e2e tests (2 tests consolidated into 1)
**Time Savings:** ~20-50 seconds per test run

## Alternative Considered: Parametrized Tests

We considered using `@pytest.mark.parametrize` to share a single build across multiple validation functions. However, this approach:
- Would require complex fixture management
- Makes test failures harder to debug (single failure stops all validations)
- Doesn't provide significant benefits over helper functions

The helper function approach is simpler and more maintainable.
