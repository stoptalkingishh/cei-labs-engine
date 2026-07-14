"""docker/orchestrator/app/ports.py

Allocates host ports for challenge types that need a direct connection
method (SSH, etc.) instead of/alongside HTTP-through-Traefik.

Production uses the same SQLite file as the instance store so allocation is
atomic across gunicorn worker *processes*. The earlier in-memory set was only
thread-safe inside one process: two workers could both hand out port 32000,
which made otherwise-unrelated cold creates collide in Docker Swarm.
"""
import sqlite3
import threading


class PortsExhaustedError(Exception):
    pass


class PortAllocator:
    def __init__(self, range_start: int, range_end: int, db_path: "str | None" = None):
        if range_start > range_end:
            raise ValueError("range_start must be <= range_end")
        self.range_start = range_start
        self.range_end = range_end
        # A SQLite ':memory:' database is private to each connection, so it
        # cannot coordinate thread-local connections. Keep the lightweight
        # in-process implementation for isolated tests; production supplies
        # a real shared file path.
        self._db_path = None if db_path in (None, ":memory:") else db_path
        self._lock = threading.Lock()
        self._allocated: set[int] = set()
        self._local = threading.local()
        self._open_conns: list[sqlite3.Connection] = []
        self._open_conns_lock = threading.Lock()
        if self._db_path is not None:
            self._conn().execute(
                "CREATE TABLE IF NOT EXISTS allocated_ports (port INTEGER PRIMARY KEY)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
            with self._open_conns_lock:
                self._open_conns.append(conn)
        return conn

    def close(self) -> None:
        with self._open_conns_lock:
            for conn in self._open_conns:
                conn.close()
            self._open_conns.clear()

    def allocate(self) -> int:
        if self._db_path is not None:
            conn = self._conn()
            for port in range(self.range_start, self.range_end + 1):
                cursor = conn.execute("INSERT OR IGNORE INTO allocated_ports (port) VALUES (?)", (port,))
                if cursor.rowcount == 1:
                    return port
            raise PortsExhaustedError(f"no free ports in [{self.range_start}, {self.range_end}]")
        with self._lock:
            for port in range(self.range_start, self.range_end + 1):
                if port not in self._allocated:
                    self._allocated.add(port)
                    return port
        raise PortsExhaustedError(f"no free ports in [{self.range_start}, {self.range_end}]")

    def release(self, port: int) -> None:
        if self._db_path is not None:
            self._conn().execute("DELETE FROM allocated_ports WHERE port = ?", (port,))
            return
        with self._lock:
            self._allocated.discard(port)

    def reserve(self, port: int) -> None:
        """Marks a port as taken without allocating a new one — used when
        restoring state for an instance that already has a published port."""
        if self._db_path is not None:
            self._conn().execute("INSERT OR IGNORE INTO allocated_ports (port) VALUES (?)", (port,))
            return
        with self._lock:
            self._allocated.add(port)
