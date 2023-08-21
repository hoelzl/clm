from multiprocessing import RLock

import click

from clm.core.notifier import Notifier


class ClickNotifier(Notifier):
    def __init__(self, line_length=72, verbose: bool = True):
        self.line_length = line_length
        self.verbose = verbose
        self.current_position = 0
        self.lock = RLock()

    def _print(self, msg: str):
        with self.lock:
            click.echo(msg, nl=False)
            self.current_position += len(msg)
            if self.current_position >= self.line_length:
                click.echo()
                self.current_position = 0

    def processed_document(self):
        if self.verbose:
            self._print("p")

    def wrote_document(self):
        if self.verbose:
            self._print("w")

    def completed_document(self):
        self._print(".")
