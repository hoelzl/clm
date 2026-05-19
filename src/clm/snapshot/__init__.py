"""Build-output snapshot and verification.

Public API:
    verify_against(snapshot_dir, output_dir, *, include_html, strict) -> VerifyReport
    verify_against_targets(snapshot_dir, targets, *, include_html, strict) -> VerifyReport
    VerifyReport
"""

from clm.snapshot.verifier import (
    VerifyReport,
    verify_against,
    verify_against_targets,
)

__all__ = ["VerifyReport", "verify_against", "verify_against_targets"]
