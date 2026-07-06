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
