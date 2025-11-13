# CLI Tests

This directory contains tests for the CLX command-line interface.

## Test Structure

The tests are organized into three tiers based on speed and scope:

### Tier 1: Unit Tests (`test_cli_unit.py`)
Fast tests using Click's CliRunner for in-process testing.

- **What they test**: Argument parsing, validation, help text, basic command structure
- **Speed**: Very fast (milliseconds)
- **Dependencies**: None (no workers, no backend)
- **Run by default**: Yes

**Run unit tests only:**
```bash
cd clx-cli
pytest tests/test_cli_unit.py
```

### Tier 2: Integration Tests (`test_cli_integration.py`)
Tests that run CLI with real backend integration.

- **What they test**: CLI → Backend → Output flow
- **Speed**: Medium (seconds)
- **Dependencies**: SQLite backend (no external services)
- **Run by default**: No (marked with `@pytest.mark.integration`)

**Run integration tests:**
```bash
cd clx-cli
pytest -m integration
```

### Tier 3: E2E Subprocess Tests (`test_cli_subprocess.py`)
True end-to-end tests that run the actual `clx` command via subprocess.

- **What they test**: Installed CLI command, real user experience
- **Speed**: Slow (seconds to minutes)
- **Dependencies**: Installed `clx` command, may require workers
- **Run by default**: No (marked with `@pytest.mark.e2e` and `@pytest.mark.slow`)

**Run E2E subprocess tests:**
```bash
cd clx-cli
pytest -m e2e
```

## Running Tests

### Default (Fast Unit Tests Only)
```bash
cd clx-cli
pytest
```

### All Tests Except Slow Ones
```bash
cd clx-cli
pytest -m "not slow"
```

### All Tests Including E2E
```bash
cd clx-cli
pytest -m ""
```

### Specific Test File
```bash
cd clx-cli
pytest tests/test_cli_unit.py -v
```

### Specific Test Class
```bash
cd clx-cli
pytest tests/test_cli_unit.py::TestBuildCommandArguments -v
```

### Specific Test Method
```bash
cd clx-cli
pytest tests/test_cli_unit.py::TestBuildCommandArguments::test_build_requires_spec_file -v
```

### With Coverage
```bash
cd clx-cli
pytest --cov=clx_cli --cov-report=html
```

## Test Markers

Tests use pytest markers for organization:

- `@pytest.mark.integration` - Tests requiring backend
- `@pytest.mark.slow` - Slow-running tests
- `@pytest.mark.e2e` - End-to-end subprocess tests

**Filter by marker:**
```bash
# Run only integration tests
pytest -m integration

# Run everything except slow tests
pytest -m "not slow"

# Run unit tests only (exclude integration and e2e)
pytest -m "not integration and not e2e"
```

## Installing Test Dependencies

```bash
cd clx-cli
pip install -e ".[dev]"
```

## Continuous Integration

For CI/CD pipelines, we recommend:

1. **Pull Request Checks**: Run unit tests only (fast feedback)
   ```bash
   pytest  # Uses default config, skips slow tests
   ```

2. **Merge to Main**: Run unit + integration tests
   ```bash
   pytest -m "not slow"
   ```

3. **Nightly/Release**: Run all tests including E2E
   ```bash
   pytest -m ""
   ```

## Fixtures

Shared fixtures are defined in `conftest.py`:

- `test_data_dir` - Path to test data directory
- `test_spec_files` - Dictionary of test specification files
- `temp_workspace` - Temporary workspace with standard directories
- `cli_test_db_path` - Temporary database path
- `sample_course_spec_xml` - Minimal valid course XML
- `create_test_spec_file` - Factory for creating test specs
- `configure_test_logging` - Reduces log noise during tests

## Adding New Tests

### Adding a Unit Test

Add to `test_cli_unit.py`:

```python
def test_my_new_feature(self):
    runner = CliRunner()
    result = runner.invoke(cli, ['build', '--my-option'])
    assert result.exit_code == 0
```

### Adding an Integration Test

Add to `test_cli_integration.py`:

```python
@pytest.mark.integration
def test_my_integration(self, tmp_path):
    runner = CliRunner()
    # Test with real backend...
```

### Adding an E2E Test

Add to `test_cli_subprocess.py`:

```python
@pytest.mark.e2e
@pytest.mark.slow
def test_my_e2e_feature(self):
    result = subprocess.run(['clx', 'my-command'], ...)
    assert result.returncode == 0
```

## Troubleshooting

### Tests Skip with "Test data not available"
Ensure you're running tests from the repository root where `test-data/` exists.

### Subprocess tests fail with "clx: command not found"
Install the package in development mode:
```bash
pip install -e .
```

### Integration tests hang or fail
Check that:
- SQLite backend is properly configured
- No other processes are using the test database
- Sufficient disk space for test outputs

## Best Practices

1. **Keep unit tests fast** - They run on every commit
2. **Use appropriate markers** - Don't mark unit tests as slow
3. **Clean up resources** - Use `tmp_path` fixture for temporary files
4. **Test failure cases** - Don't just test happy paths
5. **Use descriptive names** - Test names should explain what they test
6. **Isolate tests** - Each test should be independent
