"""A bounded single worker: running request + latest pending + latest result."""
from collections import OrderedDict
import threading


class LatestTask:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None
        self._result = None
        self._thread = None
        self._closed = False
        self._wanted = None
        self._cache = OrderedDict()

    def request(self, key, producer):
        with self._lock:
            if self._closed:
                return
            self._wanted = key
            if key in self._cache:
                self._pending = None
                self._result = (key, self._cache[key], None)
                return
            self._pending = (key, producer)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='lap-comparison', daemon=False)
                self._thread.start()

    def _run(self):
        while True:
            with self._lock:
                request, self._pending = self._pending, None
                if self._closed or request is None:
                    self._thread = None
                    return
            key, producer = request
            try:
                value, error = producer(), None
            except Exception as exc:
                value, error = None, exc
            with self._lock:
                if not self._closed:
                    if self._wanted == key:
                        self._result = (key, value, error)
                    if error is None:
                        self._cache[key] = value
                        while len(self._cache) > 4:
                            self._cache.popitem(last=False)

    def take(self):
        with self._lock:
            value, self._result = self._result, None
            return value

    def close(self):
        with self._lock:
            self._closed = True
            self._pending = self._result = None
            self._cache.clear()

    def ready(self):
        with self._lock:
            return self._thread is None
