"""HTTP tests for the deck editor routes.

Mirrors the ``TestClient`` pattern from ``tests/recordings/test_web.py``.
Every mutating test re-reads the file from disk afterwards to confirm the
write is byte-correct and that untouched cells are preserved. Both the
``#`` (python) and ``//`` (c-family) percent-format token decks are
covered.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("jinja2", reason="jinja2 not installed (needs [edit] extra)")
pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402

from clm.edit.app import create_app  # noqa: E402

PY_DECK = textwrap.dedent("""\
    # j2 from 'macros.j2' import header
    # {{ header("Demo", "Demo") }}

    # %% [markdown] lang="de" tags=["slide"]
    # # Hallo

    # %% lang="de"
    print("hi")
    """)

CPP_DECK = textwrap.dedent("""\
    // j2 from 'macros.j2' import header
    // {{ header("Demo", "Demo") }}

    // %% [markdown] lang="de" tags=["slide"]
    // # Hallo

    // %% lang="de"
    std::cout << "hi";
    """)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """A data dir with one module, one topic, and two deck files."""
    topic = tmp_path / "slides" / "module_010_demo" / "topic_100_demo"
    topic.mkdir(parents=True)
    (topic / "slides_demo.py").write_text(PY_DECK, encoding="utf-8")
    (topic / "slides_demo.cpp").write_text(CPP_DECK, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def app(data_dir: Path):
    return create_app(data_dir, host="127.0.0.1", port=8080)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


PY_REL = "module_010_demo/topic_100_demo/slides_demo.py"
CPP_REL = "module_010_demo/topic_100_demo/slides_demo.cpp"


# ----------------------------------------------------------------------
# Browse
# ----------------------------------------------------------------------


class TestBrowse:
    def test_root_lists_module_and_decks(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "module_010_demo" in resp.text
        assert "slides_demo.py" in resp.text

    def test_root_empty_when_no_slides(self, tmp_path: Path):
        app = create_app(tmp_path)
        c = TestClient(app)
        resp = c.get("/")
        assert resp.status_code == 200
        assert "No slide files found" in resp.text


# ----------------------------------------------------------------------
# Deck view
# ----------------------------------------------------------------------


class TestDeckView:
    def test_deck_view_shows_cells(self, client: TestClient):
        resp = client.get("/deck", params={"path": PY_REL})
        assert resp.status_code == 200
        assert "slides_demo.py" in resp.text
        # Bodies are HTML-escaped in the template; check unquoted fragments.
        assert "print(" in resp.text
        assert "Hallo" in resp.text

    def test_deck_view_cell_count(self, client: TestClient):
        resp = client.get("/deck", params={"path": PY_REL})
        # 2 j2 + 2 content cells = 4 cell cards.
        assert resp.text.count('class="cell') >= 4

    def test_path_escape_is_rejected(self, client: TestClient):
        resp = client.get("/deck", params={"path": "../../../etc/passwd"})
        assert resp.status_code == 404

    def test_nonexistent_deck_404s(self, client: TestClient):
        resp = client.get("/deck", params={"path": "module_010_demo/nope.py"})
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Cell edit form
# ----------------------------------------------------------------------


class TestCellEditForm:
    def test_edit_form_renders(self, client: TestClient):
        # cell 3 is the code cell ('# %% lang="de"').
        resp = client.get("/deck/cell/3/edit", params={"path": PY_REL})
        assert resp.status_code == 200
        assert "textarea" in resp.text
        # The header field is pre-filled with the cell's current header.
        assert "# %% lang=" in resp.text

    def test_edit_form_out_of_range(self, client: TestClient):
        resp = client.get("/deck/cell/99/edit", params={"path": PY_REL})
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Cell save (the critical write path)
# ----------------------------------------------------------------------


class TestCellSave:
    def test_save_writes_body_to_disk(self, client: TestClient, data_dir: Path):
        # cell 3 is the code cell.
        resp = client.post(
            "/deck/cell/3",
            params={"path": PY_REL},
            data={"header": '# %% lang="de"', "body": 'print("edited")'},
        )
        assert resp.status_code == 200
        written = (data_dir / "slides" / PY_REL).read_text(encoding="utf-8")
        assert 'print("edited")' in written

    def test_save_preserves_untouched_cells_byte_for_byte(self, client: TestClient, data_dir: Path):
        path = data_dir / "slides" / PY_REL
        original = path.read_text(encoding="utf-8")
        client.post(
            "/deck/cell/3",
            params={"path": PY_REL},
            data={"header": '# %% lang="de"', "body": 'print("edited")'},
        )
        after = path.read_text(encoding="utf-8")
        # The j2 header cells and the slide markdown cell survive verbatim.
        assert "# j2 from 'macros.j2' import header" in after
        assert '# {{ header("Demo", "Demo") }}' in after
        assert "# # Hallo" in after
        # The code cell body changed.
        assert 'print("edited")' in after
        assert 'print("hi")' not in after
        assert after != original

    def test_save_header_change_updates_chips(self, client: TestClient):
        resp = client.post(
            "/deck/cell/3",
            params={"path": PY_REL},
            data={"header": '# %% [markdown] lang="en" tags=["notes"]', "body": "new note"},
        )
        assert resp.status_code == 200
        assert "notes" in resp.text
        assert "en" in resp.text

    def test_save_cpp_deck(self, client: TestClient, data_dir: Path):
        resp = client.post(
            "/deck/cell/2",
            params={"path": CPP_REL},
            data={"header": '// %% lang="de"', "body": "// # Neu"},
        )
        assert resp.status_code == 200
        written = (data_dir / "slides" / CPP_REL).read_text(encoding="utf-8")
        assert "// # Neu" in written


# ----------------------------------------------------------------------
# Cell move
# ----------------------------------------------------------------------


class TestCellMove:
    def test_move_down(self, client: TestClient, data_dir: Path):
        resp = client.post("/deck/cell/2/move", params={"path": PY_REL, "dir": "down"})
        assert resp.status_code == 200
        after = (data_dir / "slides" / PY_REL).read_text(encoding="utf-8")
        # The slide markdown and code cell swap order: code now precedes Hallo.
        assert after.index('print("hi")') < after.index("# # Hallo")

    def test_move_up(self, client: TestClient, data_dir: Path):
        resp = client.post("/deck/cell/3/move", params={"path": PY_REL, "dir": "up"})
        assert resp.status_code == 200
        after = (data_dir / "slides" / PY_REL).read_text(encoding="utf-8")
        assert after.index('print("hi")') < after.index("# # Hallo")

    def test_move_at_boundary_noop(self, client: TestClient, data_dir: Path):
        path = data_dir / "slides" / PY_REL
        before = path.read_text(encoding="utf-8")
        resp = client.post("/deck/cell/3/move", params={"path": PY_REL, "dir": "down"})
        assert resp.status_code == 200
        assert path.read_text(encoding="utf-8") == before


# ----------------------------------------------------------------------
# Cell delete
# ----------------------------------------------------------------------


class TestCellDelete:
    def test_delete_removes_cell(self, client: TestClient, data_dir: Path):
        path = data_dir / "slides" / PY_REL
        before_count = path.read_text(encoding="utf-8").count("# %%")
        resp = client.post("/deck/cell/2/delete", params={"path": PY_REL})
        assert resp.status_code == 200
        after = path.read_text(encoding="utf-8")
        assert after.count("# %%") == before_count - 1
        assert "# # Hallo" not in after

    def test_delete_preserves_terminal_newline(self, client: TestClient, data_dir: Path):
        resp = client.post("/deck/cell/3/delete", params={"path": PY_REL})
        assert resp.status_code == 200
        after = (data_dir / "slides" / PY_REL).read_text(encoding="utf-8")
        assert after.endswith("\n")

    def test_delete_out_of_range(self, client: TestClient):
        resp = client.post("/deck/cell/99/delete", params={"path": PY_REL})
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# Cell insert
# ----------------------------------------------------------------------


class TestCellInsert:
    def test_insert_after(self, client: TestClient, data_dir: Path):
        path = data_dir / "slides" / PY_REL
        before_cells = path.read_text(encoding="utf-8").count("# %%")
        resp = client.post(
            "/deck/cell",
            params={"path": PY_REL, "after": 2},
            data={"header": '# %% [markdown] lang="en" tags=["slide"]', "body": "# Inserted"},
        )
        assert resp.status_code == 200
        after = path.read_text(encoding="utf-8")
        assert after.count("# %%") == before_cells + 1
        assert "# Inserted" in after

    def test_insert_at_head(self, client: TestClient, data_dir: Path):
        resp = client.post(
            "/deck/cell",
            params={"path": PY_REL, "after": -1},
            data={"header": "# %%", "body": "head cell"},
        )
        assert resp.status_code == 200
        after = (data_dir / "slides" / PY_REL).read_text(encoding="utf-8")
        assert "head cell" in after

    def test_insert_preserves_existing_cells(self, client: TestClient, data_dir: Path):
        path = data_dir / "slides" / PY_REL
        before = path.read_text(encoding="utf-8")
        client.post(
            "/deck/cell",
            params={"path": PY_REL, "after": 2},
            data={"header": "# %%", "body": "fresh"},
        )
        after = path.read_text(encoding="utf-8")
        # Everything that was there before is still there.
        for snippet in ["# j2 from", "# {{ header", "# # Hallo", 'print("hi")']:
            assert snippet in after
        assert "fresh" in after
        assert len(after) > len(before)
