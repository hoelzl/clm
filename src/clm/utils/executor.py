import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor


def create_executor():
    if os.name == "nt":
        return ThreadPoolExecutor(max_workers=4)
    else:
        return ProcessPoolExecutor(max_workers=8)
