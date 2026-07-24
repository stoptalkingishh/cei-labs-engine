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
import time

from cryptography.fernet import Fernet

from app import instance_types as it
from app.controller import InstanceController, ShutdownNotPendingError
from app.crypto import CredentialCipher
from app.ports import PortAllocator
from app.store import InstanceStore, RangeStore, ReservationCapacityError

from .fakes import FakeDockerOrchestratorClient

N_WORKERS = 20
BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


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


def test_reaper_pause_does_not_race_a_concurrent_extend_shutdown():
    """Regression test for the reaper/controller race described in
    store.py's transition_stopped()/schedule_shutdown()/extend_shutdown()
    docstrings: reaper.py's background thread (started in main.py, running
    inside the SAME process as every request thread) calls
    controller.pause() out of _sweep_due_shutdowns()/_sweep_idle_instances()
    concurrently with a player's request thread calling
    controller.extend_shutdown() on the identical (owner_id, instance_key)
    row -- e.g. a player extends their shutdown deadline at the exact
    instant the reaper sweeps it as due.

    Before pause()/extend_shutdown() wrapped their read-then-write in a
    BEGIN IMMEDIATE transaction (the same pattern reserve()/
    claim_for_replacement() use above), both sides read the identical
    pre-mutation row via a plain get(), mutated their own Python-side copy,
    and called update() -- three separate auto-committing statements with
    no transaction spanning the read and the write. Whichever update()
    landed last silently clobbered the other's fields. Concretely: if
    extend_shutdown()'s update() (built from a stale get() that still saw
    stopped=False) committed after pause()'s update(), the row would end up
    with stopped=False even though pause() had already torn down the real
    Docker service/network -- store bookkeeping desynced from reality, so a
    later create_or_get()/reboot() would treat a nonexistent container as
    still running.

    This drives many trials of that exact interleaving (a fresh
    barrier-released thread pair per trial, since racy interleavings only
    reproduce probabilistically) and asserts, every time, that the
    persisted `stopped` flag agrees with whether the Docker resources are
    actually still live -- the store can never observably disagree with
    what really happened to Docker -- and that the row never lands in a
    hybrid state no single serialized operation could have produced.
    """
    trials = 40
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "reaper-race.db")
        docker = FakeDockerOrchestratorClient()
        store = InstanceStore(db_path=db_path)
        range_store = RangeStore(db_path=db_path)
        ports = PortAllocator(32000, 32767, db_path=db_path)
        controller = InstanceController(
            docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET,
            max_instances=1000, shutdown_max_extensions=3,
        )

        for i in range(trials):
            owner_id, instance_key = "race-team", f"box-{i}"
            controller.create_or_get(it.WEB_APP, owner_id, instance_key, {"image": "img"})
            controller.schedule_shutdown(owner_id, instance_key, delay_seconds=30)

            barrier = threading.Barrier(2)
            errors = []

            def sweep_pause():
                barrier.wait()
                try:
                    controller.pause(owner_id, instance_key)
                except Exception as exc:  # pragma: no cover - surfaced via errors list
                    errors.append(exc)

            def player_extend():
                barrier.wait()
                try:
                    controller.extend_shutdown(owner_id, instance_key, extend_seconds=300)
                except ShutdownNotPendingError:
                    pass  # correct outcome if pause() won the DB race first
                except Exception as exc:  # pragma: no cover - surfaced via errors list
                    errors.append(exc)

            reaper_thread = threading.Thread(target=sweep_pause)
            request_thread = threading.Thread(target=player_extend)
            reaper_thread.start()
            request_thread.start()
            reaper_thread.join()
            request_thread.join()

            assert not errors, f"trial {i}: unexpected exception(s): {errors}"

            record = store.get(owner_id, instance_key)
            assert record is not None, f"trial {i}: record vanished"

            service_names = {svc.name for svc in record.plan.services}
            docker_live = bool(service_names & set(docker.services.keys()))

            if record.stopped:
                # pause() won: the row must not still claim a live shutdown
                # countdown, and Docker must actually be torn down.
                assert record.shutdown_at is None, f"trial {i}: stopped but shutdown_at survived: {record}"
                assert record.extensions_used == 0, f"trial {i}: stopped but extensions_used survived: {record}"
                assert not docker_live, f"trial {i}: store says stopped but Docker services are still live"
            else:
                # extend_shutdown() alone can never observably win here --
                # pause() unconditionally clears shutdown_at as part of its
                # own atomic transition regardless of commit order, so the
                # only way `stopped` is False afterwards is if pause() never
                # got a chance to run at all. Either way, Docker must still
                # be live and the row internally consistent.
                assert docker_live, f"trial {i}: store says running but Docker services were torn down (lost pause)"
                assert record.shutdown_at is not None and record.shutdown_at > time.time(), (
                    f"trial {i}: running with no live countdown: {record}"
                )

        store.close()
        range_store.close()
        ports.close()
