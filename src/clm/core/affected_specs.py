"""Map changed source paths to the course specs whose builds they affect (issue #350).

Backs ``clm query affected-specs``: given a set of changed paths (typically
``git diff --name-only`` output) and a directory of course specs, report which
specs' builds those changes can influence, so a CI matrix can build only the
affected courses instead of all of them.

The mapping is **build-faithful**: every spec's claimed input surface is
derived from the same resolution the build uses —

* topic references resolve through :func:`clm.core.topic_resolver.build_topic_map`
  with the spec's section/topic ``module=`` bindings;
* a directory topic claims its whole subtree (slides, ``img/``, data files);
* a single-file topic additionally claims the sibling files its content
  references, replicating ``FileTopic.build_file_map`` (images via
  :func:`find_images`, imported modules via :func:`find_imports`);
* ``<include source=...>`` and ``<dir-group path=...>`` claim their
  course-root-relative sources;
* JupyterLite wheels/environment, output-target paths, and release-channel
  paths/ledgers are claimed by their owning spec.

The mapping **fails open**: a path that no spec claims but that is not clearly
build-irrelevant (Jinja macros, shared headers, the spec dir itself, ...)
marks *every* spec as affected. Only obviously irrelevant paths (``.github/``,
top-level docs) and content invisible to the build (topics no spec references,
underscore-prefixed dirs under ``slides/``) affect nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.topic_resolver import TopicMatch, build_topic_map, matches_for_binding
from clm.core.utils.notebook_utils import find_images, find_imports
from clm.infrastructure.utils.path_utils import (
    is_private_dir_name,
    prog_lang_to_extension,
)

__all__ = [
    "STATUS_CLAIMED",
    "STATUS_IGNORED",
    "STATUS_UNKNOWN",
    "STATUS_UNREFERENCED",
    "AffectedSpecsReport",
    "PathVerdict",
    "SpecClaims",
    "compute_spec_claims",
    "find_affected_specs",
    "render_report",
    "report_to_dict",
]

# Verdict statuses for a single input path.
STATUS_CLAIMED = "claimed"  # at least one spec's build consumes this path
STATUS_IGNORED = "ignored"  # clearly build-irrelevant (.github/, top-level docs)
STATUS_UNREFERENCED = "unreferenced"  # invisible to every build (unreferenced topic, _archive)
STATUS_UNKNOWN = "unknown"  # build-relevant but unclaimed -> fail open, affects ALL specs

# Top-level directories that can never influence a course build.
_IRRELEVANT_TOP_DIRS = frozenset({".git", ".github", ".idea", ".vscode"})

# Top-level files that can never influence a course build.
_IRRELEVANT_TOP_FILES = frozenset(
    {".gitignore", ".gitattributes", ".editorconfig", "LICENSE", "LICENSE.txt"}
)


@dataclass
class SpecClaims:
    """The course-root-relative input surface one spec's build consumes.

    ``claims`` holds POSIX-style relative paths; a directory claim covers its
    whole subtree. ``parse_error`` is set when the spec file could not be
    parsed — its claims are then unknown and the spec is conservatively
    treated as affected by every build-relevant change.
    """

    name: str
    spec_file: Path
    claims: set[str] = field(default_factory=set)
    parse_error: str | None = None


@dataclass
class PathVerdict:
    """Classification of a single input path."""

    path: str
    status: str
    specs: list[str] = field(default_factory=list)


@dataclass
class AffectedSpecsReport:
    """Outcome of mapping a change set onto a spec directory.

    ``specs`` is the sorted list of affected spec names (file stems). When
    ``all_affected`` is set (fail open), it equals ``all_specs`` so scripted
    consumers can feed it into a build matrix without branching.
    """

    specs: list[str]
    all_affected: bool
    all_specs: list[str]
    verdicts: list[PathVerdict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _rel_to_root(path: Path, course_root: Path) -> str | None:
    """*path* as a POSIX path relative to *course_root*, or ``None`` if outside."""
    try:
        return path.resolve().relative_to(course_root.resolve()).as_posix()
    except ValueError:
        return None


def _normalize_rel(raw: str) -> str | None:
    """Normalize a spec-declared relative path to canonical POSIX form.

    Returns ``None`` for empty, absolute, ``..``-escaping, or course-root
    (``.``) paths — such claims are skipped (an unmatched path then fails
    open, which is the safe direction).
    """
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _normalize_input(raw: str, course_root: Path) -> str | None:
    """Normalize one user-supplied changed path to course-root-relative POSIX form.

    Absolute paths are re-rooted at *course_root* when possible; an absolute
    path outside the root is kept verbatim so it falls through to the
    fail-open branch instead of silently matching nothing.
    """
    cleaned = raw.strip().strip('"').replace("\\", "/")
    if not cleaned:
        return None
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        rel = _rel_to_root(Path(cleaned), course_root)
        return rel if rel is not None else cleaned
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts:
        return None
    return "/".join(parts)


def _overlaps(a: str, b: str) -> bool:
    """True when one POSIX path equals or contains the other."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _is_build_irrelevant(path: str) -> bool:
    """Clearly build-irrelevant paths: CI config, repo metadata, top-level docs."""
    parts = path.split("/")
    if parts[0] in _IRRELEVANT_TOP_DIRS:
        return True
    if len(parts) == 1:
        name = parts[0]
        return name.lower().endswith(".md") or name in _IRRELEVANT_TOP_FILES
    return False


def _file_topic_sibling_claims(
    topic_file: Path,
    prog_lang: str,
    course_root: Path,
    cache: dict[tuple[Path, str], set[str]],
) -> set[str]:
    """Sibling files a single-file topic pulls in, per ``FileTopic.build_file_map``.

    A file topic claims the images and imported module files its content
    references, resolved against its parent (module) directory — this is how
    a shared header next to several decks ends up owned by every course that
    builds one of them.
    """
    try:
        ext = prog_lang_to_extension(prog_lang)
    except KeyError:
        ext = ""
    key = (topic_file, ext)
    cached = cache.get(key)
    if cached is not None:
        return cached

    claims: set[str] = set()
    try:
        contents = topic_file.read_text(encoding="utf-8")
    except OSError:
        contents = ""
    if contents:
        referenced = set(find_images(contents))
        referenced.update(module + ext for module in find_imports(contents))
        for name in referenced:
            rel = _rel_to_root(topic_file.parent / name, course_root)
            if rel is not None:
                claims.add(rel)
    cache[key] = claims
    return claims


def compute_spec_claims(
    spec_file: Path,
    course_root: Path,
    topic_map: dict[str, list[TopicMatch]],
    *,
    file_topic_cache: dict[tuple[Path, str], set[str]] | None = None,
) -> SpecClaims:
    """Compute the course-root-relative input surface of one spec's build.

    Topic bindings resolve through *topic_map* exactly as the build does; an
    unbound topic ID that exists in several modules claims **all** matches
    (the build picks one first-occurrence-wins, but a change to either copy
    may affect the outcome, so over-claiming is the safe direction).
    """
    if file_topic_cache is None:
        file_topic_cache = {}

    claims: set[str] = set()
    spec_rel = _rel_to_root(spec_file, course_root)
    if spec_rel is not None:
        claims.add(spec_rel)

    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as exc:
        return SpecClaims(
            name=spec_file.stem, spec_file=spec_file, claims=claims, parse_error=str(exc)
        )

    for binding in spec.iter_topic_bindings():
        for match in matches_for_binding(topic_map, binding.topic_id, binding.effective_module):
            match_rel = _rel_to_root(match.path, course_root)
            if match_rel is not None:
                claims.add(match_rel)
            if match.path_type == "file":
                prog_lang = binding.topic_spec.prog_lang or spec.prog_lang
                claims.update(
                    _file_topic_sibling_claims(match.path, prog_lang, course_root, file_topic_cache)
                )
        # Include sources are parse-time normalized course-root-relative POSIX paths.
        for include in binding.section.includes_for(binding.topic_spec):
            claims.add(include.source)

    for dir_group in spec.dictionaries:
        claim = _normalize_rel(dir_group.path)
        if claim is not None:
            claims.add(claim)

    for jl in (spec.jupyterlite, *(target.jupyterlite for target in spec.output_targets)):
        if jl is None:
            continue
        for wheel in jl.wheels:
            claim = _normalize_rel(wheel)
            if claim is not None:
                claims.add(claim)
        if jl.environment:
            claim = _normalize_rel(jl.environment)
            if claim is not None:
                claims.add(claim)

    # Output dirs and release working trees/ledgers are not build *inputs*,
    # but a change there concerns exactly this spec — mapping them here is
    # strictly better than letting them fall open to an all-specs rebuild.
    for target in spec.output_targets:
        claim = _normalize_rel(target.path)
        if claim is not None:
            claims.add(claim)
    for block in spec.release_channel_blocks:
        for channel in block.channels:
            for raw in (channel.path, channel.ledger):
                claim = _normalize_rel(raw)
                if claim is not None:
                    claims.add(claim)

    return SpecClaims(name=spec_file.stem, spec_file=spec_file, claims=claims)


def _classify_path(
    path: str,
    parsed_claims: list[SpecClaims],
    topic_roots: set[str],
    spec_dir_rel: str | None,
) -> PathVerdict:
    if _is_build_irrelevant(path):
        return PathVerdict(path=path, status=STATUS_IGNORED)

    matched = sorted(
        {sc.name for sc in parsed_claims if any(_overlaps(path, c) for c in sc.claims)}
    )
    if matched:
        return PathVerdict(path=path, status=STATUS_CLAIMED, specs=matched)

    # Unclaimed content under the spec dir (a deleted spec, a shared fragment)
    # cannot be attributed -> fail open.
    if spec_dir_rel is not None and _is_under(path, spec_dir_rel):
        return PathVerdict(path=path, status=STATUS_UNKNOWN)

    if _is_under(path, "slides"):
        # Inside a discovered topic that no spec references: only topic
        # references consume topic dirs (includes were checked above), so
        # the change affects no build.
        if any(_overlaps(path, root) for root in topic_roots):
            return PathVerdict(path=path, status=STATUS_UNREFERENCED)
        # Underscore-prefixed dirs are invisible to topic discovery (issue
        # #318); parked content affects no build unless an include claims it.
        parts = path.split("/")
        if any(is_private_dir_name(part) for part in parts[1:]):
            return PathVerdict(path=path, status=STATUS_UNREFERENCED)

    return PathVerdict(path=path, status=STATUS_UNKNOWN)


def find_affected_specs(
    raw_paths: list[str],
    spec_dir: Path,
    *,
    course_root: Path | None = None,
    topic_map: dict[str, list[TopicMatch]] | None = None,
) -> AffectedSpecsReport:
    """Map *raw_paths* (e.g. ``git diff --name-only`` output) to affected specs.

    Paths are interpreted relative to *course_root* (default: the parent of
    *spec_dir*, matching the ``<root>/course-specs/`` convention). Every
    ``*.xml`` file in *spec_dir* is resolved once; an unparseable spec is
    reported as a warning and conservatively marked affected whenever any
    build-relevant path changed.
    """
    spec_dir = spec_dir.resolve()
    root = (course_root if course_root is not None else spec_dir.parent).resolve()
    if topic_map is None:
        topic_map = build_topic_map(root / "slides")

    spec_files = sorted(spec_dir.glob("*.xml"))
    file_topic_cache: dict[tuple[Path, str], set[str]] = {}
    all_claims = [
        compute_spec_claims(f, root, topic_map, file_topic_cache=file_topic_cache)
        for f in spec_files
    ]
    parsed_claims = [sc for sc in all_claims if sc.parse_error is None]
    broken_specs = [sc for sc in all_claims if sc.parse_error is not None]

    warnings = [
        f"spec '{sc.spec_file.name}' failed to parse — treating it as affected by "
        f"every build-relevant change: {sc.parse_error}"
        for sc in broken_specs
    ]

    topic_roots = {
        rel
        for matches in topic_map.values()
        for match in matches
        if (rel := _rel_to_root(match.path, root)) is not None
    }
    spec_dir_rel = _rel_to_root(spec_dir, root)

    verdicts: list[PathVerdict] = []
    seen: set[str] = set()
    affected: set[str] = set()
    all_affected = False
    any_relevant = False
    for raw in raw_paths:
        norm = _normalize_input(raw, root)
        if norm is None or norm in seen:
            continue
        seen.add(norm)
        verdict = _classify_path(norm, parsed_claims, topic_roots, spec_dir_rel)
        verdicts.append(verdict)
        if verdict.status == STATUS_CLAIMED:
            affected.update(verdict.specs)
            any_relevant = True
        elif verdict.status == STATUS_UNKNOWN:
            all_affected = True
            any_relevant = True

    # A spec whose claims are unknown is affected by any build-relevant change.
    if any_relevant:
        affected.update(sc.name for sc in broken_specs)

    all_specs = sorted(sc.name for sc in all_claims)
    specs = all_specs if all_affected else sorted(affected)
    return AffectedSpecsReport(
        specs=specs,
        all_affected=all_affected,
        all_specs=all_specs,
        verdicts=verdicts,
        warnings=warnings,
    )


_STATUS_LABELS = {
    STATUS_IGNORED: "(ignored: build-irrelevant)",
    STATUS_UNREFERENCED: "(unreferenced: affects no spec)",
    STATUS_UNKNOWN: "(unclaimed: fail open -> ALL specs)",
}


def render_report(report: AffectedSpecsReport) -> str:
    """Human-readable per-path table plus an affected-specs summary."""
    lines: list[str] = []
    if report.verdicts:
        width = max(len(v.path) for v in report.verdicts)
        for verdict in report.verdicts:
            label = (
                ", ".join(verdict.specs)
                if verdict.status == STATUS_CLAIMED
                else _STATUS_LABELS[verdict.status]
            )
            lines.append(f"{verdict.path:<{width}}  ->  {label}")
        lines.append("")

    total = len(report.all_specs)
    if report.all_affected:
        lines.append(f"Affected specs: ALL ({total}): {', '.join(report.all_specs)}")
    elif report.specs:
        lines.append(f"Affected specs ({len(report.specs)}/{total}): {', '.join(report.specs)}")
    else:
        lines.append(f"Affected specs (0/{total}): none")
    return "\n".join(lines)


def report_to_dict(report: AffectedSpecsReport) -> dict:
    """JSON-serializable report. ``specs`` lists every spec when ``all`` is true."""
    return {
        "specs": report.specs,
        "all": report.all_affected,
        "paths": [{"path": v.path, "status": v.status, "specs": v.specs} for v in report.verdicts],
    }
