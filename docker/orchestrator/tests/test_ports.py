import os
import tempfile
import threading

import pytest

from app.ports import PortAllocator, PortsExhaustedError


def test_allocate_returns_first_port_in_range():
    allocator = PortAllocator(30000, 30010)
    assert allocator.allocate() == 30000


def test_allocate_never_returns_same_port_twice():
    allocator = PortAllocator(30000, 30002)
    seen = {allocator.allocate(), allocator.allocate(), allocator.allocate()}
    assert seen == {30000, 30001, 30002}


def test_allocate_raises_when_exhausted():
    allocator = PortAllocator(30000, 30001)
    allocator.allocate()
    allocator.allocate()
    with pytest.raises(PortsExhaustedError):
        allocator.allocate()


def test_release_frees_port_for_reuse():
    allocator = PortAllocator(30000, 30000)
    port = allocator.allocate()
    allocator.release(port)
    assert allocator.allocate() == 30000


def test_reserve_prevents_future_allocation():
    allocator = PortAllocator(30000, 30002)
    allocator.reserve(30000)
    assert allocator.allocate() == 30001


def test_invalid_range_rejected():
    with pytest.raises(ValueError):
        PortAllocator(30010, 30000)


def test_file_backed_allocators_never_duplicate_ports_across_workers():
    worker_count = 20
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "ports.db")
        allocators = [PortAllocator(30000, 30019, db_path=db_path) for _ in range(worker_count)]
        results = [None] * worker_count
        barrier = threading.Barrier(worker_count)

        def race(i):
            barrier.wait()
            results[i] = allocators[i].allocate()

        threads = [threading.Thread(target=race, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert set(results) == set(range(30000, 30020))
        for allocator in allocators:
            allocator.close()
