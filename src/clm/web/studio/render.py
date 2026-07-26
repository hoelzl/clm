"""Tier-2 cell render — design §3.8.

Expands the Jinja in an ``is_j2`` cell (header macros, ``{{ … }}`` expressions)
server-side using the **same** bundled ``macros.j2`` and line-statement prefix as
the build pipeline, but **without a kernel** — so the phone sees an expanded
header instead of raw ``{{ header_de("…") }}``. Plain (non-j2) cells need no
round-trip; the client renders their markdown directly (tier 1).

**"No-execution" means no kernel, not no code.** This tier used to be described
as the no-execution one, which was wrong in the way that matters: Jinja
rendering *is* execution, and on a plain ``Environment`` a template can walk
``__class__``/``__mro__`` out of the template namespace and reach anything the
process can. The 2026-07-24 adversarial review (S7) reproduced that against
this function — the body being rendered is a **request body**, not a file from
the course repo, so a client with the Studio token could run arbitrary Python
in the server. It now renders in an
:class:`~jinja2.sandbox.ImmutableSandboxedEnvironment`, which blocks the
attribute traversal those escapes depend on.

Two things the sandbox does *not* give you, and what this module does about
each. Both are stated precisely because the first attempt at them overclaimed.

**Size.** A sandbox bounds attribute access and nothing else, so
``{{ "A" * 200000000 }}`` was a one-expression memory bomb. Now bounded:
``*`` and ``+`` are intercepted before they allocate (:data:`MAX_REPEAT`,
:data:`MAX_OUTPUT_CHARS`), and :meth:`concat` accumulates the rendered output
with a running check — which matters because a post-render ``len()`` bounds
only what is *returned*: a 500-iteration loop emitting a legal 100 000
characters each peaked at 100 MB before such a check could run. It is now
1.1 MB. **Not** bounded: ``~``. Jinja compiles it to a ``Concat`` node, which
is not a ``BinExpr`` (so ``intercepted_binops`` cannot see it) and which emits
a call to ``str_join`` resolved from the compiled template's own namespace
(so ``environment.concat`` does not see it either); the only hook left is a
process-wide monkeypatch of ``jinja2.runtime``, which would also change how
the *build* renders decks. So
``{% set s = "A" * 100000 %}{% set s = s ~ s %}…`` still reaches ~1.2 GB, and
nested ``{% for %}`` over ``range()`` still burns CPU without allocating.
**A client holding the Studio token can therefore still exhaust this
process.** That is accepted rather than fixed: the token is the trust boundary
(decision D4), the same client can already rewrite any deck, and closing it
properly means rendering in a subprocess under an rlimit — issue #698.

**The loader.** The sandbox constrains the *template*, not the
``FileSystemLoader`` under it, so ``{% include %}`` still reads files. Jinja
refuses traversal out of the loader's root (``..``, absolute paths, and the
backslash forms), and ``_resolve_deck_id`` pins the deck under the slides dir
— but the root is the deck's directory *and its whole subtree*, dotfiles
included. Anything parked beside a deck (a ``.env``, a sidecar subdir) is
readable by a token holder.

And the sandbox boundary is not unlimited in the usual sense either: a sandbox
escape is a sandbox escape, and the token remains the access gate.

This is best-effort preview: any Jinja error (a macro that needs build-only
context, a missing include, a sandbox refusal) is caught and returned as
``ok=False`` with the body unchanged, so the preview degrades to tier-1 rather
than failing. It also runs with a **lenient** ``Undefined`` (not the build's
``StrictUndefined``) so a missing course variable renders empty instead of
raising — preview, not parity.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Identity globals the build injects from the course; a header macro only needs
#: them to be *defined* for a preview, so placeholders are fine.
_PREVIEW_AUTHOR = "Preview"
_PREVIEW_ORG = ""

#: Largest sequence repetition a preview template may ask for. Blocking
#: attribute traversal does nothing about ``{{ "A" * 200000000 }}``, which
#: allocates 200 MB in about a second — measured — and then again for the JSON
#: response. The body is client-supplied, so this is the cheapest denial of
#: service in the app and needs its own bound.
MAX_REPEAT = 100_000

#: Largest render output returned. Backstop for any other way of growing the
#: output (nested loops, a macro over a long list) that the repeat cap misses.
MAX_OUTPUT_CHARS = 1_000_000


def _preview_environment_class():
    """Return the sandboxed Environment subclass used for previews.

    Built lazily so this module keeps importing without jinja2 installed.

    ``ImmutableSandboxedEnvironment`` rather than the mutable one — Jinja's own
    recommendation for untrusted template text, and nothing here needs to call
    a mutating method. On top of that it adds the size bounds the sandbox does
    not provide: a sandbox constrains *attribute access* and nothing else, so
    without these a single expression is a memory bomb. See the module
    docstring for what remains unbounded and why.
    """
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    from jinja2.sandbox import SecurityError as _SecurityError

    class _PreviewEnvironment(ImmutableSandboxedEnvironment):
        # `*` and `+` go through the sandbox's binop interception. `~` does
        # NOT, and cannot: Jinja compiles it to a `Concat` node, which is not a
        # `BinExpr` (invisible to `intercepted_binops`) and which calls
        # `str_join` from the compiled template's own namespace (invisible to
        # `environment.concat` too). That gap is documented in the module
        # docstring and pinned by a test; do not add "~" here expecting it to
        # work — it is silently inert.
        intercepted_binops = frozenset({"*", "+"})

        def call_binop(self, context, operator, left, right):  # noqa: ANN001
            if operator == "*":
                _refuse_oversized_repeat(left, right)
                _refuse_oversized_repeat(right, left)
            else:
                _refuse_oversized_concat(left, right)
            return super().call_binop(context, operator, left, right)

        def concat(self, iterable) -> str:  # noqa: ANN001
            """Join with a running bound, refusing before the total is built.

            Jinja calls this once per render, over the root render function's
            generator, so it is the one place that sees output growth
            incrementally. Checking ``len()`` after ``"".join(...)`` — which is
            what this replaced — bounds only what is *returned*: a
            500-iteration loop each emitting a legal 100 000 characters peaked
            at 100 MB before the check ran, and 1.1 MB after this.

            (It does **not** see ``~``; that goes to ``str_join``. See above.)
            """
            parts: list[str] = []
            total = 0
            for part in iterable:
                total += len(part)
                if total > MAX_OUTPUT_CHARS:
                    raise _SecurityError(f"preview output exceeded {MAX_OUTPUT_CHARS} characters")
                parts.append(part)
            return "".join(parts)

    def _size_of(value) -> int | None:  # noqa: ANN001
        """Length of a sequence whose growth is worth bounding, else ``None``.

        Numbers and ``None`` return ``None``: they cannot grow, and treating
        them as sized would make ordinary arithmetic pay for this check.
        """
        if isinstance(value, str | bytes | list | tuple):
            return len(value)
        return None

    def _refuse_oversized_repeat(sequence, count) -> None:  # noqa: ANN001
        """Raise before ``sequence * count`` allocates, not after."""
        size = _size_of(sequence)
        if size is None:
            return
        if isinstance(count, bool) or not isinstance(count, int):
            return
        if count > MAX_REPEAT or size * max(count, 0) > MAX_OUTPUT_CHARS:
            raise _SecurityError(
                f"refusing to repeat a {size}-item sequence {count} times "
                f"(preview limit: {MAX_REPEAT} repeats / {MAX_OUTPUT_CHARS} characters)"
            )

    def _refuse_oversized_concat(left, right) -> None:  # noqa: ANN001
        """Raise before ``left ~ right`` / ``left + right`` allocates."""
        left_size, right_size = _size_of(left), _size_of(right)
        if left_size is None and right_size is None:
            return  # numeric addition — nothing to bound
        total = (left_size or 0) + (right_size or 0)
        if total > MAX_OUTPUT_CHARS:
            raise _SecurityError(
                f"refusing to build a {total}-item value "
                f"(preview limit: {MAX_OUTPUT_CHARS} characters)"
            )

    return _PreviewEnvironment


def render_j2_cell(deck_path: Path, body: str, lang: str | None) -> tuple[bool, str | None, str]:
    """Expand the Jinja in ``body`` for ``deck_path``. Returns ``(ok, error, text)``.

    ``ok`` True → ``text`` is the expanded body; False → ``text`` is ``body``
    unchanged and ``error`` explains why (the client falls back to tier-1). Never
    raises — a preview must not crash the request.
    """
    try:
        from jinja2 import ChoiceLoader, FileSystemLoader, PackageLoader

        from clm.infrastructure.utils.path_utils import path_to_prog_lang
        from clm.workers.notebook.utils.prog_lang_utils import jinja_prefix_for
    except Exception as exc:  # noqa: BLE001 - missing optional dep → tier-1 fallback
        return False, f"render unavailable: {exc}", body

    try:
        prog_lang = path_to_prog_lang(deck_path)
    except (KeyError, ValueError):
        prog_lang = "python"

    try:
        loaders: list = [PackageLoader("clm.workers.notebook", f"templates_{prog_lang}")]
        deck_dir = deck_path.parent
        if deck_dir.exists():
            # Lets the cell `{% include %}` a sibling file shown in a slide.
            loaders.append(FileSystemLoader(str(deck_dir)))
        # Sandboxed, not plain: `body` is a request body. See the module
        # docstring — a plain Environment lets a template reach `__class__` and
        # walk out of the template namespace.
        env = _preview_environment_class()(
            loader=ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0],
            autoescape=False,
            line_statement_prefix=jinja_prefix_for(prog_lang),
            keep_trailing_newline=True,
        )
        template = env.from_string(
            body,
            globals={
                "is_notebook": False,
                "is_html": True,
                "lang": lang or "de",
                "author": _PREVIEW_AUTHOR,
                "organization": _PREVIEW_ORG,
            },
        )
        text = template.render()
        if len(text) > MAX_OUTPUT_CHARS:
            # Backstop for growth the repeat cap does not see — a nested loop,
            # a macro over a long list. Refusing beats returning it: the result
            # is JSON-encoded into a response and then assigned to innerHTML.
            return (
                False,
                f"preview output too large ({len(text)} chars; limit {MAX_OUTPUT_CHARS})",
                body,
            )
        return True, None, text
    except Exception as exc:  # noqa: BLE001 - preview must never crash the request
        logger.debug("Studio tier-2 render failed for %s: %s", deck_path, exc)
        return False, str(exc), body
