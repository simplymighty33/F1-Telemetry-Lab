"""Cooperative one-shot operations. Worker code never touches Qt objects."""
import threading

from storage.task_control import TaskControl


class Operation:
    def __init__(self):
        self.control = TaskControl()
        self._lock = threading.Lock()
        self._thread = None
        self._result = None
        self._closed = False
        self.cancelled = threading.Event()

    def start(self, key, producer):
        if self._closed or not self.ready():
            return False
        self.control = TaskControl()
        self.cancelled.clear()
        self._thread = threading.Thread(target=self._run, args=(key, producer),
                                        name='qt-operation', daemon=False)
        self._thread.start()
        return True

    def _run(self, key, producer):
        try:
            value, error = producer(self.control, self.cancelled), None
        except Exception as exc:
            value, error = None, exc
        with self._lock:
            self._result = key, value, error

    def take(self):
        with self._lock:
            result, self._result = self._result, None
            return result

    def ready(self):
        return self._thread is None or not self._thread.is_alive()

    def cancel(self):
        self.cancelled.set()
        self.control.cancel()

    def close(self):
        self._closed = True
        self.cancel()
