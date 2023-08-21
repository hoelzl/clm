from abc import ABC
from contextlib import AbstractContextManager


class NotifierProxy(ABC):
    def processed_document(self):
        ...

    def wrote_document(self):
        ...

    def completed_document(self):
        ...


class Notifier(AbstractContextManager, ABC):
    pass
