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
import multiprocessing
import os
import tempfile
import threading

from cryptography.fernet import Fernet

from app import instance_types as it
from app.crypto import CredentialCipher
from app.ports import PortAllocator
from app.store import InstanceStore, RangeStore, ReservationCapacityError

N_WORKERS = 20


def _open_all_sqlite_components(db_path, barrier):
    """Process target: reproduce Gunicorn workers importing together."""
    barrier.wait()
    store = InstanceStore(db_path=db_path)
    range_store = RangeStore(db_path=db_path)
    ports = PortAllocator(30000, 30020, db_path=db_path)
    store.close()
    range_store.close()
    ports.close()


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


def test_per_owner_quota_is_atomic_across_independent_store_objects():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "owner-quota.db")
        stores = [InstanceStore(db_path=db_path) for _ in range(N_WORKERS)]
        results = [None] * N_WORKERS
        barrier = threading.Barrier(N_WORKERS)

        def race(i):
            barrier.wait()
            try:
                results[i] = stores[i].reserve(
                    "race-team",
                    f"box-{i}",
                    max_instances=30,
                    max_instances_per_owner=3,
                )
            except ReservationCapacityError:
                results[i] = "capacity"

        threads = [threading.Thread(target=race, args=(i,)) for i in range(N_WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == 3
        assert results.count("capacity") == N_WORKERS - 3
        assert stores[0].count_for_owner("race-team") == 3
        for store in stores:
            store.close()


def test_relaunch_claim_is_atomic_across_independent_store_objects():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "relaunch.db")
        # Real gunicorn workers each construct their own CredentialCipher
        # from the SAME mounted `credential_encryption_key` secret file, so
        # sharing one cipher (rather than each store defaulting to its own
        # ephemeral random key) reproduces that -- otherwise worker B could
        # never decrypt a plan worker A encrypted, which is a real
        # deployment requirement, not a race.
        cipher = CredentialCipher(Fernet.generate_key())
        stores = [InstanceStore(db_path=db_path, cipher=cipher) for _ in range(N_WORKERS)]
        plan = it.plan_web_app("race-team", "juice", {"image": "img"}, "ctf.local", "challenge-net")
        assert stores[0].reserve("race-team", "juice") is True
        stores[0].finalize("race-team", "juice", plan)
        results = [None] * N_WORKERS
        barrier = threading.Barrier(N_WORKERS)

        def race(i):
            barrier.wait()
            results[i] = stores[i].claim_for_replacement("race-team", "juice")

        threads = [threading.Thread(target=race, args=(i,)) for i in range(N_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(result is not None for result in results) == 1
        assert stores[0].reservation_pending("race-team", "juice") is True
        for store in stores:
            store.close()


def test_components_initialize_cleanly_in_parallel_processes():
    worker_count = 12
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "parallel-startup.db")
        barrier = context.Barrier(worker_count)
        processes = [
            context.Process(target=_open_all_sqlite_components, args=(db_path, barrier))
            for _ in range(worker_count)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)

        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0] * worker_count
