"""Main entry point for notebook processor.

SQLite-based worker that polls job queue and processes notebooks.
RabbitMQ support has been removed in favor of SQLite orchestration.
"""

from clx.workers.notebook.notebook_worker import main

if __name__ == "__main__":
    main()
