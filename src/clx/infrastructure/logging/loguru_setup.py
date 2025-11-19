import sys

import requests
from loguru import logger


class LokiSink:
    def __init__(self, loki_url: str, static_labels: dict[str, str]):
        self.loki_url = loki_url
        self.static_labels = static_labels

    def write(self, message):
        record = message.record

        # Prepare labels
        labels = self.static_labels.copy()
        labels.update(
            {
                "level": record["level"].name,
                "file": record["file"].name,
                "function": record["function"],
                "line": str(record["line"]),
                "module": record["module"],
                "process_name": record["process"].name,
                "thread_name": record["thread"].name,
                "correlation_id": record["extra"].get("correlation_id", ""),
            }
        )

        log_entry = {
            "streams": [
                {
                    "stream": {k: str(v) for k, v in labels.items()},
                    "values": [
                        [str(int(record["time"].timestamp() * 1e9)), record["message"]]
                    ],
                }
            ]
        }

        try:
            response = requests.post(self.loki_url, json=log_entry)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Failed to send log to Loki: {e}", file=sys.stderr)


def setup_logger(
    loki_url: str, app_name: str, local_level: str = "WARNING", loki_level: str = "INFO"
):
    # Remove default handler
    logger.remove()

    # Add console handler
    logger.add(
        sys.stderr,
        format="<level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=local_level,
        colorize=True,
    )

    # Add Loki handler
    logger.add(
        LokiSink(loki_url, {"app": app_name}), level=loki_level, format="{message}"
    )

    return logger


# Example usage
if __name__ == "__main__":
    loki_url = "http://localhost:3100/loki/api/v1/push"
    app_name = "my_app"
    logger = setup_logger(loki_url, app_name)

    logger.info("Application started", extra={"correlation_id": "1234"})
    logger.warning("This is a warning message")
    logger.error("This is an error message")

    # Example of adding extra contextual information
    logger.bind(user_id="12345").info("User logged in")
