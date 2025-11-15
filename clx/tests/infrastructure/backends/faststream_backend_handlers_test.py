import base64
from unittest.mock import MagicMock

import pytest

from clx.infrastructure.database.db_operations import DatabaseManager
from clx.infrastructure.messaging.base_classes import ImageResult, ProcessingError
from clx.infrastructure.messaging.notebook_classes import NotebookResult
from clx.infrastructure.messaging.correlation_ids import (
    clear_correlation_ids,
    active_correlation_ids,
    new_correlation_id,
)
from clx.infrastructure.backends.handlers import clear_database_manager, \
    clear_handler_errors, database_manager, handle_image, handle_notebook, \
    handler_errors, set_database_manager


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

@pytest.fixture
def db_manager():
    with DatabaseManager(":memory:") as manager:
        yield manager

class BackendDatabaseManager:
    def __init__(self):
        self.database_manager = DatabaseManager(":memory:")

    def __enter__(self):
        self.database_manager.__enter__()
        set_database_manager(self.database_manager)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        clear_database_manager()
        self.database_manager.__exit__(exc_type, exc_val, exc_tb)

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
        input_file=str(tmp_path / "test.png"),
        data=raw_data,
        content_hash="abcd"
    )

    with BackendDatabaseManager():
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
        input_file=str(tmp_path / "input-file.txt"),
        data=data,
        content_hash="1234",
        output_metadata_tags=("a", "b", "c", "d"),
    )

    with BackendDatabaseManager():
        await handle_notebook(result, message_mock)

    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8") == data
    assert len(handler_errors) == 0
    assert len(active_correlation_ids) == 0


async def test_handle_notebook_with_error(processing_error):
    message_mock = await create_message_mock()

    await handle_notebook(processing_error, message_mock)

    assert handler_errors == [processing_error]
    assert len(active_correlation_ids) == 0
