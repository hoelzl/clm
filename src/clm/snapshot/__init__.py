"""Build-output snapshot and verification.

Public API:
    verify_against(snapshot_dir, output_dir, *, include_html, strict) -> VerifyReport
    VerifyReport
"""

from clm.snapshot.verifier import VerifyReport, verify_against

__all__ = ["VerifyReport", "verify_against"]
