import base64
import traceback
from unittest.mock import MagicMock

from clx_common.messaging.base_classes import ImageResult, ProcessingError
from clx_common.messaging.notebook_classes import NotebookResult
from clx_faststream_backend.correlation_ids import (
    clear_correlation_ids,
    correlation_ids,
    new_correlation_id,
)
from clx_faststream_backend.faststream_backend import (
    clear_handler_errors,
    handle_image,
    handle_notebook,
    handler_errors,
)


def test_clear_handler_errors():
    handler_errors.append(("An error has occurred", ""))
    assert handler_errors

    clear_handler_errors()
    assert handler_errors == []


def create_message_mock():
    clear_handler_errors()
    clear_correlation_ids()
    correlation_id = new_correlation_id()
    assert len(correlation_ids) == 1
    message_mock = MagicMock()
    message_mock.correlation_id = correlation_id
    return message_mock


async def test_handle_image(tmp_path):
    output_file = tmp_path / "test.png"
    assert not output_file.exists()
    raw_data = b"1234567890" * 10
    encoded_data = base64.b64encode(raw_data)
    result = ImageResult(result=encoded_data, output_file=output_file)
    message_mock = create_message_mock()

    await handle_image(result, message_mock)

    assert output_file.exists()
    assert output_file.read_bytes() == raw_data
    assert len(handler_errors) == 0
    assert len(correlation_ids) == 0


async def test_handle_image_with_error():
    error_message = "An error has occurred"
    processing_error = ProcessingError(
        error=error_message, traceback=traceback.format_exc()
    )
    message_mock = create_message_mock()

    await handle_image(processing_error, message_mock)

    assert handler_errors == [error_message]
    assert len(correlation_ids) == 0


async def test_handle_notebook(tmp_path):
    output_file = tmp_path / "test.py"
    assert not output_file.exists()
    data = "# %% [markdown]\nA notebook\n"
    result = NotebookResult(result=data, output_file=output_file)
    message_mock = create_message_mock()

    await handle_notebook(result, message_mock)

    assert output_file.exists()
    assert output_file.read_text() == data
    assert len(handler_errors) == 0
    assert len(correlation_ids) == 0


async def test_handle_notebook_with_error():
    error_message = "An error has occurred"
    processing_error = ProcessingError(error=error_message)
    message_mock = create_message_mock()

    await handle_notebook(processing_error, message_mock)

    assert handler_errors == [error_message]
    assert len(correlation_ids) == 0
