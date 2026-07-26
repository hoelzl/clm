"""Containment tests for the recordings dashboard (finding S3, decision D4).

The dashboard has no login, so "the request reached the socket" used to be the
whole authorization story. Three separate holes followed from that, and each
gets a test here:

1. ``POST /process`` took a path from a form, checked only ``exists()``, and
   handed it to the processing backend — which for Auphonic uploads the file
   to a third party. Any local file, chosen by any page in the browser.
2. ``course_slug`` reached :func:`clm.recordings.state.get_state_path`
   unsanitized, so ``../../../evil`` wrote outside CLM's config directory.
3. Every mutating route was reachable from a cross-origin auto-submitting
   form, and from a rebound DNS name.

Unlike ``test_web.py``, the app here is built **without** a host override, so
these exercise the production default.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from clm.recordings.workflow.directories import ensure_root

#: The dashboard's real default bind; used as the test client's base URL so
#: requests carry a Host the production allowlist accepts.
LOCAL_URL = "http://127.0.0.1:8008"


@pytest.fixture()
def recording_root(tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    ensure_root(root)
    return root


@pytest.fixture()
def app(recording_root: Path):
    """A dashboard app with the **default** security posture and a mock OBS."""
    with patch("clm.recordings.workflow.obs.ObsClient") as MockObs:
        mock_obs = MagicMock()
        mock_obs.connected = True
        mock_obs.connection_state = "connected"
        mock_obs._record_callbacks = []
        mock_obs.on_record_state_changed.side_effect = lambda cb: mock_obs._record_callbacks.append(
            cb
        )
        MockObs.return_value = mock_obs

        from clm.recordings.web.app import create_app

        application = create_app(
            recordings_root=recording_root,
            obs_host="localhost",
            obs_port=4455,
        )
        mock_obs.connect.side_effect = ConnectionError("OBS not running")
        yield application


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app, base_url=LOCAL_URL)


class TestProcessPathContainment:
    """S3 — ``/process`` must refuse a path outside the recordings root."""

    def test_file_outside_the_root_is_refused(self, client: TestClient, tmp_path: Path):
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY", encoding="utf-8")

        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post("/process", data={"raw_path": str(secret)})

        assert response.status_code == 400
        assert "outside the recordings root" in response.text
        submit.assert_not_called()

    def test_traversal_out_of_the_root_is_refused(
        self, client: TestClient, recording_root: Path, tmp_path: Path
    ):
        """The escape is spelled relative to a legitimate directory."""
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY", encoding="utf-8")
        traversal = recording_root / "to-process" / ".." / ".." / "id_rsa"

        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post("/process", data={"raw_path": str(traversal)})

        assert response.status_code == 400
        submit.assert_not_called()

    def test_a_batch_is_refused_whole_if_any_path_escapes(
        self, client: TestClient, recording_root: Path, tmp_path: Path
    ):
        """Nothing at all is submitted when any entry in the batch escapes.

        Validation runs as its own pass for exactly this reason: refusing
        mid-loop would already have started an upload for the entries before
        the bad one, while returning an error that says nothing about it.
        """
        good = recording_root / "to-process" / "lecture--RAW.mkv"
        good.write_bytes(b"video")
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY", encoding="utf-8")

        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post("/process", data={"raw_path": [str(good), str(secret)]})

        assert response.status_code == 400
        submit.assert_not_called()

    def test_a_directory_is_refused(self, client: TestClient, recording_root: Path):
        """A directory resolves and is contained, but is not something to upload."""
        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post(
                "/process", data={"raw_path": str(recording_root / "to-process")}
            )

        assert response.status_code == 400
        assert "not a file" in response.text
        submit.assert_not_called()

    def test_a_symlink_out_of_the_root_is_refused(
        self, client: TestClient, recording_root: Path, tmp_path: Path
    ):
        """Containment is judged after resolution, not on the literal path."""
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY", encoding="utf-8")
        link = recording_root / "to-process" / "innocent--RAW.mkv"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable (Windows without developer mode)")

        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post("/process", data={"raw_path": str(link)})

        assert response.status_code == 400
        submit.assert_not_called()

    def test_a_file_under_the_root_is_still_submitted(
        self, client: TestClient, recording_root: Path
    ):
        """The guard must not break the feature it protects."""
        raw = recording_root / "to-process" / "lecture--RAW.mkv"
        raw.write_bytes(b"video")

        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post("/process", data={"raw_path": str(raw)})

        assert response.status_code == 200
        submit.assert_called_once()
        assert Path(submit.call_args.args[0]) == raw.resolve()

    def test_a_missing_file_is_still_skipped_not_refused(self, client: TestClient):
        """Pre-existing behaviour: a vanished file is a notice, not a 400."""
        with patch("clm.recordings.workflow.job_manager.JobManager.submit_async") as submit:
            response = client.post("/process", data={"raw_path": "no-such-file.mkv"})

        assert response.status_code == 200
        submit.assert_not_called()


class TestDeckIdentityValidation:
    """The ``(course, section, deck)`` triple names path components."""

    @pytest.mark.parametrize(
        "course_slug",
        [
            "../../../evil",
            "..",
            ".",
            "a/b",
            "a\\b",
            "C:evil",
            "nul\x00byte",
            "ctrl\x01char",
            "trailing.",
            "trailing ",
            # Windows device names ignore the extension, so ``NUL.json`` is the
            # null device: the state write succeeds and the data disappears.
            "NUL",
            "nul",
            "COM1",
        ],
    )
    def test_arm_refuses_an_unsafe_course_slug(self, client: TestClient, course_slug: str):
        response = client.post(
            "/arm",
            data={
                "course_slug": course_slug,
                "section_name": "section",
                "deck_name": "deck",
            },
        )
        assert response.status_code == 400

    def test_arm_refuses_a_blank_course_slug(self, client: TestClient):
        response = client.post(
            "/arm",
            data={"course_slug": "  ", "section_name": "section", "deck_name": "deck"},
        )
        assert response.status_code == 400
        assert "must not be empty" in response.text

    def test_state_file_is_never_written_outside_the_config_dir(self, client: TestClient):
        """The concrete escape: ``get_state_path`` joins the slug unsanitized."""
        with patch("clm.recordings.state.save_state") as save:
            response = client.post(
                "/arm",
                data={
                    "course_slug": "../../../evil",
                    "section_name": "section",
                    "deck_name": "deck",
                },
            )
        assert response.status_code == 400
        save.assert_not_called()

    @pytest.mark.parametrize("route", ["/record", "/advance"])
    @pytest.mark.parametrize("field", ["course_slug", "section_name", "deck_name"])
    def test_action_routes_refuse_traversal_in_any_component(
        self, client: TestClient, route: str, field: str
    ):
        data = {"course_slug": "course", "section_name": "section", "deck_name": "deck"}
        data[field] = ".."
        response = client.post(route, data=data)
        assert response.status_code == 400

    def test_take_panel_refuses_traversal(self, client: TestClient):
        """Percent-encoded, so the traversal survives client-side normalization.

        A literal ``..`` segment is collapsed by httpx before the request is
        sent, which would make this test pass with the validation deleted.
        """
        response = client.get("/decks/course/section/%2e%2e/takes")
        assert response.status_code == 400
        assert "deck_name" in response.text

    def test_restore_refuses_traversal(self, client: TestClient):
        response = client.post("/decks/course/section/%2e%2e/takes/1/restore")
        assert response.status_code == 400
        # Pin the *source* of the 400: without this the session's own "no such
        # take" 404 would look like containment.
        assert "deck_name" in response.text

    def test_a_normal_deck_name_still_works(self, client: TestClient):
        """Names with spaces, dots and parentheses are ordinary here."""
        response = client.post(
            "/arm",
            data={
                "course_slug": "python-course",
                "section_name": "Section 1",
                "deck_name": "topic_100_intro (part 1).py",
            },
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "section_name",
        [
            # Verbatim from machine-learning-azav.xml, the default recordings
            # spec: 33 of its 39 section names contain a colon. Validating
            # these as if they were filenames took out the whole Lectures page.
            "Woche 01: Einführung, LLMs und Python in JupyterLite",
            # And this one carries a *slash* — sanitize_file_name replaces it
            # with "_", so it never becomes a separator. Found by driving the
            # dashboard with all 22 section names from the real spec after the
            # colon fix; three hand-picked examples had missed it.
            "Woche 12: Datenanalyse mit pandas (+ ML-/Metrik-Kostprobe)",
            "Week 04: Chatbots in Practice -- CLI, Gradio, Streaming",
            "Woche 02: Python-Setup, Webservices und erste LLM-API",
        ],
    )
    def test_a_real_course_section_name_is_accepted(self, client: TestClient, section_name: str):
        """Section names are titles the user wrote, not slugs.

        They reach disk only through ``sanitize_file_name``, which deletes the
        very characters (``:`` ``?`` ``!`` ``"`` ``'``) that make them look
        unsafe — so judging them as filenames is a false positive, and an
        expensive one.
        """
        response = client.post(
            "/arm",
            data={
                "course_slug": "ml-course-de",
                "section_name": section_name,
                "deck_name": "01 Einführung.py",
            },
        )
        assert response.status_code == 200

    def test_a_colon_is_still_refused_in_the_course_slug(self, client: TestClient):
        """The slug is used as a filename verbatim, so it keeps the strict rule."""
        response = client.post(
            "/arm",
            data={
                "course_slug": "ml:course",
                "section_name": "Woche 01: Einführung",
                "deck_name": "deck",
            },
        )
        assert response.status_code == 400
        assert "course_slug" in response.text

    def test_a_component_that_sanitizes_to_dotdot_is_refused(self, client: TestClient):
        """``":..:"`` looks harmless raw and sanitizes to ``..``.

        The sanitizer deletes colons, so containment has to be judged on the
        sanitized form as well as the raw one.
        """
        response = client.post(
            "/arm",
            data={
                "course_slug": "ml-course-de",
                "section_name": ":..:",
                "deck_name": "deck",
            },
        )
        assert response.status_code == 400
        assert "section_name" in response.text


class TestCrossOriginContainment:
    """D4 — no other page may drive the dashboard."""

    def test_cross_site_form_post_is_refused(self, client: TestClient):
        response = client.post(
            "/disarm",
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert response.status_code == 403

    def test_cross_origin_post_without_fetch_metadata_is_refused(self, client: TestClient):
        response = client.post("/disarm", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_every_unsafe_method_is_guarded(self, client: TestClient, method: str):
        """Pins the SAFE_METHODS complement — not just POST."""
        response = getattr(client, method)("/disarm", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_the_dashboards_own_htmx_post_is_served(self, client: TestClient):
        response = client.post(
            "/disarm",
            headers={"Origin": LOCAL_URL, "Sec-Fetch-Site": "same-origin"},
        )
        # 409 is the session's own "nothing armed" answer — the point is that
        # the request reached the handler at all.
        assert response.status_code in (200, 409)

    def test_reading_the_dashboard_is_never_blocked(self, client: TestClient):
        assert client.get("/").status_code == 200

    def test_a_rebound_dns_name_is_refused(self, app):
        """Origin and Host agree — only the host allowlist catches this."""
        rebound = TestClient(app, base_url="http://evil.example:8008")
        response = rebound.post("/disarm", headers={"Origin": "http://evil.example:8008"})
        assert response.status_code == 400

    def test_an_opted_in_host_is_served(self, recording_root: Path):
        from clm.recordings.web.app import create_app

        with patch("clm.recordings.workflow.obs.ObsClient"):
            application = create_app(
                recordings_root=recording_root,
                allowed_hosts=["box.ts.net"],
            )
        client = TestClient(application, base_url="http://box.ts.net")
        assert client.get("/").status_code == 200

    def test_a_wildcard_host_pattern_is_served(self, recording_root: Path):
        """``*.ts.net`` has to survive the middleware's own normalization."""
        from clm.recordings.web.app import create_app

        with patch("clm.recordings.workflow.obs.ObsClient"):
            application = create_app(
                recordings_root=recording_root,
                allowed_hosts=["*.ts.net"],
            )
        assert TestClient(application, base_url="http://box.ts.net").get("/").status_code == 200
        assert TestClient(application, base_url="http://evilts.net").get("/").status_code == 400

    def test_an_opted_in_origin_can_drive_actions(self, recording_root: Path):
        """``--allowed-origin`` end-to-end, not just through the unit helper."""
        from clm.recordings.web.app import create_app

        with patch("clm.recordings.workflow.obs.ObsClient"):
            application = create_app(
                recordings_root=recording_root,
                allowed_hosts=["box.ts.net"],
                allowed_origins=["https://proxy.example"],
            )
        client = TestClient(application, base_url="http://box.ts.net")

        allowed = client.post("/disarm", headers={"Origin": "https://proxy.example"})
        # 409 is the session's own "nothing armed" answer; either way the
        # request reached the handler, which is what is under test.
        assert allowed.status_code in (200, 409)

        refused = client.post("/disarm", headers={"Origin": "https://other.example"})
        assert refused.status_code == 403

    def test_the_400_body_says_how_to_fix_it(self, app):
        """The user hitting this is looking at a browser, not at the server log."""
        rebound = TestClient(app, base_url="http://evil.example:8008")
        response = rebound.get("/")
        assert response.status_code == 400
        assert "--allowed-host" in response.text
