import functools
import os
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from clm.utils.config import config


def max_workers():
    cores = os.cpu_count() or 6
    cores = max(cores - config.num_non_worker_cores, 1)
    if sys.platform == "win32":
        # For some reason, having too many workers seems to cause significant
        # slowdowns on Windows, even on machines with many cores.
        return min(config.num_win_workers, cores)
    else:
        return cores


def create_executor(single_threaded: bool = False):
    if single_threaded:
        return ThreadPoolExecutor(max_workers=1)
    # Enabling this removes some warnings but also significantly slows down
    # the execution of the program.
    # elif sys.platform == "win32":
    #     return ThreadPoolExecutor(max_workers=max_workers())
    else:
        return ProcessPoolExecutor(max_workers=max_workers())


def genjobs(func):
    @functools.wraps(func)
    def inner(self, executor, *args, **kwargs):
        # Not using a generator here so that users don't *need* to iterate.
        futures = []
        for job_func, *job_args in func(self, *args, **kwargs):
            futures.append(executor.submit(job_func, *job_args))
        return futures

    return inner
