"""Cooperative cancellation and coalesced progress; no Tk access."""
import threading


class TaskControl:
    def __init__(self):
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._progress = ('准备', 0, None)

    def cancel(self):
        self._cancel.set()

    def check(self):
        if self._cancel.is_set():
            raise InterruptedError('后台任务已取消；原始数据与已提交进度保留。')

    def report(self, stage, completed=0, total=None):
        self.check()
        with self._lock:
            self._progress = (stage, completed, total)

    def snapshot(self):
        with self._lock:
            return self._progress
