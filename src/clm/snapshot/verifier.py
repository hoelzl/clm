"""Compare two CLM build-output trees byte-by-byte and produce a
class-aware report.

Migration verification (the Phase-1 use case in the slide-format-redesign
track) needs to confirm that an applied change produced byte-identical
build output relative to a pre-change snapshot. The fixed point of
"byte-identical" is intentionally strict: any diff that is not an
explicitly-tolerated normalization (today, just hex memory addresses
in HTML when ``include_html=True``) shows up as a failure.

By default ``.html`` files are skipped because their content includes
live-kernel execution output. CLM's notebook conversion is fully
deterministic post-PR-#76, but slides whose source code uses
``random.choice``, ``print(obj)``-with-default-``__repr__``, or has
intermixed stdout/exception output produce different rendered HTML each
run — these are properties of slide content, not of CLM. Skipping HTML
removes that noise floor; ``--include-html`` re-enables it with hex
address normalization, and ``--strict`` turns off all skips and
normalization.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from clm.snapshot.normalize import normalize_for_compare

# File extensions whose content can vary run-to-run because they include
# the output of live kernel execution. Excluded from comparison unless
# the user opts in with ``--include-html`` (which then applies
# normalization) or ``--strict`` (which compares them raw).
_NOISY_EXTENSIONS: frozenset[str] = frozenset({".html"})


@dataclass
class ExtCounts:
    total: int = 0
    identical: int = 0
    differing: int = 0
    skipped: int = 0
    missing: int = 0  # missing in output (was in snapshot but build did not produce it)


@dataclass
class VerifyReport:
    snapshot_dir: Path
    output_dir: Path
    identical: list[Path] = field(default_factory=list)
    differing: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    missing_in_output: list[Path] = field(default_factory=list)
    missing_in_snapshot: list[Path] = field(default_factory=list)
    by_extension: dict[str, ExtCounts] = field(default_factory=lambda: defaultdict(ExtCounts))

    @property
    def has_diffs(self) -> bool:
        """True if any non-skipped file diverged or is missing on either side."""
        return bool(self.differing or self.missing_in_output or self.missing_in_snapshot)

    @property
    def total_files(self) -> int:
        return (
            len(self.identical)
            + len(self.differing)
            + len(self.skipped)
            + len(self.missing_in_output)
        )

    def format_text(self) -> str:
        """Human-readable single-page summary."""
        lines: list[str] = []
        lines.append(f"Snapshot:        {self.snapshot_dir}")
        lines.append(f"Output:          {self.output_dir}")
        lines.append("")
        lines.append(f"Files compared:  {self.total_files}")
        lines.append(f"  Identical:     {len(self.identical)}")
        lines.append(f"  Differing:     {len(self.differing)}")
        lines.append(f"  Skipped:       {len(self.skipped)}")
        lines.append(f"  Missing in output:    {len(self.missing_in_output)}")
        lines.append(f"  Missing in snapshot:  {len(self.missing_in_snapshot)}")

        if self.by_extension:
            lines.append("")
            lines.append("By extension:")
            lines.append(
                f"  {'ext':<12} {'total':>6} {'ident':>6} {'diff':>6} {'skip':>6} {'miss':>6}"
            )
            for ext in sorted(self.by_extension):
                c = self.by_extension[ext]
                ext_display = ext or "<none>"
                lines.append(
                    f"  {ext_display:<12} {c.total:>6} {c.identical:>6} "
                    f"{c.differing:>6} {c.skipped:>6} {c.missing:>6}"
                )

        # List concrete diffs — cap to keep the report scannable.
        cap = 50
        if self.differing:
            lines.append("")
            lines.append(f"Differing files ({len(self.differing)}):")
            for p in self.differing[:cap]:
                lines.append(f"  ~ {p.as_posix()}")
            if len(self.differing) > cap:
                lines.append(f"  ... and {len(self.differing) - cap} more")
        if self.missing_in_output:
            lines.append("")
            lines.append(f"Missing in output ({len(self.missing_in_output)}):")
            for p in self.missing_in_output[:cap]:
                lines.append(f"  - {p.as_posix()}")
            if len(self.missing_in_output) > cap:
                lines.append(f"  ... and {len(self.missing_in_output) - cap} more")
        if self.missing_in_snapshot:
            lines.append("")
            lines.append(f"Missing in snapshot ({len(self.missing_in_snapshot)}):")
            for p in self.missing_in_snapshot[:cap]:
                lines.append(f"  + {p.as_posix()}")
            if len(self.missing_in_snapshot) > cap:
                lines.append(f"  ... and {len(self.missing_in_snapshot) - cap} more")

        return "\n".join(lines)


def _collect_files(root: Path) -> set[Path]:
    """Return relative paths of every regular file under *root*."""
    return {f.relative_to(root) for f in root.rglob("*") if f.is_file()}


def _should_skip(rel_path: Path, *, include_html: bool, strict: bool) -> bool:
    if strict:
        return False
    return rel_path.suffix in _NOISY_EXTENSIONS and not include_html


def _content_matches(
    snap_file: Path,
    out_file: Path,
    rel_path: Path,
    *,
    include_html: bool,
    strict: bool,
) -> bool:
    """Byte-compare two files, optionally normalizing both sides first.

    Normalization is only applied when ``include_html`` is true; under
    ``strict`` even include_html is moot (strict turns off normalization
    entirely).
    """
    snap_bytes = snap_file.read_bytes()
    out_bytes = out_file.read_bytes()
    if strict:
        return snap_bytes == out_bytes
    rel = rel_path.as_posix()
    return normalize_for_compare(
        rel, snap_bytes, include_html=include_html
    ) == normalize_for_compare(rel, out_bytes, include_html=include_html)


def verify_against(
    snapshot_dir: Path,
    output_dir: Path,
    *,
    include_html: bool = False,
    strict: bool = False,
) -> VerifyReport:
    """Compare *output_dir* against *snapshot_dir* and return a report.

    Args:
        snapshot_dir: Path to a previously-captured build output. Files
            here are the expected baseline.
        output_dir: Path to a freshly-built output tree to verify.
        include_html: If True, include ``.html`` files in the comparison
            using hex-address normalization. Default skips HTML because
            slide content with random output or default-``__repr__``
            objects produces irreducible per-run noise.
        strict: If True, byte-compare every file with no normalization
            and no skipping. Overrides ``include_html``.

    Returns:
        A :class:`VerifyReport`. Inspect ``has_diffs`` for a pass/fail
        signal and ``format_text()`` for a human-readable summary.
    """
    snapshot_dir = snapshot_dir.resolve()
    output_dir = output_dir.resolve()

    if not snapshot_dir.is_dir():
        raise FileNotFoundError(f"Snapshot directory does not exist: {snapshot_dir}")
    if not output_dir.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

    snap_files = _collect_files(snapshot_dir)
    out_files = _collect_files(output_dir)

    report = VerifyReport(snapshot_dir=snapshot_dir, output_dir=output_dir)

    common = snap_files & out_files
    only_in_snap = snap_files - out_files
    only_in_out = out_files - snap_files

    for rel in sorted(common):
        ext = rel.suffix
        report.by_extension[ext].total += 1
        if _should_skip(rel, include_html=include_html, strict=strict):
            report.skipped.append(rel)
            report.by_extension[ext].skipped += 1
            continue
        snap_file = snapshot_dir / rel
        out_file = output_dir / rel
        if _content_matches(snap_file, out_file, rel, include_html=include_html, strict=strict):
            report.identical.append(rel)
            report.by_extension[ext].identical += 1
        else:
            report.differing.append(rel)
            report.by_extension[ext].differing += 1

    for rel in sorted(only_in_snap):
        ext = rel.suffix
        report.by_extension[ext].total += 1
        report.by_extension[ext].missing += 1
        report.missing_in_output.append(rel)

    for rel in sorted(only_in_out):
        report.missing_in_snapshot.append(rel)

    return report
