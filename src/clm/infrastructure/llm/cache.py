"""SQLite-based cache for LLM summaries."""

import functools
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Filename of the shared LLM SQLite cache (summaries, titles, translations,
# sync watermarks, …) inside the resolved cache directory. The CLI commands
# under ``clm slides`` each keep a local copy of this literal; this is the
# canonical home.
CACHE_DB_NAME = "clm-llm.sqlite"


class SummaryCache:
    """Cache LLM summaries keyed by (content_hash, audience, model, language, style)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self):
        """Create or migrate the summaries table."""
        cursor = self._conn.execute("PRAGMA table_info(summaries)")
        columns = {row[1] for row in cursor.fetchall()}

        if not columns:
            # Fresh database
            self._create_current_table()
        elif "language" not in columns:
            # Very old table without language — rebuild with both language and style
            logger.info("Migrating summary cache to include language and style columns")
            self._conn.execute("ALTER TABLE summaries RENAME TO summaries_old")
            self._create_current_table()
            self._conn.execute(
                """INSERT OR IGNORE INTO summaries
                   (content_hash, audience, model, language, style, summary, created_at)
                   SELECT content_hash, audience, model, 'en', 'prose', summary, created_at
                   FROM summaries_old"""
            )
            self._conn.execute("DROP TABLE summaries_old")
            self._conn.commit()
        elif "style" not in columns:
            # Has language but no style — add style column
            logger.info("Migrating summary cache to include style column")
            self._conn.execute("ALTER TABLE summaries RENAME TO summaries_old")
            self._create_current_table()
            self._conn.execute(
                """INSERT OR IGNORE INTO summaries
                   (content_hash, audience, model, language, style, summary, created_at)
                   SELECT content_hash, audience, model, language, 'prose', summary, created_at
                   FROM summaries_old"""
            )
            self._conn.execute("DROP TABLE summaries_old")
            self._conn.commit()

    def _create_current_table(self):
        self._conn.execute(
            """CREATE TABLE summaries (
                content_hash TEXT NOT NULL,
                audience TEXT NOT NULL,
                model TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                style TEXT NOT NULL DEFAULT 'prose',
                summary TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (content_hash, audience, model, language, style)
            )"""
        )
        self._conn.commit()

    def get(
        self,
        content_hash: str,
        audience: str,
        model: str,
        language: str = "en",
        style: str = "prose",
    ) -> str | None:
        row = self._conn.execute(
            "SELECT summary FROM summaries "
            "WHERE content_hash=? AND audience=? AND model=? AND language=? AND style=?",
            (content_hash, audience, model, language, style),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        content_hash: str,
        audience: str,
        model: str,
        summary: str,
        language: str = "en",
        style: str = "prose",
    ):
        self._conn.execute(
            """INSERT OR REPLACE INTO summaries
               (content_hash, audience, model, language, style, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (content_hash, audience, model, language, style, summary),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()


class TitleSuggestionCache:
    """Cache LLM-suggested slide titles keyed by ``(content_hash, prompt_version, lang)``.

    Used by ``clm slides assign-ids --llm-suggest`` to avoid re-querying
    the local LLM for cells whose content has not changed. Shares the
    same SQLite file as :class:`SummaryCache` (the consuming repo's
    ``clm-llm.sqlite`` cache; see §2.5 of the slide-format-redesign
    handover) but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(title_suggestions)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE title_suggestions (
                    content_hash    TEXT NOT NULL,
                    prompt_version  TEXT NOT NULL,
                    lang            TEXT NOT NULL,
                    suggested_title TEXT NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, prompt_version, lang)
                )"""
            )
            self._conn.commit()

    def get(self, content_hash: str, prompt_version: str, lang: str = "en") -> str | None:
        row = self._conn.execute(
            "SELECT suggested_title FROM title_suggestions "
            "WHERE content_hash=? AND prompt_version=? AND lang=?",
            (content_hash, prompt_version, lang),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        content_hash: str,
        prompt_version: str,
        suggested_title: str,
        lang: str = "en",
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO title_suggestions
               (content_hash, prompt_version, lang, suggested_title)
               VALUES (?, ?, ?, ?)""",
            (content_hash, prompt_version, lang, suggested_title),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM title_suggestions WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()


class TranslationCache:
    """Cache translated cell bodies for ``clm slides translate`` (Issue #232).

    Keyed by ``(content_hash, prompt_version, source_lang, target_lang, role)``:
    the source body's hash plus everything that changes the *output* — the
    translator's prompt version (model-folded by the caller, so two models never
    share an entry), the direction, and the role (markdown vs the
    identifier-preserving code prompt). Bootstrapping a whole deck is the same
    per-cell translation a later sync would do, so a shared cache makes re-runs
    and tests cheap. Only **successful** translations are stored.

    Shares the consuming repo's ``clm-llm.sqlite`` cache file with the other LLM
    caches but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(translations)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE translations (
                    content_hash   TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    source_lang    TEXT NOT NULL,
                    target_lang    TEXT NOT NULL,
                    role           TEXT NOT NULL,
                    translation    TEXT NOT NULL,
                    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, prompt_version, source_lang, target_lang, role)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        content_hash: str,
        prompt_version: str,
        source_lang: str,
        target_lang: str,
        role: str,
    ) -> str | None:
        row = self._conn.execute(
            "SELECT translation FROM translations WHERE content_hash=? AND prompt_version=? "
            "AND source_lang=? AND target_lang=? AND role=?",
            (content_hash, prompt_version, source_lang, target_lang, role),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        content_hash: str,
        prompt_version: str,
        source_lang: str,
        target_lang: str,
        role: str,
        translation: str,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO translations
               (content_hash, prompt_version, source_lang, target_lang, role, translation)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (content_hash, prompt_version, source_lang, target_lang, role, translation),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM translations WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()


class CoverageCache:
    """Cache LLM voiceover-coverage verdicts.

    Keyed by ``(slide_hash, voiceover_hash, prompt_version, lang)`` per
    §2.5 of the slide-format-redesign handover. The verdict is a short
    string (``"covered"`` or ``"gaps"``) and ``gap_details`` is a JSON
    blob produced by the judge listing the per-bullet results.

    Shares the same SQLite file as :class:`SummaryCache` and
    :class:`TitleSuggestionCache` (the consuming repo's
    ``clm-llm.sqlite``) but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(coverage)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE coverage (
                    slide_hash      TEXT NOT NULL,
                    voiceover_hash  TEXT NOT NULL,
                    prompt_version  TEXT NOT NULL,
                    lang            TEXT NOT NULL,
                    verdict         TEXT NOT NULL,
                    gap_details     TEXT,
                    checked_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (slide_hash, voiceover_hash, prompt_version, lang)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        slide_hash: str,
        voiceover_hash: str,
        prompt_version: str,
        lang: str,
    ) -> tuple[str, str | None] | None:
        """Return ``(verdict, gap_details_json)`` or ``None`` on a miss."""
        row = self._conn.execute(
            "SELECT verdict, gap_details FROM coverage "
            "WHERE slide_hash=? AND voiceover_hash=? AND prompt_version=? AND lang=?",
            (slide_hash, voiceover_hash, prompt_version, lang),
        ).fetchone()
        if row is None:
            return None
        return (row[0], row[1])

    def put(
        self,
        slide_hash: str,
        voiceover_hash: str,
        prompt_version: str,
        lang: str,
        verdict: str,
        gap_details: str | None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO coverage
               (slide_hash, voiceover_hash, prompt_version, lang, verdict, gap_details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (slide_hash, voiceover_hash, prompt_version, lang, verdict, gap_details),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM coverage WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def iter_entries(self) -> list[tuple[str, str, str, str, str, str | None, str]]:
        """Return every cached entry for ``coverage --dump``.

        Tuples are ``(slide_hash, voiceover_hash, prompt_version, lang,
        verdict, gap_details, checked_at)`` ordered by check time so the
        most recent verdicts surface first.
        """
        rows = self._conn.execute(
            "SELECT slide_hash, voiceover_hash, prompt_version, lang, "
            "verdict, gap_details, checked_at "
            "FROM coverage ORDER BY checked_at DESC, slide_hash"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class CacheDirResolution:
    """Where the LLM cache directory resolves to, and *why*.

    The provenance fields exist so a read-only diagnostic (``clm config
    locate``) can explain the resolution without re-deriving it — and so the
    git-worktree anchoring is observable. ``path`` is NOT created here; call
    :func:`resolve_cache_dir` (or ``_ensure_dir``) when you need the directory
    to exist.
    """

    path: Path
    source: str  # "cli" | "env" | "pyproject" | "default"
    configured_value: str | None = None  # raw [tool.clm] cache_dir value, if used
    pyproject_path: Path | None = None
    relative_anchor: Path | None = None  # dir a relative configured_value was joined to
    main_worktree_root: Path | None = None  # set iff resolved from a LINKED git worktree


def describe_cache_dir(
    *,
    cli_override: Path | None = None,
    repo_root: Path | None = None,
    start: Path | None = None,
) -> CacheDirResolution:
    """Resolve the LLM cache directory and report its provenance (pure).

    Lookup order:

    1. ``cli_override`` (the ``--cache-dir`` flag value)
    2. ``CLM_CACHE_DIR`` environment variable
    3. ``tool.clm.cache_dir`` in ``<repo_root>/pyproject.toml``
    4. ``<repo_root>/.clm-cache/`` (default, gitignored)

    ``repo_root`` defaults to the **discovered project root** —
    :func:`clm.infrastructure.utils.path_utils.find_project_root` walks up from
    ``start`` (default: the current working directory) to the nearest
    ``pyproject.toml`` / ``.clm/config.toml`` / ``.git`` (like ``git`` / ``uv`` /
    ``ruff``), so the cache resolves to the same place no matter which
    subdirectory the command was invoked from (issue #477). Without the
    walk-up, running from a topic subdir treated the subdir as the root: it
    missed ``[tool.clm] cache_dir`` and created a stray ``<subdir>/.clm-cache``.
    Pass ``start`` when the anchor is a *path argument* rather than cwd (the
    voiceover cache walks up from the deck directory, issue #568); unlike an
    explicit ``repo_root``, a ``start`` anchor keeps the git-worktree
    re-anchoring below active. This function has **no side effects** — it does
    not create the directory.

    Git-worktree anchoring: a *relative* ``[tool.clm] cache_dir`` (e.g.
    ``../shared-cache``) is normally joined to ``repo_root``. But in a git
    **worktree**, ``repo_root`` is the per-worktree checkout — so the relative
    value would resolve *under* the worktree instead of beside the main
    checkout, silently giving each worktree its own cache (the cause of
    sync watermarks "disappearing" in a worktree). When ``repo_root`` is not
    given explicitly (the real-CLI path, resolving from cwd) and cwd is inside a
    linked worktree, the relative value is anchored to the **main worktree
    root** instead, so every worktree shares the one cache. Passing an explicit
    ``repo_root`` opts out of this detection and anchors to that root verbatim.
    """
    import os

    from clm.infrastructure.utils.path_utils import find_project_root

    if cli_override is not None:
        return CacheDirResolution(path=Path(cli_override), source="cli")

    env = os.environ.get("CLM_CACHE_DIR")
    if env:
        return CacheDirResolution(path=Path(env), source="env")

    # Discover the project root by walking up (issue #477); an explicit
    # ``repo_root`` opts out and anchors to that root verbatim (library callers /
    # tests). The worktree re-anchoring of a relative value below then runs with
    # the correct root (the worktree checkout root, not a subdir).
    root = repo_root or find_project_root(start)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        configured = _read_pyproject_cache_dir(pyproject)
        if configured:
            path = Path(configured)
            if path.is_absolute():
                return CacheDirResolution(
                    path=path,
                    source="pyproject",
                    configured_value=configured,
                    pyproject_path=pyproject,
                )
            # Anchor a relative value to the main worktree root when resolving
            # from cwd inside a linked worktree (see docstring).
            main_root = _main_worktree_root(root) if repo_root is None else None
            anchor = main_root or root
            return CacheDirResolution(
                path=anchor / path,
                source="pyproject",
                configured_value=configured,
                pyproject_path=pyproject,
                relative_anchor=anchor,
                main_worktree_root=main_root,
            )

    return CacheDirResolution(path=root / ".clm-cache", source="default")


def resolve_cache_dir(
    *,
    cli_override: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Resolve the LLM cache directory and ensure it exists.

    Thin wrapper over :func:`describe_cache_dir` (which holds the resolution
    logic, including git-worktree anchoring of a relative ``[tool.clm]
    cache_dir``). The returned path is created if it does not exist.
    """
    return _ensure_dir(describe_cache_dir(cli_override=cli_override, repo_root=repo_root).path)


def _main_worktree_root(start: Path) -> Path | None:
    """The main worktree root if ``start`` is inside a LINKED git worktree.

    Returns ``None`` for the main worktree, outside any repo, or when git is
    unavailable — callers then fall back to ``start``. The main worktree's
    ``--git-common-dir`` is the repo's own ``.git`` (so the parent equals
    ``start``'s root and there is nothing to re-anchor); a linked worktree's
    common dir points at the *main* checkout's ``.git``, whose parent is the
    shared root we want.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    out = completed.stdout.strip()
    if not out:
        return None
    common = Path(out)
    if not common.is_absolute():
        # In the MAIN worktree, --git-common-dir is the relative ".git"; its
        # parent is `start`'s root, so there is no separate main root to use.
        return None
    common = common.resolve()
    if common.name != ".git":
        return None
    return common.parent


def _git_show_toplevel(start: Path) -> Path | None:
    """The working-tree root of the (possibly linked) worktree containing ``start``.

    ``git rev-parse --show-toplevel`` run with ``cwd=start``. Returns ``None`` when
    git is unavailable or ``start`` is outside a repo. Used together with
    :func:`_main_worktree_root` to remap a worktree path to its main-checkout twin.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    out = completed.stdout.strip()
    if not out:
        return None
    return Path(out).resolve()


@functools.cache
def _worktree_remap_for_dir(directory: str) -> tuple[str, str] | None:
    """``(worktree_toplevel, main_root)`` if ``directory`` is in a LINKED worktree.

    ``None`` for the main worktree, outside a repo, or when git is unavailable.
    Memoized: the answer is constant per worktree, so keying a whole batch of
    pairs makes at most one git invocation per unique directory.
    """
    start = Path(directory)
    main_root = _main_worktree_root(start)
    if main_root is None:
        return None
    top = _git_show_toplevel(start)
    if top is None:
        return None
    return (str(top), str(main_root))


def to_main_worktree_path(p: Path) -> Path:
    """Remap a path under a linked git worktree to its main-checkout equivalent.

    The sync watermark is keyed by the absolute ``(de_path, en_path)`` strings,
    but :meth:`Path.resolve` from a linked worktree yields the *worktree* path —
    which never matches the rows recorded from the main checkout, so every pair
    misses its watermark and silently cold-starts off git HEAD (issue #435).
    Canonicalizing the **key** to the main-checkout path lets the worktree and the
    main checkout share both the cache file (#374) and the keys inside it, and
    keeps writes on the single canonical key (no orphaned worktree-path rows).

    Returns ``p`` unchanged when it is not under a linked worktree, is outside a
    repo, or git is unavailable — so the main checkout and non-git callers are
    unaffected, and the function is idempotent (a main-checkout path remaps to
    itself). Only the watermark **key** is canonicalized; file reads and the
    content-keyed, worktree-portable sync ledger keep the real on-disk path.
    """
    resolved = p.resolve()
    directory = resolved if resolved.is_dir() else resolved.parent
    remap = _worktree_remap_for_dir(str(directory))
    if remap is None:
        return p
    wt_top, main_root = Path(remap[0]), Path(remap[1])
    try:
        rel = resolved.relative_to(wt_top)
    except ValueError:
        return p
    return main_root / rel


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_pyproject_cache_dir(pyproject: Path) -> str | None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python <3.11 not supported
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tool = data.get("tool", {})
    clm = tool.get("clm", {})
    value = clm.get("cache_dir")
    if isinstance(value, str) and value:
        return value
    return None
