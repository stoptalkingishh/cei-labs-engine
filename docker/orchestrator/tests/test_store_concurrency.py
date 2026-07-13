"""Regression test for the multi-worker race documented in TRACKER.md and
reproduced live against a real --workers 2 deployment: two gunicorn workers
are two separate OS processes, each with its own Python heap, so a plain
in-memory dict guarded by a threading.Lock gave each worker a completely
independent, blind view of instance state.

This test simulates that scenario without needing real separate processes:
many independent InstanceStore *objects* (standing in for separate workers'
own Python state) pointed at the same on-disk SQLite file, all released
against a barrier so they call reserve() for the identical
(owner_id, instance_key) at the same instant. Exactly one may ever win --
that's the property store.py's module docstring claims and
controller.create_or_get relies on to only ever let one caller touch Docker.
"""
import os
import tempfile
import threading

from app.store import InstanceStore, RangeStore

N_WORKERS = 20


def test_reserve_is_atomic_across_independent_store_objects_sharing_one_file():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")

        # Each "worker" gets its own InstanceStore object -- exactly like
        # each gunicorn worker process would independently construct its
        # own in create_app(). The only thing they share is the file path.
        stores = [InstanceStore(db_path=db_path) for _ in range(N_WORKERS)]
        results = [None] * N_WORKERS
        barrier = threading.Barrier(N_WORKERS)

        def race(i):
            barrier.wait()  # release all N_WORKERS threads at the same instant
            results[i] = stores[i].reserve("race-team", "box1")

        threads = [threading.Thread(target=race, args=(i,)) for i in range(N_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for s in stores:
            s.close()

        assert results.count(True) == 1, f"expected exactly 1 winner, got {results.count(True)}: {results}"
        assert results.count(False) == N_WORKERS - 1


def test_range_reserve_is_also_atomic_across_independent_store_objects():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "ranges.db")
        stores = [RangeStore(db_path=db_path) for _ in range(N_WORKERS)]
        results = [None] * N_WORKERS
        barrier = threading.Barrier(N_WORKERS)

        def race(i):
            barrier.wait()
            results[i] = stores[i].reserve("race-team")

        threads = [threading.Thread(target=race, args=(i,)) for i in range(N_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for s in stores:
            s.close()

        assert results.count(True) == 1, f"expected exactly 1 winner, got {results.count(True)}: {results}"
        assert results.count(False) == N_WORKERS - 1
