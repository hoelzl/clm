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
``{{ "A" * 200000000 }}`` was a one-expression memory bomb. Every way of
growing a *value* is now bounded, each needing a different hook because Jinja
routes them differently:

- ``*`` and ``+`` — ``intercepted_binops``, refused before they allocate
  (:data:`MAX_REPEAT`, :data:`MAX_OUTPUT_CHARS`).
- the rendered output — :meth:`concat`, accumulating with a running check.
  This matters because a post-render ``len()`` bounds only what is *returned*:
  a 500-iteration loop emitting an individually-legal 100 000 characters each
  peaked at 100 MB before such a check could run, and 1.1 MB after it.
- ``~`` — neither of the above can see it. It compiles to a ``Concat`` node,
  which is not a ``BinExpr`` (invisible to ``intercepted_binops``) and which
  emits ``str_join`` resolved from the compiled template's own namespace
  (invisible to ``environment.concat``). It is redirected at compile time
  instead, via ``code_generator_class`` — a documented per-environment
  extension point, so the build pipeline's environments are untouched.
  ``{% set s = "A" * 100000 %}{% set s = s ~ s %}…`` went from ~1.2 GB to
  5.2 MB.

**Still unbounded: CPU.** Nothing limits iteration, so nested ``{% for %}``
over ``range()`` burns time without allocating, and ``run_in_threadpool``
gives a client 40 shared threads to occupy. A token holder can still stall
this process. That is accepted rather than fixed — the token is the trust
boundary (decision D4) and the same client can already rewrite any deck —
and doing it properly means rendering under a wall-clock limit in a
subprocess, which is issue #698.

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

**What reaches the phone (issue #697).** :func:`render_j2_cell` returns expanded
*text*; :func:`render_j2_cell_html` is what the endpoint uses, and it goes on to
drop the ``%% [markdown]`` delimiter line, strip the comment prefix, and
**sanitize** the result (:mod:`clm.web.studio.sanitize`). The order is the point:
the client injects the sanitizer's output verbatim, so every byte-changing step
happens before sanitizing, never after.

The endpoint sends the sanitized fragment plus the caller's *original* body (for
the tier-1 fallback) and never the expanded text — so no route hands a consumer
something unsanitized to reach for by mistake. Note that is a property of the
callers, not of this module: :func:`render_j2_cell` is public and still returns
raw expanded text, because the expansion is worth testing on its own. Anything
new that calls it and ships the result to a browser owes it a sanitize pass.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from clm.web.studio.logo import rewrite_bundled_logo
from clm.web.studio.sanitize import SanitizerUnavailableError, sanitize_preview_html

logger = logging.getLogger(__name__)

#: A cell-delimiter line in the expanded output. The header macros emit a whole
#: *cell* — ``%% [markdown] lang="de" tags=["slide"]`` — because in the build the
#: expansion happens before cell splitting. A single-cell preview must drop it
#: (with or without the language's comment prefix, which the source line carries
#: and the macro's own first line does not).
#:
#: ``%%`` must be followed by whitespace, ``[``, or end-of-line: jupytext's own
#: rule, and without it prose that merely begins ``%%something`` disappears from
#: the preview.
_CELL_DELIMITER = re.compile(r"^\s*(?:#|//)?\s*%%(?:\s.*|\[.*)?$")

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
    from jinja2.compiler import CodeGenerator
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    from jinja2.sandbox import SecurityError as _SecurityError

    def _bounded_join(iterable) -> str:  # noqa: ANN001
        """Join ``iterable``, refusing once the running total exceeds the cap.

        The point is *running*: a check after ``"".join(...)`` bounds only what
        is returned, so a 500-iteration loop emitting an individually-legal
        100 000 characters each peaked at 100 MB before anything looked at it.
        """
        parts: list[str] = []
        total = 0
        for part in iterable:
            text = str(part)
            total += len(text)
            if total > MAX_OUTPUT_CHARS:
                raise _SecurityError(f"preview output exceeded {MAX_OUTPUT_CHARS} characters")
            parts.append(text)
        return "".join(parts)

    class _BoundedCodeGenerator(CodeGenerator):
        """Route ``~`` through the environment so it can be bounded.

        Stock Jinja compiles ``~`` to ``str_join((a, b))``, resolved from the
        compiled template's own module namespace — which is why neither
        ``intercepted_binops`` (``Concat`` is not a ``BinExpr``) nor
        ``environment.concat`` can see it. ``code_generator_class`` is a
        documented per-environment extension point, so redirecting the emitted
        call is enough; the build pipeline's own environments are untouched.
        """

        def visit_Concat(self, node, frame) -> None:  # noqa: ANN001
            self.write("environment.concat_operands((")
            for arg in node.nodes:
                self.visit(arg, frame)
                self.write(", ")
            self.write("))")

    class _PreviewEnvironment(ImmutableSandboxedEnvironment):
        # `*` and `+` go through the sandbox's binop interception; `~` cannot
        # (see _BoundedCodeGenerator), so it is redirected at compile time
        # instead. Adding "~" to this set is silently inert — don't.
        intercepted_binops = frozenset({"*", "+"})
        code_generator_class = _BoundedCodeGenerator

        def call_binop(self, context, operator, left, right):  # noqa: ANN001
            if operator == "*":
                _refuse_oversized_repeat(left, right)
                _refuse_oversized_repeat(right, left)
            else:
                _refuse_oversized_concat(left, right)
            return super().call_binop(context, operator, left, right)

        def concat(self, iterable) -> str:  # noqa: ANN001
            """Bound the rendered output as it accumulates.

            Jinja emits ``concat = environment.concat`` into the root render
            function *and* every block, macro, ``{% filter %}`` and ``{% call %}``
            body — so this runs once per output buffer, not once per render.
            Each call bounds its own buffer and the root call still sees the
            grand total, so the effect is a stricter bound, not a leaky one.
            """
            return _bounded_join(iterable)

        def concat_operands(self, iterable) -> str:  # noqa: ANN001
            """Bound a ``~`` expression. Emitted by :class:`_BoundedCodeGenerator`.

            Separate from :meth:`concat` only because this one receives
            arbitrary operands rather than already-rendered strings; both
            stringify, so the shared join handles it.
            """
            return _bounded_join(iterable)

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


def _to_preview_html(text: str, comment_token: str) -> str:
    """Turn expanded cell text into the HTML fragment the client injects.

    Two transforms, both of which must happen **before** sanitizing — the client
    injects the sanitizer's output verbatim, so anything that changes the bytes
    afterwards would be injecting something nobody checked:

    1. drop the ``%% [markdown] …`` cell-delimiter lines the header macros emit
       (see :data:`_CELL_DELIMITER`);
    2. strip the language's comment prefix, since the macro output is
       comment-prefixed source (``# <div …>`` / ``// <div …>``) and the ``<`` has
       to reach the browser as markup.

    The prefix strip is the editor's own :func:`~clm.web.studio.prefix.deprefix`
    rather than a second implementation — it is already the per-line,
    leave-unprefixed-lines-alone rule this needs, which matters because the
    base64 logo the macros embed continues on *unprefixed* continuation lines.
    """
    from clm.web.studio.prefix import deprefix

    kept = [line for line in text.split("\n") if not _CELL_DELIMITER.match(line)]
    return deprefix("\n".join(kept), comment_token).strip("\n")


def render_j2_cell_html(
    deck_path: Path, body: str, lang: str | None
) -> tuple[bool, str | None, str | None]:
    """Expand ``body``'s Jinja and return **sanitized HTML** for the phone.

    Returns ``(ok, error, html)``. ``ok`` True → ``html`` is safe to assign to
    ``innerHTML`` (:mod:`clm.web.studio.sanitize` is the allowlist, and see
    :func:`_to_preview_html` for why the de-prefixing runs first). False →
    ``html`` is ``None``, ``error`` says why, and the caller falls back to
    tier-1 client-side markdown.

    Fails closed when the sanitizer is missing: no HTML leaves this function
    unless it went through :func:`sanitize_preview_html`.
    """
    ok, error, text = render_j2_cell(deck_path, body, lang)
    if not ok:
        return False, error, None
    try:
        from clm.infrastructure.utils.path_utils import path_to_prog_lang
        from clm.workers.notebook.utils.prog_lang_utils import line_comment_for

        try:
            prog_lang = path_to_prog_lang(deck_path)
        except (KeyError, ValueError):
            prog_lang = "python"
        comment_token = line_comment_for(prog_lang)
    except Exception as exc:  # noqa: BLE001 - missing optional dep → tier-1 fallback
        return False, f"render unavailable: {exc}", None

    try:
        # The bundled logo's data: URI becomes a same-origin asset URL *before*
        # sanitizing (#706) — the client injects exactly what was sanitized,
        # and a relative URL needs no data: exception in the allowlist.
        preview_html = rewrite_bundled_logo(_to_preview_html(text, comment_token), prog_lang)
        return True, None, sanitize_preview_html(preview_html)
    except SanitizerUnavailableError as exc:
        return False, str(exc), None
    except Exception as exc:  # noqa: BLE001 - preview must never crash the request
        logger.debug("Studio tier-2 sanitize failed for %s: %s", deck_path, exc)
        return False, f"sanitize failed: {exc}", None
