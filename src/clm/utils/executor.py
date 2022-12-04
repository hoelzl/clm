import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor


def create_executor(single_threaded: bool = False):
    if single_threaded:
        return ThreadPoolExecutor(max_workers=1)
    elif os.name == "nt":
        return ThreadPoolExecutor(max_workers=4)
    else:
        return ProcessPoolExecutor(max_workers=8)
