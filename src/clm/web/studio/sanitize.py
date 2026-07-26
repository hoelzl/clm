r"""Server-side HTML sanitizing for the Studio's tier-2 cell preview (issue #697).

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
``url(`` / ``expression(`` / a backslash escape dropped (after comments are
stripped, since ``url/*x*/(`` otherwise walks past that check). CSS cannot
execute script in a current browser, so the residual risk is visual: a
fixed-position overlay that impersonates the app's own UI and links out.

That is also why ``class`` is **not** an allowed attribute even though it looks
inert. The Studio's own stylesheet defines ``.toast { position: fixed;
z-index: 20 }``, so injected markup could *name* that class and get the exact
overlay :data:`ALLOWED_CSS_PROPERTIES` refuses to grant through ``style`` — an
allowlist is only as tight as the page's own class names. Found by an
adversarial review of this module; **a property allowlist and a class allowlist
have to be reasoned about together.**

**URL rules, and why they are here rather than left to nh3.** Two decisions
nh3's declarative config cannot express, both in :func:`_attribute_filter`:

* ``data:`` is confined to an ``<img src>`` that is ``data:image/…`` (the logo).
* an **authority-relative** target (``//host``, and the ``\\`` / ``/\`` / ``\/``
  spellings WHATWG parsing treats identically) is refused, matching the client's
  ``safeUrl()`` for tier-1 markdown links. Both tiers render into the same page,
  which holds a non-expiring bearer token, so both get the same rule — and an
  ``<img>`` needs no click, so opening a deck would otherwise beacon.

Both decisions depend on normalizing a URL the way ammonia and the browser do,
which is **not** Python's ``\s`` — see :data:`_URL_INSIGNIFICANT` for the
control-character gap that made this a real bypass.
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

#: Tags removed **with their text content**, not just unwrapped. Without this,
#: ``<iframe>fallback</iframe>`` leaves ``fallback`` behind as prose — harmless
#: on its own, but it makes "active content is removed" false as stated, and
#: fallback text is exactly where a payload's social-engineering copy lives.
CLEAN_CONTENT_TAGS: frozenset[str] = frozenset(
    {"script", "style", "iframe", "object", "embed", "form", "noscript", "template"}
)

#: Per-tag attribute allowlist. Two deliberate absences:
#:
#: * ``rel`` — nh3 manages it (it adds ``rel="noopener noreferrer"`` to every
#:   link) and rejects the config outright if both are set.
#: * ``class`` — it was here, and it made :data:`ALLOWED_CSS_PROPERTIES`
#:   decorative: the Studio's own stylesheet defines ``.toast { position: fixed;
#:   z-index: 20 }``, so injected markup could *name* that class and get the
#:   fixed-position overlay the CSS allowlist exists to refuse — no ``style``
#:   attribute needed. An allowlist that omits a property while allowing the
#:   page's own class that sets it is not an allowlist. The macros emit inline
#:   ``style``, never ``class``, so nothing legitimate wanted it.
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "*": {"style", "align"},
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
#: exist to spell any of the above without spelling it). Comments are stripped
#: first (:data:`_CSS_COMMENT`) — ``url/*x*/(…)`` otherwise walks past a naive
#: ``url\s*\(``.
_CSS_REJECT = re.compile(r"url\s*\(|expression\s*\(|\\", re.IGNORECASE)

#: A CSS comment, which may appear *inside* a declaration.
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Characters to remove before judging a URL's scheme. **Not** Python ``\s``:
#: ammonia (and the WHATWG URL parser) strip every C0 control plus space, and
#: Python's ``\s`` misses U+0001–U+0008 and U+000E–U+001B. That gap was a real
#: bypass — ``<a href="&#1;data:text/html;base64,…">`` left this filter seeing
#: the scheme ``"\x01data"`` (no match, so no refusal) while nh3 normalized the
#: control away, saw ``data:``, found it allowlisted, and kept the attribute.
#: The two normalizations have to agree, so this one is the stricter superset.
_URL_INSIGNIFICANT = re.compile(r"[\x00-\x20\x7f]+|\s+")

#: Leading pairs that make a URL **authority-relative** — an off-origin
#: navigation from a page holding a non-expiring bearer token. WHATWG parsing
#: treats ``\`` as ``/`` for http(s), so all four combinations reach the same
#: host. The Studio's client-side ``safeUrl()`` refuses these for tier-1
#: markdown links; tier-2 output lands in the same page and gets the same rule.
_AUTHORITY_RELATIVE = ("//", r"\\", "/\\", "\\/")


class SanitizerUnavailableError(RuntimeError):
    """:mod:`nh3` is not installed, so no HTML may be handed to the client.

    Raised instead of returning unsanitized markup: the ``[web]`` extra carries
    the sanitizer, and an install without it must lose the *feature*, not the
    guarantee.
    """


def _normalize_url(value: str) -> str:
    """``value`` with the characters a URL parser ignores removed, lowercased.

    The comparison form for every URL decision in this module, so all of them
    agree with the normalization nh3 and the browser perform. See
    :data:`_URL_INSIGNIFICANT` for why this is not ``\\s``.
    """
    return _URL_INSIGNIFICANT.sub("", value).lower()


def _scheme_of(normalized: str) -> str:
    """The scheme of an already-:func:`_normalize_url`\\ ed value, or ``""``."""
    head, sep, _rest = normalized.partition(":")
    if not sep or not head or not head.isascii():
        return ""
    return head


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """nh3 per-attribute hook: the decisions nh3's own config cannot express.

    Returning ``None`` drops the attribute. nh3 still applies its own scheme
    allowlist afterwards, so this can only ever be *more* restrictive — which
    is why ``data`` has to be in :data:`ALLOWED_URL_SCHEMES` for the ``img``
    case to survive at all.

    Three rules: CSS is filtered (:func:`_filter_style`); ``data:`` is confined
    to an ``<img src>`` that is an image; and an **authority-relative** target is
    refused outright, matching the client's ``safeUrl()`` for tier-1 links (both
    land in the same token-holding page, so both get the same rule).
    """
    if attribute == "style":
        return _filter_style(value)
    if attribute not in ("href", "src"):
        return value
    normalized = _normalize_url(value)
    if normalized.startswith(_AUTHORITY_RELATIVE):
        return None
    # Strip the leading characters a URL parser ignores — case-preserving, so a
    # base64 payload survives — rather than shipping invisible control bytes the
    # client would then have to be trusted to normalize the same way we did.
    kept = value.lstrip("".join(chr(c) for c in range(0x21)) + "\x7f")
    if _scheme_of(normalized) == "data":
        # The one legitimate use is the logo the header macros embed.
        if tag == "img" and attribute == "src" and normalized.startswith("data:image/"):
            return kept
        return None
    return kept


def _filter_style(value: str) -> str | None:
    """Keep only allowlisted, inert CSS declarations; ``None`` if none survive."""
    kept: list[str] = []
    for declaration in _CSS_COMMENT.sub("", value).split(";"):
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
    except ImportError as exc:
        raise SanitizerUnavailableError(
            "the HTML sanitizer (nh3) is not installed, so the server-side cell "
            "preview is unavailable; install clm[web]"
        ) from exc

    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        clean_content_tags=set(CLEAN_CONTENT_TAGS),
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(ALLOWED_URL_SCHEMES),
        attribute_filter=_attribute_filter,
    )
