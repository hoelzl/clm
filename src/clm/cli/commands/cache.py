"""Read-only build-cache inspection commands (issue #328).

``clm cache explain`` shows, for one slide source file, the exact cache-key
components a build would compute, the resulting hashes, and the hit/miss
state of every cache layer — so "would this build execute or replay, and
why" is answerable in seconds. Issue #321 took an hour to diagnose because
replayed output is freshly timestamped and nothing showed what the cache
key did (and did not) cover.

Everything here is read-only: cache databases are opened in SQLite ``ro``
mode and no output directories are created.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from base64 import b64decode
from pathlib import Path
from typing import Any

import click


@click.group("cache")
def cache_group():
    """Inspect CLM's build caches."""


def _open_readonly(db_path: Path) -> sqlite3.Connection | None:
    """Open a SQLite database strictly read-only; None when absent."""
    if not db_path.exists():
        return None
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def _query_one(conn: sqlite3.Connection | None, sql: str, params: tuple) -> tuple | None:
    if conn is None:
        return None
    try:
        row: tuple | None = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        # Tables are created lazily by their writers; a database file
        # without this table is simply an empty cache layer.
        return None
    return row


def _sibling_entries(payload) -> tuple[list[dict[str, Any]], str | None]:
    """Describe ``other_files`` for display: (entries, excluded cassette name).

    Sizes/digests are of the DECODED content so they match the files on
    disk (``other_files`` values are base64-encoded in the payload).
    """
    cassette_key = payload.http_replay_cassette_name
    entries = []
    for name in sorted(payload.other_files):
        raw = payload.other_files[name]
        try:
            content = b64decode(raw, validate=True)
        except Exception:
            content = raw
        entries.append(
            {
                "name": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "excluded": cassette_key is not None and name == cassette_key,
            }
        )
    excluded = cassette_key if any(e["excluded"] for e in entries) else None
    return entries, excluded


def _collect_artifact(payload, op, cache_db: Path, jobs_db: Path) -> dict[str, Any]:
    """Compute hashes and per-layer cache state for one payload."""
    content_hash = payload.content_hash()
    execution_hash = payload.execution_cache_hash()
    output_metadata = payload.output_metadata()

    cache_conn = _open_readonly(cache_db)
    jobs_conn = _open_readonly(jobs_db)
    try:
        processed = _query_one(
            cache_conn,
            """
            SELECT created_at, correlation_id FROM processed_files
            WHERE file_path = ? AND content_hash = ? AND output_metadata = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (payload.input_file, content_hash, output_metadata),
        )
        executed = _query_one(
            cache_conn,
            """
            SELECT created_at FROM executed_notebooks
            WHERE input_file = ? AND content_hash = ? AND language = ? AND prog_lang = ?
            """,
            (payload.input_file, execution_hash, payload.language, payload.prog_lang),
        )
        issues = _query_one(
            cache_conn,
            """
            SELECT
                SUM(CASE WHEN issue_type = 'error' THEN 1 ELSE 0 END),
                SUM(CASE WHEN issue_type = 'warning' THEN 1 ELSE 0 END)
            FROM processing_issues
            WHERE file_path = ? AND content_hash = ? AND output_metadata = ?
            """,
            (payload.input_file, content_hash, output_metadata),
        )
        results = _query_one(
            jobs_conn,
            """
            SELECT created_at, last_accessed, access_count FROM results_cache
            WHERE output_file = ? AND content_hash = ?
            """,
            (payload.output_file, content_hash),
        )
    finally:
        if cache_conn is not None:
            cache_conn.close()
        if jobs_conn is not None:
            jobs_conn.close()

    output_exists = Path(payload.output_file).exists()
    artifact: dict[str, Any] = {
        "kind": payload.kind,
        "format": payload.format,
        "language": payload.language,
        "output_metadata": output_metadata,
        "output_file": payload.output_file,
        "output_exists": output_exists,
        "content_hash": content_hash,
        "execution_cache_hash": execution_hash,
        "caches": {
            "processed_files": (
                {"stored_at": processed[0], "correlation_id": processed[1]} if processed else None
            ),
            "executed_notebooks": {"stored_at": executed[0]} if executed else None,
            "results_cache": (
                {
                    "stored_at": results[0],
                    "last_accessed": results[1],
                    "access_count": results[2],
                }
                if results
                else None
            ),
        },
        "stored_issues": {
            "errors": (issues[0] or 0) if issues else 0,
            "warnings": (issues[1] or 0) if issues else 0,
        },
    }
    artifact["verdict"] = _verdict(artifact)
    return artifact


def _verdict(artifact: dict[str, Any]) -> str:
    """Mirror SqliteBackend.execute_operation's cache-consultation order."""
    caches = artifact["caches"]
    if caches["processed_files"] is not None:
        # Stage-4 producer gate (_can_replay_from_cache): a Recording HTML
        # processed_files hit only replays when the execution cache is warm,
        # otherwise the worker runs to repopulate it for the consumers.
        if (
            artifact["kind"] in ("recording", "speaker")
            and artifact["format"] == "html"
            and caches["executed_notebooks"] is None
        ):
            return (
                "will execute (processed_files hit, but the execution cache is "
                "cold and this kind is its producer)"
            )
        return f"replays stored result from {caches['processed_files']['stored_at']}"
    if caches["results_cache"] is not None:
        if artifact["output_exists"]:
            return (
                f"skips execution (results_cache hit from "
                f"{caches['results_cache']['stored_at']}, output already on disk)"
            )
        return "will execute (results_cache hit but output file is missing)"
    if caches["executed_notebooks"] is not None and artifact["format"] == "html":
        return "will run worker, reusing the cached execution (no kernel start)"
    return "will execute"


def _unwrap_operations(op) -> list:
    """Flatten the Operation tree get_processing_operation returns."""
    from clm.core.operations.process_notebook import ProcessNotebookOperation
    from clm.infrastructure.operation import Concurrently

    if isinstance(op, ProcessNotebookOperation):
        return [op]
    if isinstance(op, Concurrently):
        result = []
        for child in op.operations:
            result.extend(_unwrap_operations(child))
        return result
    return []


@cache_group.command("explain")
@click.argument("source_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--spec",
    "spec_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Course spec XML that builds this file.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory. Default: inferred from the spec location.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Output root the build uses. Must match the build's to resolve the "
        "same output paths (the job-level cache keys on them). Default: the "
        "same fallback as clm build (<course root>/output, or the spec's "
        "output targets)."
    ),
)
@click.option(
    "--lang",
    "-L",
    "languages",
    multiple=True,
    help="Limit to output language(s), e.g. -L de.",
)
@click.option("--kind", "kinds", multiple=True, help="Limit to output kind(s).")
@click.option("--format", "formats", multiple=True, help="Limit to output format(s).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def explain_cmd(
    ctx,
    source_file: Path,
    spec_file: Path,
    data_dir: Path | None,
    output_dir: Path | None,
    languages: tuple[str, ...],
    kinds: tuple[str, ...],
    formats: tuple[str, ...],
    as_json: bool,
):
    """Explain the cache keys and cache state for one slide source file.

    Shows the exact key components a build would compute for SOURCE_FILE
    (dependency set, template fingerprint, worker image identity, execution
    flags), the resulting hashes, and whether each cache layer would hit —
    i.e. whether the next build executes this deck or replays stored output,
    and why.

    Read-only: opens the cache databases in read-only mode and creates no
    output directories. Use the same --cache-db-path/--jobs-db-path (and
    --output-dir) as your builds, or the lookups will miss spuriously.

    \b
    Examples:
        clm cache explain slides/.../slides_shared_ptr.cpp --spec cpp-einsteiger.xml
        clm cache explain slides_intro.py --spec course.xml -L de --format html
        clm cache explain slides_intro.py --spec course.xml --json
    """
    import asyncio

    from clm.core.course import Course
    from clm.core.course_files.notebook_file import NotebookFile
    from clm.core.course_paths import resolve_course_paths
    from clm.core.course_spec import CourseSpec, CourseSpecError
    from clm.infrastructure.messaging.notebook_classes import (
        CACHE_HASH_SCHEMA_VERSION,
    )

    cache_db: Path = ctx.obj["CACHE_DB_PATH"]
    jobs_db: Path = ctx.obj["JOBS_DB_PATH"]

    course_root, default_output = resolve_course_paths(spec_file.absolute(), data_dir)
    try:
        spec = CourseSpec.from_file(spec_file.absolute())
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse course spec: {e}") from None

    # Same fallback as `clm build`, but WITHOUT mkdir — this command is
    # read-only and must work against a never-built tree.
    effective_output = output_dir
    if effective_output is None and not spec.output_targets:
        effective_output = default_output

    course = Course.from_spec(
        spec,
        course_root,
        effective_output,
        output_languages=list(languages) or None,
        output_kinds=list(kinds) or None,
    )

    course_file = course.find_course_file(source_file.absolute())
    if course_file is None:
        raise click.ClickException(
            f"'{source_file}' is not part of the course described by "
            f"'{spec_file}'. Is the topic enabled in the spec?"
        )
    if not isinstance(course_file, NotebookFile):
        raise click.ClickException(
            f"'{source_file}' is a {type(course_file).__name__}, not a notebook "
            f"slide file — only notebook execution is cached with explained keys."
        )

    async def _payloads():
        operations = []
        for target in course.output_targets:
            op = await course_file.get_processing_operation(target.output_root, target=target)
            operations.extend(_unwrap_operations(op))
        if formats:
            operations = [op for op in operations if op.format in formats]
        # Across targets the same (kind, format, language) can repeat with
        # different output roots; keep all (the results_cache key differs).
        return [(op, await op.payload()) for op in operations]

    pairs = asyncio.run(_payloads())
    if not pairs:
        raise click.ClickException(
            "No output artifacts match the given language/kind/format filters."
        )

    # Components are payload-wide (same source text, siblings, fingerprints
    # for every artifact); take them from the first payload.
    first = pairs[0][1]
    siblings, excluded_cassette = _sibling_entries(first)
    components: dict[str, Any] = {
        "schema_version": CACHE_HASH_SCHEMA_VERSION,
        "data_chars": len(first.data),
        "data_sha256": hashlib.sha256(first.data.encode("utf-8")).hexdigest(),
        "prog_lang": first.prog_lang,
        "template_fingerprint": first.template_fingerprint,
        "worker_image_identity": first.worker_image_identity,
        "skip_evaluation": first.skip_evaluation,
        "skip_errors": first.skip_errors,
        "other_files": siblings,
        "excluded_cassette": excluded_cassette,
    }

    artifacts = [_collect_artifact(payload, op, cache_db, jobs_db) for op, payload in pairs]

    if as_json:
        click.echo(
            json.dumps(
                {
                    "source_file": str(source_file.absolute()),
                    "spec": str(spec_file.absolute()),
                    "cache_db": str(cache_db),
                    "cache_db_exists": cache_db.exists(),
                    "jobs_db": str(jobs_db),
                    "jobs_db_exists": jobs_db.exists(),
                    "components": components,
                    "artifacts": artifacts,
                },
                indent=2,
            )
        )
        return

    _print_human(source_file, cache_db, jobs_db, components, artifacts)


def _print_human(
    source_file: Path,
    cache_db: Path,
    jobs_db: Path,
    components: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    def short(digest: str) -> str:
        return digest[:12] + "…" if digest else "(empty)"

    click.echo(f"Cache explanation for {source_file}")
    click.echo("")
    click.echo(f"Key components (schema v{components['schema_version']})")
    click.echo(
        f"  data                  {components['data_chars']:,} chars, "
        f"sha256 {short(components['data_sha256'])}"
    )
    click.echo(f"  prog_lang             {components['prog_lang']}")
    click.echo(f"  template fingerprint  {short(components['template_fingerprint'])}")
    click.echo(f"  worker image          {components['worker_image_identity'] or '(unset)'}")
    click.echo(
        f"  skip_evaluation       {components['skip_evaluation']}    "
        f"skip_errors  {components['skip_errors']}"
    )
    hashed = [e for e in components["other_files"] if not e["excluded"]]
    click.echo(f"  other_files ({len(hashed)} hashed)")
    for entry in components["other_files"]:
        marker = "  excluded (cassette — documented exclusion)" if entry["excluded"] else ""
        click.echo(
            f"    {entry['name']}  {entry['size']:,} B  sha256 {short(entry['sha256'])}{marker}"
        )
    if not components["other_files"]:
        click.echo("    (none)")
    click.echo("")

    for db_label, db_path in (("cache db", cache_db), ("jobs db", jobs_db)):
        if not db_path.exists():
            click.echo(
                f"NOTE: {db_label} not found at {db_path} — every lookup below "
                f"misses. Pass the same --{db_label.replace(' ', '-')}-path as your builds."
            )
    click.echo("Artifacts")
    for artifact in artifacts:
        caches = artifact["caches"]

        def state(entry, _artifact=artifact):
            return f"HIT   stored {entry['stored_at']}" if entry else "MISS"

        click.echo(f"  {artifact['output_metadata']}")
        click.echo(f"    output file         {artifact['output_file']}")
        click.echo(f"    content_hash        {short(artifact['content_hash'])}")
        click.echo(f"    execution_hash      {short(artifact['execution_cache_hash'])}")
        click.echo(f"    processed_files     {state(caches['processed_files'])}")
        click.echo(f"    executed_notebooks  {state(caches['executed_notebooks'])}")
        click.echo(f"    results_cache       {state(caches['results_cache'])}")
        issues = artifact["stored_issues"]
        if issues["errors"] or issues["warnings"]:
            click.echo(
                f"    stored issues       {issues['errors']} error(s), "
                f"{issues['warnings']} warning(s) — replayed on a cache hit"
            )
        click.echo(f"    verdict             {artifact['verdict']}")
        click.echo("")
