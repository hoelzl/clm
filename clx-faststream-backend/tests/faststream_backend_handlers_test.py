import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clx_common.messaging.base_classes import ImageResult, ProcessingError
from clx_common.messaging.notebook_classes import NotebookResult
from clx_common.messaging.correlation_ids import (
    clear_correlation_ids,
    active_correlation_ids,
    new_correlation_id,
)
from clx_faststream_backend.faststream_backend import (
    clear_handler_errors,
    handle_image,
    handle_notebook,
    handler_errors,
)


async def test_clear_handler_errors(processing_error):
    handler_errors.append(processing_error)
    assert handler_errors

    await clear_handler_errors()
    assert handler_errors == []


async def create_message_mock():
    await clear_handler_errors()
    await clear_correlation_ids()
    correlation_id = await new_correlation_id()
    assert len(active_correlation_ids) == 1
    message_mock = MagicMock()
    message_mock.correlation_id = correlation_id
    return message_mock


async def test_handle_image(tmp_path):
    output_file = tmp_path / "test.png"
    assert not output_file.exists()
    raw_data = b"1234567890" * 10
    encoded_data = base64.b64encode(raw_data)
    message_mock = await create_message_mock()
    result = ImageResult(
        result=encoded_data,
        correlation_id=message_mock.correlation_id,
        output_file=str(output_file),
    )

    await handle_image(result, message_mock)

    assert output_file.exists()
    assert output_file.read_bytes() == raw_data
    assert len(handler_errors) == 0
    assert len(active_correlation_ids) == 0


@pytest.fixture
def processing_error():
    return ProcessingError(
        error="An error has occurred",
        correlation_id="a-correlation-id",
        input_file="C:/tmp/input-file.txt",
        input_file_name="input-file.txt",
        output_file="C:/tmp/output-file.txt",
        traceback="An error traceback",
    )


async def test_handle_image_with_error(processing_error):
    message_mock = await create_message_mock()

    await handle_image(processing_error, message_mock)

    assert handler_errors == [processing_error]
    assert len(active_correlation_ids) == 0


async def test_handle_notebook(tmp_path):
    output_file = tmp_path / "test.py"
    assert not output_file.exists()
    data = "# %% [markdown]\nA notebook\n"
    message_mock = await create_message_mock()
    result = NotebookResult(
        result=data,
        correlation_id=message_mock.correlation_id,
        output_file=str(output_file),
    )

    await handle_notebook(result, message_mock)

    assert output_file.exists()
    assert output_file.read_text() == data
    assert len(handler_errors) == 0
    assert len(active_correlation_ids) == 0


async def test_handle_notebook_with_error(processing_error):
    message_mock = await create_message_mock()

    await handle_notebook(processing_error, message_mock)

    assert handler_errors == [processing_error]
    assert len(active_correlation_ids) == 0
