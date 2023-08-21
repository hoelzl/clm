from abc import ABC
from contextlib import AbstractContextManager


class Notifier(ABC):
    def processed_document(self):
        ...

    def wrote_document(self):
        ...

    def completed_document(self):
        ...
