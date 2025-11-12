"""Main entry point for notebook processor.

Supports both RabbitMQ mode (legacy) and SQLite worker mode (new).
Mode is selected via USE_SQLITE_QUEUE environment variable.
"""

import os
import sys

USE_SQLITE = os.getenv('USE_SQLITE_QUEUE', 'false').lower() == 'true'

if USE_SQLITE:
    # SQLite worker mode
    from nb.notebook_worker import main
    main()
else:
    # RabbitMQ mode (existing)
    import asyncio
    from nb.notebook_server import app
    asyncio.run(app.run())
