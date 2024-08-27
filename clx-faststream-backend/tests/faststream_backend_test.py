from pathlib import Path

import pytest

from clx_common.messaging.notebook_classes import NotebookPayload
from clx_faststream_backend.faststream_backend import FastStreamBackend

NOTEBOOK_TEXT = """\
# j2 from 'macros.j2' import header
# {{ header("Test DE", "Test EN") }}

# %% [markdown] lang="en" tags=["subslide"]
#
# ## This is a Heading

# %% [markdown] lang="de" tags=["subslide"]
#
# ## Das ist eine Überschrift

# %%
print("Hello World")
"""


@pytest.mark.broker
async def test_notebook_files_are_processed(tmp_path, mocker):
    payload = NotebookPayload(
        notebook_text=NOTEBOOK_TEXT,
        notebook_path=str(tmp_path / "test_notebook.py"),
        kind="completed",
        prog_lang="python",
        language="en",
        format="code",
        other_files={},
    )
    async with FastStreamBackend() as backend:
        mocker.patch("clx_faststream_backend.faststream_backend.handle_notebook")
        await backend.send_message("notebook-processor", payload)
        await backend.wait_for_completion()

        notebook_path = Path(payload.notebook_path)
        assert notebook_path.exists()
        assert "<b>Test EN</b>" in notebook_path.read_text()