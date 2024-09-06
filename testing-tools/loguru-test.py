# %%
from clx_common.logging.loguru_setup import setup_logger

# %%
logger = setup_logger(
    "http://localhost:3100/loki/api/v1/push", "your_app_name", local_level="TRACE"
)

# %%
# Now use logger throughout your application
logger.info("Application started")

# %%
# Example usage of different log levels
logger.trace("This is a trace message")
logger.debug("This is a debug message")
logger.info("This is an info message")
logger.warning("This is a warning message")
logger.error("This is an error message")
logger.critical("This is a critical message")

# %%
# Example with extra context
logger.info("User logged in", extra={"user_id": 123})


# %%
# Example of using trace
def complex_calculation(x, y):
    logger.trace(f"Starting calculation with x={x}, y={y}")
    result = x * y
    logger.trace(f"Calculation result: {result}")
    return result


# %%
complex_calculation(5, 7)
