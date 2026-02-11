"""Main entry point for PlantUML converter.

SQLite-based worker that polls job queue and converts PlantUML diagrams.
RabbitMQ support has been removed in favor of SQLite orchestration.
"""

from clm.workers.plantuml.plantuml_worker import main

if __name__ == "__main__":
    main()
