"""Export a historical deck bundle without transcription or model judgment."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from clm.core.slide_text.pairing import split_lang_tag
from clm.core.voiceover_companions import COMPANION_SUBDIR, companion_name


def export_at_rev(slide_file: Path, rev: str, output: Path) -> dict[str, Any]:
    """Export the deck, historical split twin and companions into a new directory.

    Names and companion locations are preserved. Resolution uses the selected
    tree, independent of working-copy presence/layout. A path absent at the
    selected revision is an error; renames are not inferred. Blob bytes are
    preserved. Existing destinations are refused, including empty directories.
    """
    slide_file = slide_file.absolute()
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"output directory already exists: {output}")

    def git(cwd: Path, *args: str) -> bytes:
        try:
            return subprocess.check_output(
                ["git", "--literal-pathspecs", "-C", str(cwd), *args], stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(exc.stderr.decode("utf-8", errors="replace").strip()) from exc

    anchor = slide_file.parent
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    root = Path(git(anchor, "rev-parse", "--show-toplevel").decode("utf-8").strip())
    sha = (
        git(root, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}").decode().strip()
    )
    rel = slide_file.relative_to(root)
    decks = [rel]
    lang = split_lang_tag(slide_file)
    if lang is not None:
        twin = rel.with_name(f"{rel.stem[:-3]}.{'en' if lang == 'de' else 'de'}{rel.suffix}")
        decks.append(twin)

    candidates: list[Path] = []
    for deck in decks:
        candidates.extend(
            [
                deck,
                deck.parent / COMPANION_SUBDIR / companion_name(deck),
                deck.with_name(companion_name(deck)),
            ]
        )
    tree = git(root, "ls-tree", "-z", sha, "--", *(p.as_posix() for p in candidates))
    blobs: dict[str, tuple[str, str]] = {}
    for entry in tree.split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError(f"not a regular historical file: {name.decode('utf-8')}")
            blobs[name.decode("utf-8")] = (mode, oid)
    if rel.as_posix() not in blobs:
        raise ValueError(f"{rel.as_posix()} does not exist at revision {sha}")

    selected: list[Path] = []
    for deck in decks:
        if deck.as_posix() not in blobs:
            continue
        selected.append(deck)
        for companion in (
            deck.parent / COMPANION_SUBDIR / companion_name(deck),
            deck.with_name(companion_name(deck)),
        ):
            if companion.as_posix() in blobs:
                selected.append(companion)
                break
    # Read everything before creating output, so git failures leave no artifact.
    contents = {
        path.relative_to(rel.parent): git(root, "cat-file", "blob", blobs[path.as_posix()][1])
        for path in selected
    }
    output.mkdir(parents=True, exist_ok=False)
    try:
        for path, content in contents.items():
            destination = output / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    except OSError:
        shutil.rmtree(output)
        raise
    return {
        "schema": 1,
        "tool": "harvest",
        "verb": "export-at-rev",
        "revision": sha,
        "deck": str(output / slide_file.name),
        "files": [str(output / path) for path in contents],
    }
