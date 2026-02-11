"""Error categorization for build reporting.

This module provides functionality to analyze and categorize build errors
into user errors, configuration errors, and infrastructure errors, with
actionable guidance for each type.
"""

import json
import re
from typing import Any, Literal, cast

from clm.cli.build_data_classes import BuildError
from clm.cli.text_utils import strip_ansi


class ErrorCategorizer:
    """Categorizes errors and generates actionable guidance."""

    @staticmethod
    def categorize_job_error(
        job_type: str,
        input_file: str,
        error_message: str,
        job_payload: dict[str, Any],
        job_id: int | None = None,
        correlation_id: str | None = None,
    ) -> BuildError:
        """Categorize a job failure error.

        Args:
            job_type: Type of job (notebook, plantuml, drawio)
            input_file: Path to input file
            error_message: Error message from worker (may be JSON string or plain text)
            job_payload: Job payload dict
            job_id: Optional job ID
            correlation_id: Optional correlation ID

        Returns:
            Categorized BuildError
        """
        # Try to parse structured error if it's JSON
        error_info = ErrorCategorizer._parse_error_message(error_message)

        # Delegate to specific categorizer based on job type
        if job_type == "notebook":
            return ErrorCategorizer._categorize_notebook_error(
                input_file, error_info, job_payload, job_id, correlation_id
            )
        elif job_type == "plantuml":
            return ErrorCategorizer._categorize_plantuml_error(
                input_file, error_info, job_id, correlation_id
            )
        elif job_type == "drawio":
            return ErrorCategorizer._categorize_drawio_error(
                input_file, error_info, job_id, correlation_id
            )
        else:
            # Unknown job type - infrastructure error
            return BuildError(
                error_type="infrastructure",
                category="unknown_job_type",
                severity="error",
                file_path=input_file,
                message=f"Unknown job type: {job_type}",
                actionable_guidance="This is likely a bug in CLM. Please report this issue.",
                job_id=job_id,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _parse_error_message(error_message: str) -> dict[str, Any]:
        """Parse error message into structured format.

        Attempts to parse JSON-formatted error messages from workers.
        Falls back to plain text if not JSON.
        Strips ANSI escape sequences from all text values.

        Args:
            error_message: Error message string (possibly JSON)

        Returns:
            Dictionary with error information
        """
        # Strip ANSI sequences from the raw message first
        cleaned_message = strip_ansi(error_message) if error_message else error_message

        # Try to parse as JSON
        try:
            error_info = json.loads(cleaned_message)
            if isinstance(error_info, dict):
                # Strip ANSI from all string values in the dict
                return ErrorCategorizer._strip_ansi_from_dict(error_info)
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: treat as plain text
        return {"error_message": cleaned_message}

    @staticmethod
    def _strip_ansi_from_dict(d: dict[str, Any]) -> dict[str, Any]:
        """Recursively strip ANSI sequences from all string values in a dict.

        Args:
            d: Dictionary that may contain strings with ANSI sequences

        Returns:
            Dictionary with ANSI sequences stripped from string values
        """
        result: dict[str, Any] = {}
        for key, value in d.items():
            if isinstance(value, str):
                result[key] = strip_ansi(value)
            elif isinstance(value, dict):
                result[key] = ErrorCategorizer._strip_ansi_from_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    strip_ansi(item) if isinstance(item, str) else item for item in value
                ]
            else:
                result[key] = value
        return result

    @staticmethod
    def _categorize_notebook_error(
        input_file: str,
        error_info: dict[str, Any],
        payload: dict[str, Any],
        job_id: int | None,
        correlation_id: str | None,
    ) -> BuildError:
        """Categorize notebook processing error.

        Args:
            input_file: Path to notebook file
            error_info: Structured error information
            payload: Job payload
            job_id: Job ID
            correlation_id: Correlation ID

        Returns:
            Categorized BuildError
        """
        error_message = error_info.get("error_message", "Unknown error")
        error_class = error_info.get("error_class", "")
        traceback = error_info.get("traceback", "")

        # Parse notebook-specific details
        details = ErrorCategorizer._parse_notebook_error(error_message, traceback)

        # Categorize based on error patterns
        if any(
            err in error_message or err in error_class
            for err in ["SyntaxError", "NameError", "IndentationError", "TypeError"]
        ):
            error_type = "user"
            category = "notebook_compilation"
            cell_info = f" in cell #{details['cell_number']}" if details.get("cell_number") else ""
            guidance = f"Fix the {error_class or 'error'}{cell_info} in your notebook"

        elif "FileNotFoundError" in error_message and "template" in error_message.lower():
            error_type = "configuration"
            category = "missing_template"
            guidance = "Ensure Jinja templates are available in the template directory"

        elif "TimeoutError" in error_message or "worker" in error_message.lower():
            error_type = "infrastructure"
            category = "worker_timeout"
            guidance = "Worker timed out. Check worker logs with 'clm monitor'"

        elif "ModuleNotFoundError" in error_message or "ImportError" in error_message:
            error_type = "user"
            category = "missing_module"
            guidance = "Install the required Python module or check your imports"

        elif "CellExecutionError" in error_message:
            # Cell execution failed - extract more details
            error_type = "user"
            category = "cell_execution"
            cell_info = f" in cell #{details['cell_number']}" if details.get("cell_number") else ""
            guidance = f"Fix the error{cell_info} in your notebook"

        else:
            # Default to user error for notebooks (most likely)
            error_type = "user"
            category = "notebook_processing"
            cell_info = f" in cell #{details['cell_number']}" if details.get("cell_number") else ""
            guidance = f"Check your notebook for errors{cell_info}"

        return BuildError(
            error_type=cast(Literal["user", "configuration", "infrastructure"], error_type),
            category=category,
            severity="error",
            file_path=input_file,
            message=error_message,
            actionable_guidance=guidance,
            job_id=job_id,
            correlation_id=correlation_id,
            details=details,
        )

    @staticmethod
    def _parse_notebook_error(error_message: str, traceback: str = "") -> dict[str, Any]:
        """Parse notebook error message to extract structured details.

        Looks for patterns like:
        - Cell number: "in cell #5" or "at cell 5" or "Cell[5]" or "Cell: #5"
        - Error class: "SyntaxError:", "NameError:", C++ errors like "error:"
        - Line number within cell: "line 3"
        - Code snippet (if included in traceback or error)
        - C++ compiler errors: "error: expected ';'"

        Args:
            error_message: Error message
            traceback: Full traceback (optional)

        Returns:
            Dictionary with parsed details
        """
        details: dict[str, Any] = {}

        # Combine message and traceback for parsing
        full_text = f"{error_message}\n{traceback}"

        # Extract cell number with multiple patterns
        # Pattern 1: "in cell #5" or "at cell 5"
        cell_match = re.search(r"(?:in|at)\s+[Cc]ell\s*#?(\d+)", full_text)
        if not cell_match:
            # Pattern 2: "Cell[5]" or "Cell 5"
            cell_match = re.search(r"[Cc]ell\s*\[?(\d+)\]?", full_text)
        if not cell_match:
            # Pattern 3: "Cell: #5" (from _enhance_notebook_error)
            cell_match = re.search(r"[Cc]ell:\s*#?(\d+)", full_text)
        if cell_match:
            details["cell_number"] = int(cell_match.group(1))

        # Extract error class (with or without colon)
        error_class_match = re.search(r"(\w+(?:Error|Exception))\s*:?\s*", full_text)
        if error_class_match:
            details["error_class"] = error_class_match.group(1)

            # Extract short message (first line after error class and optional colon)
            msg_start = error_class_match.end()
            msg_end = full_text.find("\n", msg_start)
            if msg_end > msg_start:
                details["short_message"] = full_text[msg_start:msg_end].strip()
            elif full_text[msg_start:].strip():
                details["short_message"] = full_text[msg_start:].strip()

        # Check for C++ compiler errors (xeus-cling style)
        # Pattern: "input_line_X:Y:Z: error: message"
        cpp_error_match = re.search(
            r"input_line_\d+:(\d+):(\d+):\s*error:\s*(.+?)(?:\n|$)", full_text
        )
        if cpp_error_match:
            if "line_number" not in details:
                details["line_number"] = int(cpp_error_match.group(1))
            details["column_number"] = int(cpp_error_match.group(2))
            details["error_class"] = "CompilationError"
            details["short_message"] = cpp_error_match.group(3).strip()

        # Also check for generic clang-style errors: "file:line:col: error: message"
        if "error_class" not in details or details.get("error_class") != "CompilationError":
            clang_error = re.search(r":\s*(\d+):\s*(\d+):\s*error:\s*(.+?)(?:\n|$)", full_text)
            if clang_error:
                if "line_number" not in details:
                    details["line_number"] = int(clang_error.group(1))
                details["column_number"] = int(clang_error.group(2))
                details["error_class"] = "CompilationError"
                details["short_message"] = clang_error.group(3).strip()

        # Extract line number within cell (if not already found)
        if "line_number" not in details:
            # Pattern 1: "Line: N" format (from enhanced errors)
            line_match = re.search(r"[Ll]ine:\s*(\d+)", full_text)
            if not line_match:
                # Pattern 2: "line N" format (without colon)
                line_match = re.search(r"line\s+(\d+)", full_text, re.IGNORECASE)
            if line_match:
                details["line_number"] = int(line_match.group(1))

        # Extract code snippet - prioritize "Cell content:" from enhanced errors
        code_lines: list[str] = []

        # First, try to extract "Cell content:" section (most reliable for our enhanced errors)
        # The cell content ends at the "Error:" line, so we need to stop there
        cell_content_match = re.search(
            r"Cell content:\s*\n((?:\s+.+\n?)+?)(?=\s*Error:|\Z)", full_text, re.MULTILINE
        )
        if cell_content_match:
            cell_content = cell_content_match.group(1)
            code_lines = [line.strip() for line in cell_content.split("\n") if line.strip()]

        # If no cell content found, try other patterns (for standard Python errors)
        if not code_lines:
            in_code_block = False
            for line in full_text.split("\n"):
                # Pattern 1: Lines with line numbers (e.g., "  5: x = 1")
                if re.match(r"^\s*\d+:", line):
                    code_lines.append(line.strip())
                    in_code_block = True
                # Pattern 2: Lines starting with >>> (Python interactive)
                elif line.strip().startswith(">>>") or line.strip().startswith("..."):
                    code_lines.append(line.strip())
                    in_code_block = True
                # Pattern 3: Indented code after "--->" marker (IPython tracebacks)
                elif "--->" in line:
                    code_lines.append(line.strip())
                    in_code_block = True
                # Pattern 4: Continue collecting indented lines after code block started
                # But avoid Python traceback lines (those with ^ underlines or "await", "result =")
                elif (
                    in_code_block
                    and line.strip()
                    and (line.startswith("    ") or line.startswith("\t"))
                    and not re.match(r"^\s*\^+\s*$", line)  # Skip caret-only lines
                    and "await " not in line  # Skip async Python traceback
                    and "result = " not in line  # Skip assignment from traceback
                ):
                    code_lines.append(line.strip())
                # Stop if we hit a non-code line after starting
                elif in_code_block and not line.strip():
                    break

        if code_lines:
            # Limit to first 10 lines for readability
            details["code_snippet"] = "\n".join(code_lines[:10])
            if len(code_lines) > 10:
                details["code_snippet"] += "\n... (truncated)"

        # Extract file path if present
        file_match = re.search(r'File\s+"([^"]+)"', full_text)
        if file_match:
            details["source_file"] = file_match.group(1)

        return details

    @staticmethod
    def _categorize_plantuml_error(
        input_file: str,
        error_info: dict[str, Any],
        job_id: int | None,
        correlation_id: str | None,
    ) -> BuildError:
        """Categorize PlantUML processing error.

        Args:
            input_file: Path to PlantUML file
            error_info: Structured error information
            job_id: Job ID
            correlation_id: Correlation ID

        Returns:
            Categorized BuildError
        """
        error_message = error_info.get("error_message", "Unknown error")
        error_class = error_info.get("error_class", "")
        error_lower = error_message.lower()

        # Check for missing PlantUML JAR or Java - specific patterns only
        # Avoid matching generic "not found" which could be input file errors
        is_missing_plantuml = (
            "PLANTUML_JAR" in error_message
            or "plantuml jar not found" in error_lower
            or "java: command not found" in error_lower
            or "java: not found" in error_lower
            or "'java' is not recognized" in error_lower
            # Non-retriable error from subprocess (executable not found)
            or (
                "command failed with non-retriable error" in error_lower
                and ("errno 2" in error_lower or "filenotfounderror" in error_lower)
                and "plantuml" not in error_lower  # Not about output file
            )
        )

        if is_missing_plantuml:
            return BuildError(
                error_type="configuration",
                category="missing_plantuml",
                severity="error",
                file_path=input_file,
                message=error_message,
                actionable_guidance=(
                    "Install PlantUML JAR and set PLANTUML_JAR environment variable. "
                    "See documentation for setup instructions."
                ),
                job_id=job_id,
                correlation_id=correlation_id,
            )

        # Check for missing input file - this is a different error type
        # Note: This includes Docker-specific errors where the mount may be misconfigured
        is_input_file_missing = (
            "input file not found" in error_lower
            or "input file not found in docker container" in error_lower
            or (error_class == "FileNotFoundError" and "input" in error_lower)
            or ("no such file or directory" in error_lower and ".puml" in error_lower)
        )

        if is_input_file_missing:
            # Provide Docker-specific guidance if it's a Docker mount issue
            is_docker_error = "docker container" in error_lower or "docker mount" in error_lower
            if is_docker_error:
                guidance = (
                    "The input file could not be found inside the Docker container. "
                    "This usually means the Docker volume mount is misconfigured. "
                    "Verify: (1) The file exists on the host, (2) The data directory "
                    "is correctly specified with --data-dir, (3) Docker has access to "
                    "the file path."
                )
            else:
                guidance = (
                    "The input file could not be found. Verify the file path is correct "
                    "and the file exists."
                )
            return BuildError(
                error_type="configuration",
                category="missing_input_file",
                severity="error",
                file_path=input_file,
                message=error_message,
                actionable_guidance=guidance,
                job_id=job_id,
                correlation_id=correlation_id,
            )

        # Default: assume user error in PlantUML syntax
        return BuildError(
            error_type="user",
            category="plantuml_syntax",
            severity="error",
            file_path=input_file,
            message=error_message,
            actionable_guidance="Check your PlantUML diagram syntax",
            job_id=job_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _categorize_drawio_error(
        input_file: str,
        error_info: dict[str, Any],
        job_id: int | None,
        correlation_id: str | None,
    ) -> BuildError:
        """Categorize DrawIO processing error.

        Args:
            input_file: Path to DrawIO file
            error_info: Structured error information
            job_id: Job ID
            correlation_id: Correlation ID

        Returns:
            Categorized BuildError
        """
        error_message = error_info.get("error_message", "Unknown error")
        error_class = error_info.get("error_class", "")
        error_lower = error_message.lower()

        # Check for missing DrawIO executable - specific patterns only
        # Avoid matching generic "not found" which could be input file errors
        is_missing_drawio = (
            "DRAWIO_EXECUTABLE" in error_message
            or "drawio executable not found" in error_lower
            or "drawio: command not found" in error_lower
            or "drawio: not found" in error_lower
            or "'drawio' is not recognized" in error_lower
            # Non-retriable error from subprocess (executable not found)
            or (
                "command failed with non-retriable error" in error_lower
                and ("errno 2" in error_lower or "filenotfounderror" in error_lower)
                and "drawio" not in error_lower  # Not about output file
            )
        )

        if is_missing_drawio:
            return BuildError(
                error_type="configuration",
                category="missing_drawio",
                severity="error",
                file_path=input_file,
                message=error_message,
                actionable_guidance=(
                    "Install DrawIO desktop and set DRAWIO_EXECUTABLE environment variable. "
                    "See documentation for setup instructions."
                ),
                job_id=job_id,
                correlation_id=correlation_id,
            )

        # Check for missing input file - this is a different error type
        # Note: This includes Docker-specific errors where the mount may be misconfigured
        is_input_file_missing = (
            "input file not found" in error_lower
            or "input file not found in docker container" in error_lower
            or (error_class == "FileNotFoundError" and "input" in error_lower)
            or ("no such file or directory" in error_lower and ".drawio" in error_lower)
        )

        if is_input_file_missing:
            # Provide Docker-specific guidance if it's a Docker mount issue
            is_docker_error = "docker container" in error_lower or "docker mount" in error_lower
            if is_docker_error:
                guidance = (
                    "The input file could not be found inside the Docker container. "
                    "This usually means the Docker volume mount is misconfigured. "
                    "Verify: (1) The file exists on the host, (2) The data directory "
                    "is correctly specified with --data-dir, (3) Docker has access to "
                    "the file path."
                )
            else:
                guidance = (
                    "The input file could not be found. Verify the file path is correct "
                    "and the file exists."
                )
            return BuildError(
                error_type="configuration",
                category="missing_input_file",
                severity="error",
                file_path=input_file,
                message=error_message,
                actionable_guidance=guidance,
                job_id=job_id,
                correlation_id=correlation_id,
            )

        # Check for V8/Electron crashes - these are infrastructure errors
        is_v8_crash = (
            "disallowjavascriptexecutionscope" in error_lower
            or "fatal error in" in error_lower
            or "v8 error" in error_lower
        )

        if is_v8_crash:
            return BuildError(
                error_type="infrastructure",
                category="drawio_crash",
                severity="error",
                file_path=input_file,
                message=error_message,
                actionable_guidance=(
                    "DrawIO crashed during conversion. This may be a transient error. "
                    "Try running the build again, or check DrawIO installation."
                ),
                job_id=job_id,
                correlation_id=correlation_id,
            )

        # Default: assume user error in DrawIO diagram
        return BuildError(
            error_type="user",
            category="drawio_processing",
            severity="error",
            file_path=input_file,
            message=error_message,
            actionable_guidance="Check your DrawIO diagram for errors",
            job_id=job_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def categorize_no_workers_error(job_type: str) -> BuildError:
        """Create error for no workers available.

        Args:
            job_type: Type of job that needs workers

        Returns:
            Categorized fatal infrastructure error
        """
        return BuildError(
            error_type="infrastructure",
            category="no_workers",
            severity="fatal",
            file_path="",
            message=f"No workers available for job type '{job_type}'",
            actionable_guidance=(
                f"Start {job_type} workers with 'clm start-services' "
                f"or check worker health with 'clm status'"
            ),
        )

    @staticmethod
    def categorize_generic_error(
        message: str,
        file_path: str = "",
        error_type: Literal["user", "configuration", "infrastructure"] = "infrastructure",
        severity: Literal["error", "warning", "fatal"] = "error",
    ) -> BuildError:
        """Create a generic categorized error.

        Args:
            message: Error message
            file_path: Path to file (if applicable)
            error_type: Type of error
            severity: Error severity

        Returns:
            Categorized BuildError
        """
        guidance_map = {
            "user": "Check your input files and fix any issues",
            "configuration": "Check your CLM configuration and environment",
            "infrastructure": "This may be a bug in CLM. Check logs or file an issue",
        }

        return BuildError(
            error_type=error_type,
            category="generic_error",
            severity=severity,
            file_path=file_path,
            message=message,
            actionable_guidance=guidance_map[error_type],
        )
