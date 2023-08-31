from abc import ABC


class Notifier(ABC):
    def processed_data_source(self):
        ...

    def wrote_to_target(self):
        ...

    def completed_processing(self):
        ...
