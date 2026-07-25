"""Finding a worker image a Docker-marked test can actually run against.

Three test modules used to answer this question three ways, and two of them got
it wrong in the same direction: they named a *published* tag, so they ran
against whatever was last pulled from Docker Hub instead of the image the
working tree builds. When the host side of the worker protocol changed, those
tests failed as "the job was never claimed", with nothing pointing at the image.

So the preference order lives here, once:

1. the tag CI builds from this checkout (`…:lite-test`, `…:test`),
2. then a published tag, for a dev machine that has pulled one.

A test that finds nothing skips; a test that finds a stale published image may
still fail, but at least every module agrees on which image that is.

**Any notebook image can run C++.** `xeus-cpp` is installed in the Dockerfile's
shared ``common`` stage, so `lite` ships the same `xcpp17/20/23` kernels as
`full` — the variants differ only in the Python ML stack (`full` adds an
nvidia/cuda base plus torch/fastai, 22.9 GB against lite's 6.3 GB). There is
therefore no reason for a C++ test to ask for `full`, and good reason not to:
nothing builds `full` in CI, so such a test only ever ran on a maintainer's
machine.
"""

from __future__ import annotations

#: Notebook-processor images, best first. Every entry ships every kernel CLM
#: configures except Rust (see ``test_cpp_docker_execution.py``), so this list
#: serves Python, C++, C#, Java and TypeScript tests alike.
NOTEBOOK_IMAGE_TAGS = [
    "clm-notebook-processor:lite-test",
    "docker.io/mhoelzl/clm-notebook-processor:lite",
    "docker.io/mhoelzl/clm-notebook-processor:latest",
    "clm-notebook-processor:full",
    "docker.io/mhoelzl/clm-notebook-processor:full",
]

#: DrawIO converter images, best first.
DRAWIO_IMAGE_TAGS = [
    "clm-drawio-converter:test",
    "docker.io/mhoelzl/clm-drawio-converter:latest",
]

#: PlantUML converter images, best first.
PLANTUML_IMAGE_TAGS = [
    "clm-plantuml-converter:test",
    "docker.io/mhoelzl/clm-plantuml-converter:latest",
]


def docker_available() -> bool:
    """True when a Docker daemon answers."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def find_image(tags: list[str]) -> str | None:
    """Return the first tag in ``tags`` present locally, or None.

    Only ever consults the local daemon: ``images.get`` does not pull, so a
    missing image makes a test *skip* rather than silently downloading
    gigabytes mid-test-run.
    """
    try:
        import docker

        client = docker.from_env()
    except Exception:
        return None

    for tag in tags:
        try:
            client.images.get(tag)
            return tag
        except Exception:  # ImageNotFound, or a daemon that went away
            continue
    return None


def find_notebook_image() -> str | None:
    """Return a notebook-processor image tag, or None."""
    return find_image(NOTEBOOK_IMAGE_TAGS)


def find_drawio_image() -> str | None:
    """Return a DrawIO converter image tag, or None."""
    return find_image(DRAWIO_IMAGE_TAGS)


def find_plantuml_image() -> str | None:
    """Return a PlantUML converter image tag, or None."""
    return find_image(PLANTUML_IMAGE_TAGS)
