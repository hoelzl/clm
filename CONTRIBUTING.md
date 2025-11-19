# Contributing to CLX

Thank you for your interest in contributing to CLX! This document provides guidelines and instructions for contributing to the project.

## Quick Links

- **[Developer Guide](docs/developer-guide/README.md)** - Detailed developer documentation
- **[Architecture](docs/developer-guide/architecture.md)** - System architecture overview
- **[Building](docs/developer-guide/building.md)** - Building Docker services
- **[Testing](docs/developer-guide/testing.md)** - Testing guidelines

## Getting Started

### Prerequisites

- Python 3.11, 3.12, or 3.13
- Git
- Docker (optional, for containerized workers)
- uv or pip (uv recommended for faster installs)

### Development Setup

1. **Fork and clone the repository**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/clx.git
   cd clx
   ```

2. **Install in development mode**:
   ```bash
   pip install -e .

   # Or with uv (faster)
   uv pip install -e .
   ```

3. **Verify installation**:
   ```bash
   clx --help
   python -c "from clx import Course; print('✓ CLX installed successfully!')"
   ```

4. **Run tests**:
   ```bash
   pytest
   ```

## Development Workflow

### 1. Create a Branch

Create a feature branch for your work:

```bash
git checkout -b feature/your-feature-name
```

**Branch Naming**:
- Features: `feature/description`
- Bug fixes: `fix/description`
- Documentation: `docs/description`
- Tests: `test/description`

### 2. Make Your Changes

- Follow existing code style and conventions
- Add type hints to all new code
- Write docstrings for public functions and classes
- Add tests for new functionality

### 3. Test Your Changes

```bash
# Run fast unit tests
pytest

# Run with specific markers
pytest -m integration
pytest -m e2e

# Run all tests
pytest -m ""

# Run with coverage
pytest --cov=src/clx --cov-report=html
```

### 4. Commit Your Changes

Write clear, descriptive commit messages:

```bash
git add .
git commit -m "Add feature: description of what you added"
```

**Commit Message Format**:
```
<type>: <short summary>

<optional longer description>

<optional footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Test additions or changes
- `refactor`: Code refactoring
- `style`: Code style changes (formatting, etc.)
- `chore`: Maintenance tasks

**Examples**:
```
feat: Add support for Rust notebooks

- Implement RustFile class
- Add rust kernel detection
- Update template for rust syntax highlighting

Closes #123
```

```
fix: Handle empty course sections gracefully

Previously crashed when encountering empty section directories.
Now skips empty sections with a warning.

Fixes #456
```

### 5. Push and Create Pull Request

```bash
git push origin feature/your-feature-name
```

Then create a pull request on GitHub.

## Code Style Guidelines

### Python Style

- **PEP 8**: Follow Python style guide
- **Type Hints**: Use extensively, especially in public APIs
- **Docstrings**: Google-style docstrings for functions and classes
- **Line Length**: 88 characters (Black default)
- **Imports**: Organized (stdlib, third-party, local)

**Example**:

```python
from pathlib import Path
from typing import Optional, List

from attrs import define

from clx.core.course_file import CourseFile


@define
class NotebookFile(CourseFile):
    """A Jupyter notebook file.

    This class handles conversion of Python source files (.py) to
    Jupyter notebooks (.ipynb) and other formats.

    Attributes:
        source_file: Path to the source .py file
        output_format: Desired output format (notebook, html, slides)
    """

    output_format: str = "notebook"

    def process(self) -> None:
        """Process the notebook file.

        Converts the source file to the specified output format.

        Raises:
            FileNotFoundError: If source file doesn't exist
            ValueError: If output format is invalid
        """
        ...
```

### Code Organization

- **Separation of Concerns**: Domain logic in `core/`, infrastructure in `infrastructure/`
- **Small Functions**: Functions should do one thing well
- **No Magic Numbers**: Use named constants
- **Error Handling**: Catch specific exceptions, provide context

### Type Hints

Use type hints for all function signatures:

```python
from pathlib import Path
from typing import Optional, List, Dict, Any

def process_file(
    file_path: Path,
    output_dir: Path,
    options: Optional[Dict[str, Any]] = None
) -> List[Path]:
    """Process a file and return generated output paths."""
    ...
```

## Testing Guidelines

### Test Organization

```
tests/
├── core/              # Core domain logic tests
├── infrastructure/    # Infrastructure tests
├── cli/               # CLI tests
└── e2e/               # End-to-end tests
```

### Test Markers

Use pytest markers to categorize tests:

```python
import pytest

# Unit test (default, no marker)
def test_course_creation():
    ...

# Integration test (requires real workers)
@pytest.mark.integration
def test_worker_execution():
    ...

# End-to-end test (full course conversion)
@pytest.mark.e2e
def test_full_course_build():
    ...

# Slow test
@pytest.mark.slow
def test_large_dataset_processing():
    ...
```

### Writing Tests

**Unit Tests**:
- Fast, isolated, no external dependencies
- Mock infrastructure components
- Test one thing at a time

**Integration Tests**:
- Test interaction between components
- Use real database, real workers
- May require external tools (PlantUML, DrawIO)

**End-to-End Tests**:
- Test complete workflows
- Verify actual course builds correctly
- Check output files are generated

**Example Test**:

```python
def test_notebook_file_processing(tmp_path):
    """Test notebook file processes correctly."""
    # Arrange
    source_file = tmp_path / "test.py"
    source_file.write_text("# %%\nprint('Hello')\n")

    notebook_file = NotebookFile(
        source_file=source_file,
        output_dir=tmp_path / "output",
        prog_lang=ProgLang.PYTHON
    )

    # Act
    notebook_file.process()

    # Assert
    output_file = tmp_path / "output" / "test.ipynb"
    assert output_file.exists()
    # Further assertions...
```

### Running Tests

```bash
# Fast unit tests only (default)
pytest

# Include integration tests (requires external tools)
pytest -m integration

# Include e2e tests
pytest -m e2e

# Run specific test file
pytest tests/core/test_course.py

# Run specific test
pytest tests/core/test_course.py::test_course_creation

# Run with coverage
pytest --cov=src/clx --cov-report=html
open htmlcov/index.html
```

## Documentation

### Code Documentation

- **Docstrings**: All public functions, classes, and methods
- **Type Hints**: All function signatures
- **Comments**: For complex logic, not obvious code

### User Documentation

When adding user-facing features:

1. Update **[User Guide](docs/user-guide/)** if relevant
2. Add examples to **[Quick Start](docs/user-guide/quick-start.md)**
3. Update **[Configuration Guide](docs/user-guide/configuration.md)** for new options
4. Add troubleshooting tips to **[Troubleshooting](docs/user-guide/troubleshooting.md)**

### Developer Documentation

When changing architecture or adding developer-relevant features:

1. Update **[Architecture](docs/developer-guide/architecture.md)**
2. Add to **[Developer Guide](docs/developer-guide/README.md)**
3. Update **CLAUDE.md** for AI assistant context

## Pull Request Process

### Before Submitting

- [ ] All tests pass (`pytest -m ""`)
- [ ] Code follows style guidelines
- [ ] New code has tests
- [ ] Documentation updated (if needed)
- [ ] Commit messages are clear

### PR Description

Provide a clear description:

```markdown
## Summary
Brief description of changes

## Changes Made
- List of key changes
- What was added/modified/removed

## Testing
- How you tested the changes
- Which tests were added

## Related Issues
Closes #123
Relates to #456
```

### Review Process

1. **Automated Checks**: CI/CD runs tests and linting
2. **Code Review**: Maintainers review your code
3. **Feedback**: Address any requested changes
4. **Approval**: Once approved, PR will be merged

## Common Tasks

### Adding a New File Type

1. **Create file class** in `src/clx/core/course_files/`:
   ```python
   @define
   class MyFileType(CourseFile):
       def process(self, backend: Backend) -> None:
           ...
   ```

2. **Add operation** in `src/clx/core/operations/`:
   ```python
   def process_my_file(input_file: Path, output_file: Path) -> None:
       ...
   ```

3. **Add message classes** in `src/clx/infrastructure/messaging/`:
   ```python
   @define
   class MyFilePayload(BasePayload):
       ...
   ```

4. **Add tests** in `tests/core/course_files/`:
   ```python
   def test_my_file_type():
       ...
   ```

5. **Update documentation**:
   - Add to user guide
   - Update architecture doc

### Adding a New Worker Service

1. **Create service directory**: `services/my-worker/`

2. **Implement worker**:
   ```python
   from clx.infrastructure.workers import WorkerBase

   class MyWorker(WorkerBase):
       def process_job(self, job):
           ...
   ```

3. **Create Dockerfile** with BuildKit caching

4. **Add to docker-compose.yaml**

5. **Add tests** with `@pytest.mark.integration`

6. **Update documentation**

## Project Structure

```
clx/
├── src/clx/              # Main package
│   ├── core/             # Domain logic
│   ├── infrastructure/   # Runtime support
│   └── cli/              # Command-line interface
├── services/             # Worker services
├── tests/                # All tests
├── docs/                 # Documentation
│   ├── user-guide/       # User documentation
│   ├── developer-guide/  # Developer documentation
│   └── archive/          # Historical documents
├── pyproject.toml        # Package configuration
└── docker-compose.yaml   # Service orchestration
```

## Code of Conduct

### Our Standards

- **Be respectful**: Treat everyone with respect
- **Be constructive**: Provide helpful feedback
- **Be collaborative**: Work together toward common goals
- **Be patient**: Not everyone has the same experience level

### Unacceptable Behavior

- Harassment or discrimination
- Trolling or insulting comments
- Personal or political attacks
- Publishing others' private information

## Getting Help

- **Documentation**: Check [docs/](docs/) folder
- **Issues**: Search [existing issues](https://github.com/hoelzl/clx/issues)
- **Discussions**: Start a discussion on GitHub
- **Questions**: Open an issue with the "question" label

## License

By contributing to CLX, you agree that your contributions will be licensed under the MIT License.

## Recognition

Contributors will be recognized in:
- GitHub contributors page
- Release notes (for significant contributions)
- Project documentation (as appropriate)

## Additional Resources

- **Developer Guide**: [docs/developer-guide/README.md](docs/developer-guide/README.md)
- **Architecture**: [docs/developer-guide/architecture.md](docs/developer-guide/architecture.md)
- **Testing**: [docs/developer-guide/testing.md](docs/developer-guide/testing.md)
- **Building**: [docs/developer-guide/building.md](docs/developer-guide/building.md)

---

Thank you for contributing to CLX! 🎉
