"""Main entry point for PlantUML converter.

Supports both RabbitMQ mode (legacy) and SQLite worker mode (new).
Mode is selected via USE_SQLITE_QUEUE environment variable.
"""

import os
import sys

USE_SQLITE = os.getenv('USE_SQLITE_QUEUE', 'false').lower() == 'true'

if USE_SQLITE:
    # SQLite worker mode
    from plantuml_converter.plantuml_worker import main
    main()
else:
    # RabbitMQ mode (existing)
    import asyncio
    from plantuml_converter.plantuml_converter import app
    asyncio.run(app.run())
