from multiprocessing import RLock

from clm.core.notifier import Notifier


class PrintingNotifier(Notifier):
    def __init__(self, line_length=72, verbose: bool = True):
        self.line_length = line_length
        self.verbose = verbose
        self.current_position = 0
        self.lock = RLock()

    def _print(self, msg: str):
        with self.lock:
            print(msg, end="")
            self.current_position += len(msg)
            if self.current_position >= self.line_length:
                print(flush=True)
                self.current_position = 0

    def processed_data_source(self):
        if self.verbose:
            self._print("p")

    def wrote_to_target(self):
        if self.verbose:
            self._print("w")

    def completed_processing(self):
        self._print(".")
