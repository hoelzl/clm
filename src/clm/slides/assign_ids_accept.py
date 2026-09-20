"""``clm slides assign-ids accept`` — land agent-proposed titles (#963).

The framing already exists: ``clm slides assign-ids --report-refusals
--context --json`` emits the refusal worklist (each refused cell's
``file``/``line``, reason, and — with ``--context`` — its marker, body,
and preceding anchors). This module is the missing accept side: it takes
an answer document with one proposed title per refused cell, echoes each
cell's body verbatim as the freshness token, runs the same slug-quality
checks the engine applies to LLM suggestions, and stamps the derived
``slide_id`` onto the cell header — atomically per file.

The contract is the shared kit's (``clm info agent-tasks``):

* **Freshness.** Each answer row echoes the cell body it was framed
  from. Accept re-reads the live file, locates the cell at the framed
  line, and refuses the answer wholesale when the body changed, the line
  moved onto a different cell, or the cell already carries a
  ``slide_id`` (someone fixed it in between) — never merged.
* **Slug quality.** ``slugify(title)`` (the engine's own slugifier) must
  produce a non-empty base; collisions with ids already present in the
  target file resolve with the engine's own suffix rule.
* **Pair consistency.** A split ``.de``/``.en`` family must receive the
  same slug set on both halves — the refusal worklist reports both cells
  of a refused pair ("both cells in the pair need a manual id"), and
  divergent one-sided ids are exactly the corruption
  ``clm slides assign-ids`` exists to prevent. A family whose halves
  would receive different slug sets is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clm.core.slide_text.pairing import derive_split_twin, split_lang_tag
from clm.core.slide_text.raw_cells import split_cells
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.slides.agent_task import VALIDATORS, AnswerRejected, envelope
from clm.slides.assign_ids import header_with_slide_id
from clm.slides.slug import MAX_SLUG_LENGTH, resolve_collision, slugify, strip_preserve_marker

__all__ = [
    "ANSWER_SCHEMA",
    "ANSWER_VALIDATOR",
    "TASK_SCHEMA",
    "TitleAnswer",
    "apply_answers",
    "parse_answer",
]

#: The wire schema of the answer document.
TASK_SCHEMA = 1

#: The validator label the answer names; registered in the shared kit's
#: registry at import of this module.
ANSWER_VALIDATOR = "assign-ids-titles"

_TITLE_GUIDANCE = (
    "a single English title of 3-7 words, written in title case, no quotes or trailing punctuation"
)

#: The answer contract: one {file, line, title, body} row per refused
#: cell. ``body`` is the freshness token — the cell body verbatim as the
#: refusal worklist's ``context.body`` carried it.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "answers"],
    "properties": {
        "schema": {"const": TASK_SCHEMA},
        "answers": {
            "type": "array",
            "description": "one row per refused cell from the `assign-ids "
            "--report-refusals --context --json` worklist",
            "items": {
                "type": "object",
                "required": ["file", "line", "title", "body"],
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer", "minimum": 1},
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "description": f"{_TITLE_GUIDANCE}",
                    },
                    "body": {
                        "type": "string",
                        "description": "echoed verbatim from the worklist's "
                        "context.body — accept re-checks it against the live cell",
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class TitleRow:
    """One parsed answer row (shape-validated; freshness pending)."""

    file: str
    line: int
    title: str
    body: str


@dataclass(frozen=True)
class TitleAnswer:
    """A parsed answer document (shape-validated; freshness pending)."""

    rows: list[TitleRow]


def parse_answer(payload: Any) -> TitleAnswer:
    """The ``assign-ids-titles`` validator: shape-check the answer document."""
    if not isinstance(payload, dict):
        raise AnswerRejected("the answer must be a JSON object (see the answer_schema)")
    if payload.get("schema") != TASK_SCHEMA:
        raise AnswerRejected(f"answer schema must be {TASK_SCHEMA}, got {payload.get('schema')!r}")
    raw_rows = payload.get("answers")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise AnswerRejected("'answers' must be a non-empty list of {file, line, title, body} rows")
    rows: list[TitleRow] = []
    seen: set[tuple[str, int]] = set()
    for i, entry in enumerate(raw_rows):
        if not isinstance(entry, dict) or set(entry) != {"file", "line", "title", "body"}:
            raise AnswerRejected(
                f"answers[{i}] must be an object with exactly file, line, title, body"
            )
        file_str, line = entry["file"], entry["line"]
        title, body = entry["title"], entry["body"]
        if not isinstance(file_str, str) or not file_str:
            raise AnswerRejected(f"answers[{i}].file must be a non-empty string")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise AnswerRejected(f"answers[{i}].line must be a positive integer")
        if (file_str, line) in seen:
            raise AnswerRejected(f"answers: duplicate row for {file_str}:{line}")
        seen.add((file_str, line))
        if not isinstance(title, str) or not title.strip():
            raise AnswerRejected(
                f"answers[{i}].title must be a non-empty string ({_TITLE_GUIDANCE})"
            )
        if not isinstance(body, str):
            raise AnswerRejected(f"answers[{i}].body must be the cell body echoed verbatim")
        rows.append(TitleRow(file=file_str, line=line, title=title, body=body))
    return TitleAnswer(rows=rows)


VALIDATORS.register(ANSWER_VALIDATOR, parse_answer)


# ---------------------------------------------------------------------------
# Applying the answer
# ---------------------------------------------------------------------------


def _strip_trailing_blank(body: str) -> str:
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _family_key(path: Path) -> tuple[str, ...]:
    """Identity of a split family: the sorted (de, en) path strings."""
    twin = derive_split_twin(path)
    if twin is None:
        return (str(path.resolve()),)
    return tuple(sorted({str(path.resolve()), str(twin.resolve())}))


@dataclass(frozen=True)
class AcceptOutcome:
    """What the accept path did: per-file writes and the stamped ids."""

    stamped: list[dict[str, Any]]  # {file, line, slide_id, title}
    written: list[str]

    def to_payload(self) -> dict[str, Any]:
        return envelope(
            TASK_SCHEMA,
            tool="assign-ids",
            verb="accept",
            body={
                "applied": True,
                "stamped": self.stamped,
                "written": self.written,
            },
        )


def apply_answers(answer: TitleAnswer, *, dry_run: bool = False) -> AcceptOutcome:
    """Validate freshness + slugs against the live files; stamp the ids.

    Raises :class:`AnswerRejected` on the first violation; nothing is
    written. Files are written atomically, one write per touched file.
    """
    from clm.infrastructure.utils.path_utils import atomic_write_all

    by_file: dict[Path, list[TitleRow]] = {}
    seen_cells: set[tuple[Path, int]] = set()
    for row in answer.rows:
        path = Path(row.file)
        if not path.exists():
            raise AnswerRejected(f"{row.file}:{row.line}: the file does not exist")
        resolved = path.resolve()
        # Normalize the spelling: rows naming the same cell through different
        # path spellings (abs + rel) are duplicates, not two writes.
        if (resolved, row.line) in seen_cells:
            raise AnswerRejected(
                f"{row.file}:{row.line}: duplicate row (the same cell is named "
                "twice through different path spellings)"
            )
        seen_cells.add((resolved, row.line))
        by_file.setdefault(resolved, []).append(row)

    # Pass 1 — validate every row against the live files; collect slugs.
    file_plans: dict[Path, tuple[list[str], dict[int, tuple[TitleRow, str]]]] = {}
    for path, rows in by_file.items():
        text = path.read_text(encoding="utf-8")
        # The deck's own comment token drives boundary detection — a `//`
        # deck parses to zero cells under the "#" default.
        comment_token = comment_token_for_path(path)
        _, cells = split_cells(text, comment_token)
        by_line = {cell.line_number: cell for cell in cells}
        used: set[str] = set()
        for cell in cells:
            if cell.metadata.slide_id:
                used.add(strip_preserve_marker(cell.metadata.slide_id))
        # A split half must also avoid its twin's existing ids — slide_id
        # is the cross-language join key (the #162 invariant).
        twin = derive_split_twin(path)
        if twin is not None:
            for cell in split_cells(twin.read_text(encoding="utf-8"), comment_token_for_path(twin))[
                1
            ]:
                if cell.metadata.slide_id:
                    used.add(strip_preserve_marker(cell.metadata.slide_id))
        stamped: dict[int, tuple[TitleRow, str]] = {}
        for row in rows:
            target = by_line.get(row.line)
            if target is None:
                raise AnswerRejected(
                    f"{row.file}:{row.line}: no cell starts at this line any more "
                    "— the file changed since the worklist was framed"
                )
            if target.metadata.slide_id:
                raise AnswerRejected(
                    f"{row.file}:{row.line}: the cell already carries "
                    f'slide_id="{target.metadata.slide_id}" — it was fixed since the '
                    "worklist was framed; re-run `assign-ids --report-refusals --json`"
                )
            if _strip_trailing_blank(target.body) != row.body:
                raise AnswerRejected(
                    f"{row.file}:{row.line}: the cell body changed since the worklist "
                    "was framed (body echo mismatch) — re-run "
                    "`assign-ids --report-refusals --context --json` and re-answer"
                )
            base = slugify(row.title, max_length=MAX_SLUG_LENGTH)
            if not base:
                raise AnswerRejected(
                    f"{row.file}:{row.line}: the title {row.title!r} yields an empty "
                    "slug — pick a title with slugifiable words"
                )
            slug = resolve_collision(base, used)
            used.add(slug)
            stamped[row.line] = (row, slug)
        file_plans[path] = (text.split("\n"), stamped)

    # Pass 2 — pair consistency: within each touched split family, the two
    # halves' refused cells pair positionally (split halves mirror cell
    # order), so the slug sequence stamped on the de half must equal the
    # en half's — same set AND same order (a swap would silently break the
    # cross-language join, #162).
    family_slugs: dict[tuple[str, ...], dict[str, list[str]]] = {}
    for path, (_lines, stamped) in file_plans.items():
        twin = derive_split_twin(path)
        if twin is None:
            continue
        side = split_lang_tag(path)
        assert side is not None  # derive_split_twin only pairs tagged halves
        slugs = [slug for _line, (_row, slug) in sorted(stamped.items())]
        family_slugs.setdefault(_family_key(path), {"de": [], "en": []})[side] = slugs
    for family, sides in family_slugs.items():
        if sides["de"] != sides["en"]:
            family_name = Path(family[0]).name
            raise AnswerRejected(
                f"split pair {family_name}: the two halves would receive different "
                f"slide_id sequences (de={sides['de']}, en={sides['en']}) — the "
                "refused cells of a pair must receive the SAME title. Frame the "
                "worklist over the deck DIRECTORY so both halves' cells appear, "
                "and answer both cells of each refused pair identically"
            )

    stamped_out: list[dict[str, Any]] = []
    writes: list[tuple[Path, str]] = []
    for path, (lines, stamped) in file_plans.items():
        for line, (row, slug) in stamped.items():
            lines[line - 1] = header_with_slide_id(lines[line - 1], slug)
            stamped_out.append(
                {"file": row.file, "line": row.line, "slide_id": slug, "title": row.title}
            )
        writes.append((path, "\n".join(lines)))

    if not dry_run and writes:
        atomic_write_all(writes)
    return AcceptOutcome(
        stamped=stamped_out,
        written=[str(p) for p, _t in writes] if not dry_run else [],
    )
