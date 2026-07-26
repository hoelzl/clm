"""Validation for git remote URLs derived from a course spec (S5).

``<repository-base>`` / ``<remote-template>`` come out of the course spec — a
file that ships with the course content — and the derived URL is handed to
``git clone`` / ``git ls-remote``. Git URLs are not inert: the syntax
``<helper>::<address>`` selects a *remote helper*, and the built-in ``ext::``
helper **executes its argument as a shell command**. So
``<repository-base>ext::sh -c 'curl … | sh'</repository-base>`` turns a
``clm git`` invocation into code execution, with nothing else in the spec
looking unusual.

This module is the scheme allowlist. It is one of two layers, deliberately:

1. **Here** — spec-derived URLs are validated when they are composed, so the
   error names the spec element and the offending value rather than surfacing as
   a confusing git failure (or not surfacing at all).
2. **Transport** — every git invocation in ``clm.cli.commands.git`` passes
   ``-c protocol.ext.allow=never``, which also covers URLs this module never
   sees (a hand-edited ``.git/config`` remote in an output repo, a submodule).

Neither layer alone is enough: (1) cannot see URLs that do not come from a spec,
and (2) would let a bad spec value fail late and cryptically.
"""

from __future__ import annotations

import re

#: URL schemes a spec may name. ``http`` is included because internal GitLab
#: instances are still reachable that way; ``file`` because a local bare repo is
#: a legitimate (and test-friendly) remote. Everything not listed — notably any
#: ``<helper>::<address>`` form — is refused.
ALLOWED_SCHEMES = ("https", "http", "ssh", "git", "file")

#: ``scheme://rest`` where scheme is alphanumeric-ish, per RFC 3986.
_SCHEME_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://")

#: A bare ``scheme:rest`` (no slashes) — how ``ext:``-style and mailto-style
#: values look. A single-letter scheme is a Windows drive letter, not a scheme.
_BARE_SCHEME_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]+):(?!//)")


class RemoteUrlError(ValueError):
    """A git remote URL a course spec is not allowed to produce."""


def validate_remote_url(url: str, *, source: str = "the course spec") -> str:
    """Return ``url`` unchanged, or raise :class:`RemoteUrlError`.

    Accepted: ``https`` / ``http`` / ``ssh`` / ``git`` / ``file`` URLs, the
    scp-like ``user@host:path`` form git also understands, and a plain local
    path (including a Windows drive path). Refused: a remote-helper
    ``<helper>::<address>`` value, any other scheme, an empty value, and
    anything starting with ``-`` (which git would read as an option).
    """
    candidate = url.strip()
    if not candidate:
        raise RemoteUrlError(f"{source} produced an empty git remote URL")
    if candidate.startswith("-"):
        raise RemoteUrlError(
            f"{source} produced the git remote URL {url!r}, which starts with '-' "
            "and would be read by git as a command-line option"
        )
    if "::" in candidate:
        # The remote-helper form. `ext::` executes its argument; the others are
        # not needed by any CLM workflow, so the whole syntax is refused rather
        # than allowlisted helper-by-helper.
        raise RemoteUrlError(
            f"{source} produced the git remote URL {url!r}, which uses git's "
            "'<helper>::<address>' remote-helper syntax. That is refused: the "
            "built-in 'ext::' helper executes its argument as a shell command. "
            f"Use one of {', '.join(ALLOWED_SCHEMES)} or the user@host:path form."
        )
    match = _SCHEME_RE.match(candidate) or _BARE_SCHEME_RE.match(candidate)
    if match is not None:
        scheme = match.group("scheme").lower()
        if scheme not in ALLOWED_SCHEMES:
            raise RemoteUrlError(
                f"{source} produced the git remote URL {url!r}, whose scheme "
                f"{scheme!r} is not allowed. Allowed: "
                f"{', '.join(ALLOWED_SCHEMES)}, the user@host:path form, or a "
                "local path."
            )
    # No scheme: scp-like (`git@host:path`), a local path, or a Windows drive
    # path (`C:\repos\x`) — all inert, all legitimate.
    return url
