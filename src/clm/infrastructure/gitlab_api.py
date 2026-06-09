"""Minimal GitLab REST helper for release provisioning (issue #294).

CLM's only remote-API need today is sharing a per-cohort release repo into a
GitLab access group (``clm release provision``), so this module deliberately
implements exactly that — no project creation, no general client. Repos are
still created the established way (push-to-create or the GitLab UI); the
share is what used to be a manual, per-cohort UI step.

Authentication: a personal/group access token with ``api`` scope, read from
``CLM_GITLAB_TOKEN`` (preferred) or ``GITLAB_TOKEN``. Calls are made with the
``PRIVATE-TOKEN`` header against the API host derived from the channel's
remote URL, so multi-host setups work without configuration.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

TOKEN_ENV_VARS = ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN")

# GitLab numeric access levels for the share endpoint.
ACCESS_LEVELS = {
    "guest": 10,
    "reporter": 20,
    "developer": 30,
    "maintainer": 40,
}

_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTP_REMOTE = re.compile(r"^(?P<base>https?://[^/]+)/(?P<path>.+?)(?:\.git)?/?$")


class GitLabApiError(Exception):
    """A provisioning API call failed; the message is user-presentable."""


def gitlab_token() -> str | None:
    """The configured GitLab API token, or ``None`` when not set."""
    for var in TOKEN_ENV_VARS:
        token = os.environ.get(var, "").strip()
        if token:
            return token
    return None


def parse_gitlab_remote(remote_url: str) -> tuple[str, str] | None:
    """Split a git remote URL into ``(api base URL, project path)``.

    Handles ``https://host/group/sub/repo[.git]`` and
    ``git@host:group/sub/repo[.git]`` forms; SSH remotes are mapped onto an
    ``https://host`` API base. Returns ``None`` for anything else (including
    local paths), which callers treat as "not provisionable".
    """
    if not remote_url:
        return None
    match = _HTTP_REMOTE.match(remote_url)
    if match:
        path = match.group("path")
        if "/" not in path:
            return None
        return match.group("base"), path
    match = _SSH_REMOTE.match(remote_url)
    if match:
        path = match.group("path")
        if "/" not in path:
            return None
        return f"https://{match.group('host')}", path
    return None


def _request(
    method: str, url: str, token: str, *, data: dict[str, object] | None = None
) -> httpx.Response:
    try:
        response = httpx.request(
            method,
            url,
            headers={"PRIVATE-TOKEN": token},
            data=data,
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise GitLabApiError(f"GitLab API request failed: {e}") from e
    return response


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:200]
    return str(payload.get("message", payload))


def share_project_with_group(
    base_url: str,
    project_path: str,
    group_path: str,
    access: str,
    token: str,
) -> str:
    """Share *project_path* into *group_path* at *access* level.

    Returns ``"shared"`` on success or ``"already-shared"`` when the share
    already exists (idempotent re-provisioning). Raises :class:`GitLabApiError`
    with an actionable message on any other failure (unknown group, missing
    project, insufficient token scope, ...).
    """
    level = ACCESS_LEVELS.get(access)
    if level is None:
        raise GitLabApiError(
            f"Unknown access level {access!r}; valid: {', '.join(sorted(ACCESS_LEVELS))}."
        )

    group_url = f"{base_url}/api/v4/groups/{quote(group_path, safe='')}"
    group_response = _request("GET", group_url, token)
    if group_response.status_code == 404:
        raise GitLabApiError(
            f"Group {group_path!r} not found on {base_url} (or the token cannot see it)."
        )
    if group_response.status_code != 200:
        raise GitLabApiError(
            f"Looking up group {group_path!r} failed with HTTP "
            f"{group_response.status_code}: {_error_detail(group_response)}"
        )
    group_id = group_response.json()["id"]

    share_url = f"{base_url}/api/v4/projects/{quote(project_path, safe='')}/share"
    share_response = _request(
        "POST", share_url, token, data={"group_id": group_id, "group_access": level}
    )
    if share_response.status_code in (200, 201):
        return "shared"
    detail = _error_detail(share_response)
    # GitLab signals an existing share as 409 Conflict; some versions answer
    # 400 with a "group_id ... already been taken"-style message instead.
    if share_response.status_code == 409 or (
        share_response.status_code == 400 and "already" in detail.lower()
    ):
        return "already-shared"
    if share_response.status_code == 404:
        raise GitLabApiError(
            f"Project {project_path!r} not found on {base_url}. Create the repo "
            f"first (push it via `clm git init`/`clm git sync --channel`, or in "
            f"the GitLab UI), then re-run provision."
        )
    raise GitLabApiError(
        f"Sharing {project_path!r} with {group_path!r} failed with HTTP "
        f"{share_response.status_code}: {detail}"
    )
