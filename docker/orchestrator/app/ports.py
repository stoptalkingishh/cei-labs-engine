"""docker/orchestrator/app/ports.py

Allocates host ports for challenge types that need a direct connection
method (SSH, etc.) instead of/alongside HTTP-through-Traefik — currently
just `single-target`. Thread-safe, in-memory (matches the rest of the
orchestrator's state — single replica, see store.py).
"""
import threading


class PortsExhaustedError(Exception):
    pass


class PortAllocator:
    def __init__(self, range_start: int, range_end: int):
        if range_start > range_end:
            raise ValueError("range_start must be <= range_end")
        self.range_start = range_start
        self.range_end = range_end
        self._lock = threading.Lock()
        self._allocated: set[int] = set()

    def allocate(self) -> int:
        with self._lock:
            for port in range(self.range_start, self.range_end + 1):
                if port not in self._allocated:
                    self._allocated.add(port)
                    return port
        raise PortsExhaustedError(f"no free ports in [{self.range_start}, {self.range_end}]")

    def release(self, port: int) -> None:
        with self._lock:
            self._allocated.discard(port)

    def reserve(self, port: int) -> None:
        """Marks a port as taken without allocating a new one — used when
        restoring state for an instance that already has a published port."""
        with self._lock:
            self._allocated.add(port)
