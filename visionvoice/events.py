"""In-process publish/subscribe used to stream live updates to the dashboard."""
from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque


class EventBus:
    def __init__(self, keep: int = 200) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.recent: deque[dict] = deque(maxlen=keep)

    def publish(self, kind: str, **data) -> dict:
        ev = {"id": next(self._ids), "kind": kind, "ts": time.time(), **data}
        with self._lock:
            self.recent.append(ev)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:  # slow browser tab: drop it, it reconnects and reloads state
                self.unsubscribe(q)
        return ev

    def subscribe(self, maxsize: int = 500) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def feed(self, kinds: set[str], limit: int = 60) -> list[dict]:
        with self._lock:
            items = [e for e in self.recent if e["kind"] in kinds]
        return items[-limit:]
