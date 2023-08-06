import functools
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor


def create_executor(single_threaded: bool = False):
    if single_threaded:
        return ThreadPoolExecutor(max_workers=1)
    elif os.name == 'nt':
        return ThreadPoolExecutor(max_workers=4)
    else:
        return ProcessPoolExecutor(max_workers=8)


def genjobs(func):
    @functools.wraps(func)
    def inner(self, executor, *args, **kwargs):
        # Not using a generator here so that users don't *need* to iterate.
        futures = []
        for job_func, *job_args in func(self, *args, **kwargs):
            futures.append(executor.submit(job_func, *job_args))
        return futures

    return inner
