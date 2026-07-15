"""Minimal fixed-destination TCP gateway for isolated challenge services."""

import json
import os
import select
import signal
import socket
import threading


BUFFER_SIZE = 64 * 1024
stop_event = threading.Event()
listeners: list[socket.socket] = []


def stop(_signum, _frame):
    stop_event.set()
    for listener in listeners:
        try:
            listener.close()
        except OSError:
            pass


def proxy(client: socket.socket, host: str, port: int) -> None:
    try:
        with client, socket.create_connection((host, port), timeout=10) as upstream:
            client.settimeout(None)
            upstream.settimeout(None)
            sockets = (client, upstream)
            while not stop_event.is_set():
                readable, _, _ = select.select(sockets, [], [], 1)
                for source in readable:
                    data = source.recv(BUFFER_SIZE)
                    if not data:
                        return
                    destination = upstream if source is client else client
                    destination.sendall(data)
    except OSError:
        return


def serve(listen: int, host: str, port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listeners.append(server)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", listen))
    server.listen(128)
    server.settimeout(1)
    while not stop_event.is_set():
        try:
            client, _address = server.accept()
        except TimeoutError:
            continue
        except OSError:
            break
        threading.Thread(target=proxy, args=(client, host, port), daemon=True).start()


def main() -> int:
    forwards = json.loads(os.environ.get("TCP_FORWARDS", "[]"))
    if not isinstance(forwards, list) or not forwards:
        raise SystemExit("TCP_FORWARDS must be a non-empty JSON list")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threads = []
    for forward in forwards:
        thread = threading.Thread(
            target=serve,
            args=(int(forward["listen"]), str(forward["host"]), int(forward["port"])),
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
