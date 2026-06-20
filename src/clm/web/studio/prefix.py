"""Comment-prefix ↔ clean-markdown conversion for the Studio editor.

CLM stores markdown cell bodies **comment-prefixed** in the ``.py`` (``# text``,
and a bare ``#`` for a blank line). The Studio editor offers *clean* markdown
editing for the cells where that is byte-safe: de-prefix on read, canonically
re-prefix on write.

:func:`deprefix` and :func:`reprefix` are **exact inverses for canonical
content**, so a cell edited back to the same text round-trips to identical bytes.
A *non-canonical* cell (one that does **not** round-trip — e.g. a ``"# "`` line
with a trailing space, or a line missing the prefix) is detected by
:func:`round_trips` and kept on the raw edit path instead, so the byte-exact
write guarantee is never weakened. ``token`` is the deck's line-comment token
(``"#"`` python/rust, ``"//"`` cpp/csharp/java/typescript).
"""

from __future__ import annotations


def deprefix(body: str, token: str) -> str:
    """Strip the line-comment ``token`` (plus one following space) from each line."""
    out: list[str] = []
    for line in body.split("\n"):
        if line == token:
            out.append("")
        elif line.startswith(token + " "):
            out.append(line[len(token) + 1 :])
        else:
            out.append(line)  # non-canonical; round_trips() will reject it
    return "\n".join(out)


def reprefix(body: str, token: str) -> str:
    """Re-apply the canonical prefix: ``token`` for a blank line, ``token + " "`` else."""
    return "\n".join(token if line == "" else f"{token} {line}" for line in body.split("\n"))


def round_trips(body: str, token: str) -> bool:
    """Whether ``body`` survives ``deprefix`` → ``reprefix`` unchanged (canonical)."""
    return reprefix(deprefix(body, token), token) == body
