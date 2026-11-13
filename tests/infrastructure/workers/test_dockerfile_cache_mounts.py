"""Static checks on the BuildKit cache mounts in the worker Dockerfiles.

Issue #1042: no apt cache mount set ``id=``, so BuildKit keyed each cache on
its target path and every image shared one ``/var/cache/apt`` and one
``/var/lib/apt`` cache. Combined with ``sharing=locked`` (which apt needs),
that put every apt step of every clm image on the daemon behind a single
lock: a plantuml build sat ~50 minutes waiting for a concurrent cold notebook
build. Each apt mount now carries an id unique to its image (and, for the
notebook image, to its base distro), which these checks pin.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]
DOCKERFILES = sorted((REPO_ROOT / "docker").glob("*/Dockerfile"))

_MOUNT_RE = re.compile(r"--mount=(\S+)")
_APT_TARGETS = {"/var/cache/apt", "/var/lib/apt"}


def _cache_mounts(dockerfile: Path) -> list[tuple[int, dict[str, str]]]:
    mounts = []
    for lineno, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
        for spec in _MOUNT_RE.findall(line):
            opts = dict(
                part.split("=", 1) if "=" in part else (part, "") for part in spec.split(",")
            )
            if opts.get("type") == "cache":
                mounts.append((lineno, opts))
    return mounts


def _apt_mounts(dockerfile: Path) -> list[tuple[int, dict[str, str]]]:
    return [(n, o) for n, o in _cache_mounts(dockerfile) if o.get("target") in _APT_TARGETS]


def test_dockerfiles_found() -> None:
    names = {p.parent.name for p in DOCKERFILES}
    assert {"notebook", "plantuml", "drawio"} <= names


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_every_apt_cache_mount_has_explicit_id(dockerfile: Path) -> None:
    mounts = _apt_mounts(dockerfile)
    assert mounts, f"{dockerfile} has no apt cache mounts; update this test"
    missing = [n for n, o in mounts if not o.get("id")]
    assert not missing, (
        f"{dockerfile.relative_to(REPO_ROOT)} lines {missing}: apt cache mount without "
        "id= shares one locked cache with every other image (#1042)"
    )


def test_apt_cache_ids_are_not_shared_between_images() -> None:
    owners: dict[str, set[str]] = defaultdict(set)
    for dockerfile in DOCKERFILES:
        for _, opts in _apt_mounts(dockerfile):
            if opts.get("id"):
                owners[opts["id"]].add(dockerfile.parent.name)
    shared = {cid: sorted(imgs) for cid, imgs in owners.items() if len(imgs) > 1}
    assert not shared, f"apt cache ids shared across images (#1042): {shared}"


def test_apt_cache_and_lists_use_distinct_ids() -> None:
    """One id must never back both /var/cache/apt and /var/lib/apt."""
    targets: dict[str, set[str]] = defaultdict(set)
    for dockerfile in DOCKERFILES:
        for _, opts in _apt_mounts(dockerfile):
            targets[opts["id"]].add(opts["target"])
    mixed = {cid: sorted(t) for cid, t in targets.items() if len(t) > 1}
    assert not mixed, f"one cache id mounted at several apt targets: {mixed}"


def test_notebook_lite_and_full_bases_use_separate_apt_caches() -> None:
    """The lite (plain Ubuntu) and full (CUDA, extra apt repo) bases must not share apt state."""
    dockerfile = REPO_ROOT / "docker" / "notebook" / "Dockerfile"
    stage = None
    ids_by_stage: dict[str, set[str]] = defaultdict(set)
    stage_re = re.compile(r"^FROM\s+\S+\s+AS\s+(\S+)", re.IGNORECASE)
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    for line in lines:
        m = stage_re.match(line)
        if m:
            stage = m.group(1)
        for spec in _MOUNT_RE.findall(line):
            if "/var/lib/apt" in spec or "/var/cache/apt" in spec:
                assert stage is not None
                cid = re.search(r"id=([^,\s]+)", spec)
                assert cid, spec
                ids_by_stage[stage].add(cid.group(1))
    assert ids_by_stage["base-lite"] and ids_by_stage["base-full-amd64"]
    assert not ids_by_stage["base-lite"] & ids_by_stage["base-full-amd64"]
