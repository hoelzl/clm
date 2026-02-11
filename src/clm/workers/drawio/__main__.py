"""Main entry point for DrawIO converter.

SQLite-based worker that polls job queue and converts DrawIO diagrams.
RabbitMQ support has been removed in favor of SQLite orchestration.
"""

from clm.workers.drawio.drawio_worker import main

if __name__ == "__main__":
    main()
