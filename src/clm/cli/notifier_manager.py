from multiprocessing.managers import BaseManager

from clm.cli.click_notifier import ClickNotifier
from clm.cli.printing_notifier import PrintingNotifier


class NotifierManager(BaseManager):
    pass


NotifierManager.register("PrintingNotifier", PrintingNotifier)
NotifierManager.register("ClickNotifier", ClickNotifier)
