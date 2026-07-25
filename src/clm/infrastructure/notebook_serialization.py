"""Serialization for executed notebooks kept in the execution cache.

The executed-notebook cache used to hold ``pickle.dumps(NotebookNode)``, both
in ``clm_cache.db`` and on the Worker API wire. That made every consumer of a
cache entry a deserialization-RCE sink: the API accepted the bytes from any
caller that could reach the port, stored them verbatim, and the host later
unpickled them.

nbformat JSON removes the class of bug rather than defending against it.
``NotebookNode`` is a ``dict`` subclass and executed notebooks are exactly
what nbformat is for, so the payload survives the round trip unchanged while
the parser can only ever produce data.

Serialization is pinned to :data:`SERIALIZED_NBFORMAT_VERSION` rather than
``NO_CONVERT`` so a cache entry cannot depend on the nbformat version of
whichever worker happened to write it — a direct-mode worker and a container
must agree byte-for-byte on the same notebook.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from nbformat import NotebookNode

logger = logging.getLogger(__name__)

#: nbformat major version every cache payload is written in. v4 is what
#: jupytext produces and what the workers execute, so this is a pin, not a
#: conversion.
SERIALIZED_NBFORMAT_VERSION = 4

#: Encoding of the stored/transmitted bytes.
_ENCODING = "utf-8"


class NotebookSerializationError(ValueError):
    """Raised when a payload is not a notebook this cache can round-trip."""


def serialize_notebook(notebook: NotebookNode) -> bytes:
    """Return ``notebook`` as nbformat JSON bytes.

    Raises:
        NotebookSerializationError: If the notebook cannot be written (for
            example, an output carrying a non-JSON-serializable value).
    """
    import nbformat

    try:
        # ``from_dict`` recursively promotes plain dicts to NotebookNode. The
        # writer reaches into outputs with attribute access, so a notebook
        # assembled by hand — outputs appended as literal dicts — would
        # otherwise fail to serialize even though it is valid data.
        text: str = nbformat.writes(
            nbformat.from_dict(notebook), version=SERIALIZED_NBFORMAT_VERSION
        )
    except Exception as e:
        raise NotebookSerializationError(f"Could not serialize notebook: {e}") from e
    return text.encode(_ENCODING)


def deserialize_notebook(payload: bytes) -> NotebookNode:
    """Parse nbformat JSON ``payload`` back into a ``NotebookNode``.

    Raises:
        NotebookSerializationError: If the payload is not decodable text, not
            JSON, or not a notebook. Callers treat this as a cache miss rather
            than propagating it — a corrupt entry must never fail a build.
    """
    import nbformat

    try:
        text = payload.decode(_ENCODING)
    except UnicodeDecodeError as e:
        raise NotebookSerializationError(f"Payload is not {_ENCODING} text: {e}") from e

    try:
        notebook = nbformat.reads(text, as_version=SERIALIZED_NBFORMAT_VERSION)
    except Exception as e:
        raise NotebookSerializationError(f"Payload is not a valid notebook: {e}") from e
    return cast("NotebookNode", notebook)
