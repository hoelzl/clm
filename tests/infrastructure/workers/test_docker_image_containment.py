"""What the built worker images guarantee (S10 + D7, #798).

The Dockerfile half of this finding had no automated coverage at all: the
containment lives in `USER`, in file modes and in an entrypoint, none of
which any unit test can see. These are the cheapest checks that would
actually fail if it regressed — image metadata and a couple of short
container runs, no build.

The setuid check is the load-bearing one. Draw.io's image makes
``/etc/passwd`` world-writable (Electron throws when the running uid has
no password-database entry, and an arbitrary uid never has one), and that
is only safe because no setuid binary remains to escalate through. An
``apt-get install`` added below the strip would silently re-arm it, and
nothing else in the suite would notice.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.docker_image_helpers import (
    DRAWIO_IMAGE_TAGS,
    NOTEBOOK_IMAGE_TAGS,
    PLANTUML_IMAGE_TAGS,
    docker_available,
    find_image,
)

pytestmark = pytest.mark.docker

_IMAGE_SETS = {
    "notebook": NOTEBOOK_IMAGE_TAGS,
    "plantuml": PLANTUML_IMAGE_TAGS,
    "drawio": DRAWIO_IMAGE_TAGS,
}


def _image_or_skip(kind: str) -> str:
    if not docker_available():
        pytest.skip("Docker daemon not available")
    image = find_image(_IMAGE_SETS[kind])
    if image is None:
        pytest.skip(f"no {kind} image built locally")
    return image


def _run(image: str, script: str, *, user: str = "4242:4242", timeout: int = 180) -> str:
    """Run a shell one-liner in the image and return stdout."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--user", user, "--entrypoint", "sh", image, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"{result.returncode}: {result.stdout}\n{result.stderr}"
    return result.stdout


@pytest.mark.parametrize("kind", sorted(_IMAGE_SETS))
def test_image_runs_as_a_non_root_user(kind: str) -> None:
    """``USER`` is set, and to something other than root.

    Metadata only, so it is nearly free — and a *removed* ``USER`` line is
    the one regression that would not break the build loudly.
    """
    image = _image_or_skip(kind)
    inspected = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    )
    user = inspected[0]["Config"].get("User") or ""
    assert user, f"{image} declares no USER — it would run as root"
    assert not user.startswith("0:"), f"{image} runs as root ({user})"
    assert user == "1000:1000", f"unexpected USER {user!r} in {image}"


@pytest.mark.parametrize("kind", sorted(_IMAGE_SETS))
def test_image_has_no_setuid_binaries(kind: str) -> None:
    """No local privilege-escalation surface inside the container.

    Draw.io's writable ``/etc/passwd`` depends on this; the other two get
    it as ordinary hardening. Run as root deliberately — the check is
    about the files, not about what the worker user can see.
    """
    image = _image_or_skip(kind)
    found = _run(image, "find / -xdev -perm /6000 -type f 2>/dev/null | head -20", user="0:0")
    assert found.strip() == "", f"setuid/setgid binaries survive in {image}:\n{found}"


def test_the_notebook_image_serves_every_kernel_to_an_arbitrary_uid() -> None:
    """Kernelspecs resolve for a uid that exists nowhere in the image.

    Both halves matter: the executor runs the container as the *host* uid
    (1001 on a CI runner, not the image's 1000), and the .NET and Deno
    kernelspecs are installed into root's user directory and copied to the
    system-wide path at build time. A wrong copy would leave those two
    kernels invisible — silently, since a missing kernel only surfaces
    when a deck in that language is built.
    """
    image = _image_or_skip("notebook")
    listing = _run(image, "jupyter kernelspec list 2>&1")
    for kernel in ("python3", "deno", "java", ".net-csharp", "xcpp20"):
        assert kernel in listing, f"{kernel} missing for uid 4242:\n{listing}"


def test_the_notebook_image_home_is_writable_by_an_arbitrary_uid() -> None:
    """A kernel that cannot write ``$HOME`` fails in ways nobody expects."""
    image = _image_or_skip("notebook")
    out = _run(image, 'touch "$HOME/probe" && echo writable')
    assert "writable" in out


def test_the_plantuml_image_renders_as_an_arbitrary_uid() -> None:
    """The JVM path, not just the metadata."""
    image = _image_or_skip("plantuml")
    out = _run(
        image,
        'printf "@startuml\\nA -> B: hi\\n@enduml\\n" > /tmp/d.puml '
        "&& java -jar /app/plantuml.jar -tpng /tmp/d.puml "
        "&& test -f /tmp/d.png && echo rendered",
        timeout=300,
    )
    assert "rendered" in out


def test_the_drawio_entrypoint_starts_under_an_arbitrary_uid() -> None:
    """The entrypoint is the most fragile piece of this change.

    Electron throws in ``os.userInfo()`` when the running uid has no
    password-database entry, and D-Bus refuses to start for one — both
    of which the entrypoint works around at run time, and both of whose
    failure mode is a **hang**, not a message. So drive the real
    entrypoint and require it to reach the worker, which then refuses
    for the expected reason (no jobs DB, no API URL).
    """
    image = _image_or_skip("drawio")
    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "4242:4242", image],
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = result.stdout + result.stderr
    assert "Running DrawIO worker" in combined, combined[-2000:]
    # The worker's own refusal — proof it got past D-Bus, Xvfb and Electron's
    # password-database lookup rather than hanging or dying earlier.
    assert "CLM_JOBS_DB_PATH" in combined, combined[-2000:]
