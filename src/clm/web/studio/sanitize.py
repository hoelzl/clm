"""Server-side HTML sanitizing for the Studio's tier-2 cell preview (issue #697).

The tier-2 preview expands a cell's Jinja server-side so the phone sees a
rendered header instead of raw ``{{ header_de("…") }}``. The expansion is
**deliberately HTML** — the bundled ``macros.j2`` header macros emit
``<div style="text-align:center">``, a ``<br/>``, and the course logo as an
``<img src="data:image/…;base64,…">`` — so the client cannot escape its way out
of the problem: escaping the output *is* removing the feature.

The decision (maintainer, 2026-07-26; D13 in the adversarial-review handover)
is to **sanitize on the server** and keep the client's ``innerHTML``
assignment. Two consequences worth stating plainly:

* The client injects this output **verbatim**. So everything that changes the
  bytes — dropping the ``%% [markdown]`` delimiter line the macro emits,
  stripping the per-language comment prefix — happens *before* the sanitizer
  runs, never after. Sanitize exactly what gets injected.
* If :mod:`nh3` is missing (an install without the ``[web]`` extra), the
  preview **fails closed**: :func:`sanitize_preview_html` raises and the caller
  degrades to tier-1 client-side markdown. There is no unsanitized fallback.

**The ``data:`` question, measured rather than assumed.** The logo is a
``data:`` URI, so the scheme has to be allowed; but with ``data`` merely added
to nh3's ``url_schemes``, ``<a href="data:text/html,<script>…">`` survives too
(verified — it is a navigation vector, not a decoration). nh3's
``attribute_filter`` *can* drop a value before the scheme check but cannot
re-permit a scheme the allowlist rejects (also verified), so the working
combination is: allow ``data`` globally, then have the filter refuse it
everywhere except an ``<img src>`` that is specifically ``data:image/``.

**Not sanitized: the contents of ``style``.** Ammonia does not parse CSS, so
the property allowlist here is CLM's own — the properties the bundled macros
actually use plus a few text-formatting ones — with any declaration containing
``url(`` / ``expression(`` / a backslash escape dropped. CSS cannot execute
script in a current browser; the residual risk is visual (an overlay), inside a
page whose content the token holder can rewrite anyway.
"""

from __future__ import annotations

import re

#: Tags a preview may contain: the macro output (``img``/``div``/``b``/``br``)
#: plus ordinary prose markup, since a j2 cell can carry markdown-ish HTML
#: around its macro call. Everything else — ``script``, ``style``, ``iframe``,
#: ``svg``, ``form``, ``math`` — is removed with its content.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "center",
        "code",
        "dd",
        "del",
        "div",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "ins",
        "kbd",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
        "var",
    }
)

#: Per-tag attribute allowlist. ``rel`` is absent on purpose: nh3 manages it
#: (it adds ``rel="noopener noreferrer"`` to every link) and rejects the config
#: outright if both are set.
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "*": {"style", "class", "align"},
}

#: Link schemes a preview may point at. ``data`` is here **only** so the logo
#: image works; :func:`_attribute_filter` is what confines it to ``<img src>``.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "data"})

#: CSS properties kept inside a surviving ``style`` attribute. The first five
#: are what the bundled header macros use; the rest are inert text formatting.
ALLOWED_CSS_PROPERTIES: frozenset[str] = frozenset(
    {
        "display",
        "font-size",
        "font-style",
        "font-weight",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "padding",
        "text-align",
        "text-decoration",
        "vertical-align",
        "width",
        "max-width",
        "height",
        "color",
        "background-color",
    }
)

#: Declaration content that is never kept, whatever the property: ``url()``
#: fetches, the legacy IE ``expression()``, and CSS backslash escapes (which
#: exist to spell any of the above without spelling it).
_CSS_REJECT = re.compile(r"url\s*\(|expression\s*\(|\\", re.IGNORECASE)

#: Whitespace is not significant inside a URL scheme, and ``da\nta:`` is a real
#: obfuscation, so the scheme check runs against a whitespace-stripped copy.
_WHITESPACE = re.compile(r"\s+")


class SanitizerUnavailableError(RuntimeError):
    """:mod:`nh3` is not installed, so no HTML may be handed to the client.

    Raised instead of returning unsanitized markup: the ``[web]`` extra carries
    the sanitizer, and an install without it must lose the *feature*, not the
    guarantee.
    """


def _scheme_of(value: str) -> str:
    """The URL scheme of ``value``, lowercased, or ``""`` if it names none."""
    candidate = _WHITESPACE.sub("", value)
    head, sep, _rest = candidate.partition(":")
    if not sep or not head or not head.isascii():
        return ""
    return head.lower()


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """nh3 per-attribute hook: confine ``data:`` and filter CSS declarations.

    Returning ``None`` drops the attribute. nh3 still applies its own scheme
    allowlist afterwards, so this can only ever be *more* restrictive — which
    is why ``data`` has to be in :data:`ALLOWED_URL_SCHEMES` for the ``img``
    case to survive at all.
    """
    if attribute == "style":
        return _filter_style(value)
    if _scheme_of(value) == "data":
        # The one legitimate use is the logo the header macros embed.
        if tag == "img" and attribute == "src":
            head = _WHITESPACE.sub("", value).lower()
            return value if head.startswith("data:image/") else None
        return None
    return value


def _filter_style(value: str) -> str | None:
    """Keep only allowlisted, inert CSS declarations; ``None`` if none survive."""
    kept: list[str] = []
    for declaration in value.split(";"):
        prop, sep, val = declaration.partition(":")
        if not sep:
            continue
        name = prop.strip().lower()
        body = val.strip()
        if name not in ALLOWED_CSS_PROPERTIES or not body:
            continue
        if _CSS_REJECT.search(declaration):
            continue
        kept.append(f"{name}:{body}")
    return "; ".join(kept) if kept else None


def sanitize_preview_html(html: str) -> str:
    """Return ``html`` reduced to the preview allowlist.

    Raises :class:`SanitizerUnavailableError` when :mod:`nh3` is missing — the
    caller must then fall back to tier-1 rather than ship the input onward.
    """
    try:
        import nh3
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise SanitizerUnavailableError(
            "the HTML sanitizer (nh3) is not installed, so the server-side cell "
            "preview is unavailable; install clm[web]"
        ) from exc

    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(ALLOWED_URL_SCHEMES),
        attribute_filter=_attribute_filter,
    )
