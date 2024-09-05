import asyncio
import logging

CONVERSION_TIMEOUT = 60
NUM_RETRIES = 3

logger = logging.getLogger(__name__)


async def run_subprocess(cmd, correlation_id):
    logger.debug(f"{correlation_id}:Waiting for conversion to complete...")

    current_iteration = 0
    while True:
        current_iteration += 1
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.debug(
            f"{correlation_id}:Communicating with subprocess:"
            f"Iteration {current_iteration}: {process.pid}"
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), CONVERSION_TIMEOUT * 2 ** (current_iteration - 1)
            )
            return process, stdout, stderr
        except Exception as e:
            logger.error(
                f"{correlation_id}:Error while communicating with subprocess:"
                f"iteration {current_iteration}:{e}"
            )
            await try_to_terminate_process(correlation_id, process)
            if current_iteration >= NUM_RETRIES:
                logger.debug(
                    f"{correlation_id}:Max number of iterations exceeded:"
                    f"{current_iteration}")
                e.add_note(
                    f"{correlation_id}:Error while communicating with subprocess:"
                    f"iteration {current_iteration}:{e}"
                )
                raise


async def try_to_terminate_process(correlation_id, process):
    try:
        process.terminate()
        await asyncio.sleep(2.0)
        process.kill()
    except Exception as e:
        logger.debug(f"{correlation_id}:Error while killing subprocess:{e}")
