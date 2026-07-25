"""Guard against tests naming a Docker image CLM does not build.

The sibling of ``test_worker_module_probes.py``, for image tags instead of
module names — and it exists for the same reason, one layer down.

``test_direct_integration.py`` hard-coded an image whose repository had lost
its ``clm-`` prefix. CLM has not published under that name since the images
were renamed, so the Docker client treated it as a registry reference and
tried to *pull* it:

    docker.errors.ImageNotFound: 404 ... pull access denied for
    drawio-converter, repository does not exist or may require 'docker login'

Nothing caught it, for two compounding reasons: the module was permanently
skipped (finding T1), and the **Docker Integration Tests** job is not a
required status check — so even after the skip was lifted, the PR was green
and `master` broke.

These tests are fast and unmarked, so they run on every commit. A rename of a
CI image tag, or a test reaching for a tag nobody builds, now fails in the
72-second gate rather than in a job that does not block anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from clm.cli.commands.docker import AVAILABLE_SERVICES, HUB_NAMESPACE, REGISTRY
from clm.infrastructure.config import DEFAULT_WORKER_IMAGES

REPO_ROOT = Path(__file__).parents[3]
TESTS_ROOT = REPO_ROOT / "tests"
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))

# ``clm docker build`` composes image names as ``clm-<service>`` where the
# notebook service is published as ``clm-notebook-processor`` and the
# converters as ``clm-<name>-converter`` (docker.py:184).
CLM_REPOSITORIES = {
    "clm-notebook-processor",
    "clm-plantuml-converter",
    "clm-drawio-converter",
}

# Any string literal in the test tree that *looks* like a CLM worker image
# reference. Deliberately matches the un-prefixed form too — catching those is
# the whole point.
IMAGE_LITERAL = re.compile(
    r"""["']((?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]*(?:converter|processor)[A-Za-z0-9._-]*:[A-Za-z0-9._-]+)["']"""
)

# Tags that exist without any workflow building them: the variants
# ``clm docker build`` publishes, plus version tags like ``1.22.1-full``.
# A bare reference to one of these is legitimate in an ``images.get()`` probe
# list (which never pulls) — several tests search local-then-published tags
# that way.
PUBLISHED_TAGS = {"latest", "lite", "full"}
VERSION_TAG = re.compile(r"^\d+\.\d+\.\d+(?:-[A-Za-z0-9._-]+)?$")


def _iter_image_literals() -> list[tuple[Path, int, str]]:
    """Every image-shaped literal in the test tree, with its location.

    This module is skipped: its own prose necessarily quotes the malformed
    references it exists to reject.
    """
    found: list[tuple[Path, int, str]] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in IMAGE_LITERAL.finditer(line):
                found.append((path, lineno, match.group(1)))
    return found


def _split_reference(reference: str) -> tuple[str, str, str]:
    """Split ``[registry/][namespace/]repository:tag`` into its parts."""
    repository, _, tag = reference.rpartition(":")
    *prefix, name = repository.split("/")
    return "/".join(prefix), name, tag


# Both spellings of "this workflow builds this tag": the ``-t`` flag of a plain
# ``docker build``, and the ``tags:`` input of ``docker/build-push-action``.
# Both are matched so the guard survives a workflow switching between them — it
# already had to once, when the builds moved to build-push-action for layer
# caching, and the anchor test below is what caught it.
WORKFLOW_TAG = re.compile(
    r"(?:-t\s+|^\s*tags:\s*)([A-Za-z0-9._/-]+:[A-Za-z0-9._-]+)\s*$",
    re.MULTILINE,
)


def _ci_built_tags() -> set[str]:
    """Locally-built image tags, read out of the workflow files.

    Parsed from the workflows rather than hard-coded, so a rename of a CI tag
    changes both sides at once and this test cannot drift into agreeing with
    itself.
    """
    tags: set[str] = set()
    for workflow in WORKFLOWS:
        for match in WORKFLOW_TAG.finditer(workflow.read_text(encoding="utf-8")):
            tags.add(match.group(1))
    return tags


def test_workflows_build_the_expected_local_tags() -> None:
    """The workflows must build one local tag per worker image.

    Anchors the other tests: if this set ever empties (a workflow rename, a
    change of build action, a parsing change), they would silently stop
    checking anything.
    """
    tags = _ci_built_tags()
    assert tags, f"no locally-built image tags found in {[w.name for w in WORKFLOWS]}"

    repositories = {_split_reference(tag)[1] for tag in tags}
    assert repositories == CLM_REPOSITORIES, (
        f"workflows build {sorted(repositories)}, expected {sorted(CLM_REPOSITORIES)}"
    )


def test_default_worker_images_match_the_published_naming() -> None:
    """``DEFAULT_WORKER_IMAGES`` must agree with what ``clm docker`` publishes."""
    assert set(DEFAULT_WORKER_IMAGES) == set(AVAILABLE_SERVICES)
    for service, reference in DEFAULT_WORKER_IMAGES.items():
        prefix, repository, tag = _split_reference(reference)
        assert prefix == f"{REGISTRY}/{HUB_NAMESPACE}", (
            f"{service}: {reference} is not under {REGISTRY}/{HUB_NAMESPACE}"
        )
        assert repository in CLM_REPOSITORIES, f"{service}: unknown repository {repository!r}"
        assert tag, f"{service}: {reference} has no tag"


def test_every_image_literal_in_tests_is_one_clm_builds() -> None:
    """No test may name an image outside the set CLM builds or publishes.

    Three rules, in order of how likely each is to rot:

    1. The **repository** must be one CLM builds. This is the rule that would
       have caught the real bug — the failing reference had lost its ``clm-``
       prefix — and it is the part a rename breaks.
    2. A registry-qualified reference must sit under ``docker.io/mhoelzl``.
    3. A bare tag must be one a workflow builds, or a published variant
       (``latest``/``lite``/``full``/a version). Bare published tags are
       legitimate inside ``images.get()`` probe lists, which never pull; a
       typo'd tag still fails.
    """
    ci_tags = _ci_built_tags()
    problems: list[str] = []

    for path, lineno, reference in _iter_image_literals():
        location = f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}"
        prefix, repository, tag = _split_reference(reference)

        if repository not in CLM_REPOSITORIES:
            problems.append(
                f"{location}: {reference!r} — repository {repository!r} is not one CLM builds "
                f"({sorted(CLM_REPOSITORIES)}). Did the `clm-` prefix get dropped?"
            )
            continue

        if prefix:
            if prefix != f"{REGISTRY}/{HUB_NAMESPACE}":
                problems.append(
                    f"{location}: {reference!r} — published images live under "
                    f"{REGISTRY}/{HUB_NAMESPACE}, not {prefix!r}"
                )
            continue

        known = reference in ci_tags or tag in PUBLISHED_TAGS or VERSION_TAG.match(tag)
        if not known:
            problems.append(
                f"{location}: {reference!r} — tag {tag!r} is neither built by a workflow "
                f"({sorted(ci_tags)}) nor a published variant ({sorted(PUBLISHED_TAGS)} "
                f"or a version). A Docker client will try to PULL this."
            )

    assert not problems, "Docker image references that cannot resolve:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_parseable(workflow: Path) -> None:
    """A malformed workflow silently stops running — parse it here, cheaply."""
    parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{workflow.name} did not parse to a mapping"
    assert parsed.get("jobs"), f"{workflow.name} declares no jobs"
