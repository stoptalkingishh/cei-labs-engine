import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import instance_types as it
from app.controller import InstanceController
from app.docker_client import ServiceSpec
from app.ports import PortAllocator
from app.reaper import Reaper, _managed_resource_age_seconds
from app.store import InstanceStore, RangeStore

from .fakes import FakeDockerOrchestratorClient

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def make_reaper(grace_minutes=120, max_lifetime_minutes=None, reservation_timeout_seconds=60):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    range_store = RangeStore()
    ports = PortAllocator(32000, 32767)
    controller = InstanceController(docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, 30, 3)
    reaper = Reaper(
        controller, store, range_store, grace_minutes, interval_seconds=9999,
        max_lifetime_minutes=max_lifetime_minutes,
        reservation_timeout_seconds=reservation_timeout_seconds,
    )
    return reaper, controller, docker, store, range_store


def test_sweep_leaves_fresh_instances_alone():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert reaper.sweep() == 0
    assert store.count() == 1


# ── idle pausing (non-destructive: credentials/flags must survive) ─────────────

def test_sweep_pauses_idle_instance_past_grace_period_without_deleting_it():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # 2 minutes idle, grace is 1
    store.update(record)

    reaped = reaper.sweep()

    assert reaped == 1
    # The record survives a pause -- only the running Docker resources go
    # away. This is the crux of the credential-lifecycle fix: idle timeout
    # must not delete the row that holds the generated credentials/env.
    assert store.count() == 1
    paused = store.get("team-1", "juice")
    assert paused.stopped is True
    assert paused.plan.services[0].env == original_env
    assert docker.services == {}
    assert docker.networks == {}


def test_sweep_only_pauses_the_idle_one():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.get("team-1", "juice").stopped is True
    assert store.get("team-2", "juice").stopped is False


def test_sweep_does_not_repause_an_already_paused_instance():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)

    assert reaper.sweep() == 1
    assert reaper.sweep() == 0  # already stopped -- nothing left to do


def test_paused_instance_resumes_with_identical_credentials():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    original_access = dict(plan.access)
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)
    reaper.sweep()
    assert store.get("team-1", "juice").stopped is True

    resumed_plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})

    assert created is False
    assert resumed_plan.services[0].env == original_env
    assert resumed_plan.access == original_access
    assert store.get("team-1", "juice").stopped is False
    assert len(docker.services) == 2  # target + gateway recreated


def test_relaunch_after_pause_does_rotate_credentials():
    """The one path that's SUPPOSED to change credentials: an explicit
    relaunch/reset, even against a currently-paused instance."""
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)
    reaper.sweep()
    assert store.get("team-1", "juice").stopped is True

    relaunched_plan, created = controller.create_or_get(
        it.WEB_APP, "team-1", "juice", {"image": "img", "env": {"MARKER": "rotated"}}, force_relaunch=True
    )

    assert created is True
    assert store.get("team-1", "juice").stopped is False
    assert relaunched_plan.services[0].env != original_env
    assert relaunched_plan.services[0].env.get("MARKER") == "rotated"


def test_sweep_pauses_range_attacker_but_keeps_shared_network_for_resume():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    record = store.get("team-1", "otw")
    record.last_accessed -= 120
    store.update(record)
    range_record = range_store.get("team-1")
    range_record.last_accessed -= 120  # range itself also idle
    range_store.update(range_record)

    reaper.sweep()

    # Both the target and the shared attacker/gateway are stopped...
    assert docker.services == {}
    assert store.get("team-1", "otw").stopped is True
    assert range_store.get("team-1").stopped is True
    # ...but the range's shared overlay network is NOT torn down on a
    # pause (only on a real teardown_range()) -- targets/attacker resume
    # back onto it later.
    assert "chrange-team-1" in docker.networks


def test_sweep_pauses_idle_range_even_with_no_remaining_targets():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    controller.teardown("team-1", "otw")  # target explicitly deleted, attacker/network remain
    range_record = range_store.get("team-1")
    range_record.last_accessed -= 120
    range_store.update(range_record)

    reaper.sweep()

    paused = range_store.get("team-1")
    assert paused is not None
    assert paused.stopped is True
    assert docker.services == {}


def test_sweep_leaves_active_range_alone_even_if_a_target_was_paused():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})
    record = store.get("team-1", "otw-1")
    record.last_accessed -= 120  # only this target is idle
    store.update(record)
    # range_store last_accessed stays fresh (touched by otw-2's creation)

    reaper.sweep()

    assert store.get("team-1", "otw-1").stopped is True
    assert store.get("team-1", "otw-2").stopped is False
    assert range_store.get("team-1").stopped is False


def test_paused_range_attacker_resumes_with_same_password():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    original_password = range_store.get("team-1").plan.access["ssh_password"]
    range_record = range_store.get("team-1")
    range_record.last_accessed -= 120
    range_store.update(range_record)
    reaper.sweep()
    assert range_store.get("team-1").stopped is True

    resumed_plan, created = controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )

    assert created is False
    assert range_store.get("team-1").stopped is False
    assert resumed_plan.access["ssh_password"] == original_password


# ── shutdown countdown sweeping (also non-destructive) ──────────────────────

def test_sweep_pauses_instance_whose_shutdown_deadline_passed_without_deleting_it():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    controller.schedule_shutdown("team-1", "juice", delay_seconds=30)
    record = store.get("team-1", "juice")
    record.shutdown_at -= 60  # force it into the past
    store.update(record)

    reaped = reaper.sweep()

    assert reaped == 1
    paused = store.get("team-1", "juice")
    assert paused is not None
    assert paused.stopped is True
    assert paused.shutdown_at is None  # countdown cleared, doesn't fire again on resume
    assert paused.plan.services[0].env == original_env


def test_sweep_does_not_touch_instance_with_future_shutdown_deadline():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=9999)

    reaped = reaper.sweep()

    assert reaped == 0
    assert store.get("team-1", "juice") is not None
    assert store.get("team-1", "juice").stopped is False


def test_pending_shutdown_instance_is_not_also_idle_reaped():
    # An instance on a shutdown countdown shouldn't ALSO get caught by the
    # separate idle-grace sweep even if it happens to look idle too.
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # looks idle
    store.update(record)
    controller.schedule_shutdown("team-1", "juice", delay_seconds=9999)  # but shutdown isn't due yet

    reaped = reaper.sweep()

    assert reaped == 0
    assert store.get("team-1", "juice") is not None


# ── absolute lifetime (still real, destructive expiration) ─────────────────

def test_sweep_enforces_absolute_lifetime_even_when_instance_is_active():
    reaper, controller, docker, store, _ = make_reaper(
        grace_minutes=120, max_lifetime_minutes=1
    )
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.created_at -= 120
    store.put(record)

    assert reaper.sweep() == 1
    assert store.get("team-1", "juice") is None
    assert docker.services == {}


def test_sweep_absolute_lifetime_also_destroys_an_already_paused_instance():
    """Absolute lifetime is a real, one-way expiration -- it must still fire
    (and actually delete the row/credentials) even for an instance that's
    currently paused, so credentials don't become permanently sticky."""
    reaper, controller, docker, store, _ = make_reaper(
        grace_minutes=1, max_lifetime_minutes=2
    )
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # idle -> gets paused first
    store.update(record)
    assert reaper.sweep() == 1
    assert store.get("team-1", "juice").stopped is True

    record = store.get("team-1", "juice")
    record.created_at -= 180  # now also past the absolute lifetime ceiling
    store.put(record)

    assert reaper.sweep() == 1
    assert store.get("team-1", "juice") is None


def test_sweep_releases_a_stale_reservation_then_reconciles_its_orphans_next_sweep():
    """A reservation is only ever abandoned on the strength of its age, and
    the orphan sweep now runs BEFORE that release (see reaper.sweep()) --
    otherwise dropping a reservation would itself be what made the same
    sweep's pending_count() check read 0 and delete the resources a
    still-running creation was in the middle of making. So a genuinely
    crashed worker's leftovers are reclaimed one sweep later, which is the
    price of never mistaking a slow launch for a dead one."""
    reaper, controller, docker, store, _ = make_reaper(reservation_timeout_seconds=10)
    assert store.reserve("team-1", "crashed")
    store._conn().execute(
        "UPDATE instances SET created_at = created_at - 60 WHERE owner_id = ? AND instance_key = ?",
        ("team-1", "crashed"),
    )
    port = controller.port_allocator.allocate()
    orphan = ServiceSpec(
        name="orphan-service",
        image="img",
        networks=["orphan-network"],
        published_ports=[(port, 22)],
    )
    docker.services[orphan.name] = orphan
    docker.networks["orphan-network"] = True
    # Well past the creation timeout, so the age guard can't excuse either.
    docker.resource_created_at[orphan.name] = time.time() - 3600
    docker.resource_created_at["orphan-network"] = time.time() - 3600

    assert reaper.sweep() == 1  # the reservation only
    assert store.pending_count() == 0
    # Now nothing is in flight, so the leftovers are unambiguously orphaned.
    assert reaper.sweep() == 2
    assert docker.services == {}
    assert docker.networks == {}
    assert controller.port_allocator.allocate() == port


def _iso(epoch: float, fraction: str = "", offset_hours: int = 0) -> str:
    """RFC3339 spelling of `epoch`, optionally with a nanosecond-ish
    fraction and a non-UTC offset, the way the Docker Engine API formats
    `CreatedAt`/`Created`."""
    tz = timezone(timedelta(hours=offset_hours))
    moment = datetime.fromtimestamp(epoch, tz=tz)
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S") + fraction
    if not offset_hours:
        return stamp + "Z"
    return stamp + moment.strftime("%z")[:3] + ":" + moment.strftime("%z")[3:]


def test_managed_resource_age_reads_dockers_nanosecond_rfc3339_timestamps():
    # The orphan guard's entire behaviour hinges on this parse succeeding:
    # an unreadable timestamp is reported as "unknown age", which the guard
    # treats as "too young to touch", so a parse failure would silently
    # disable orphan cleanup across the whole deployment rather than raise.
    # Docker really does emit nanosecond fractions here.
    two_hours_ago = time.time() - 7200

    def age_of(raw):
        return _managed_resource_age_seconds(SimpleNamespace(name="x", attrs={"CreatedAt": raw}))

    # Nanosecond fraction (what Docker's Engine API actually emits), and the
    # shorter/no fraction spellings a differently-configured daemon might.
    assert abs(age_of(f"{_iso(two_hours_ago, '.123456789')}") - 7200) < 1
    assert abs(age_of(f"{_iso(two_hours_ago, '.123456')}") - 7200) < 1
    assert abs(age_of(f"{_iso(two_hours_ago, '.5')}") - 7200) < 1
    assert abs(age_of(f"{_iso(two_hours_ago, '')}") - 7200) < 1
    # A non-UTC offset must not be silently read as UTC, or every resource
    # would look hours newer or older than it is.
    assert abs(age_of(_iso(two_hours_ago, "", offset_hours=2)) - 7200) < 1
    assert abs(age_of(_iso(two_hours_ago, "", offset_hours=-5)) - 7200) < 1

    # Networks report "Created" rather than "CreatedAt".
    assert abs(
        _managed_resource_age_seconds(
            SimpleNamespace(name="x", attrs={"Created": f"{_iso(two_hours_ago, '.000000000')}"})
        ) - 7200
    ) < 1

    # And an unrecognised shape is reported as unknown, not as "old".
    assert _managed_resource_age_seconds(SimpleNamespace(name="x", attrs={})) is None
    assert _managed_resource_age_seconds(SimpleNamespace(name="x", attrs={"CreatedAt": "nope"})) is None
    assert _managed_resource_age_seconds(SimpleNamespace(name="x")) is None


def test_sweep_does_not_reconcile_orphans_during_live_reservation():
    reaper, _, docker, store, _ = make_reaper(reservation_timeout_seconds=60)
    assert store.reserve("team-1", "creating")
    in_flight = ServiceSpec(name="in-flight-service", image="img", networks=["in-flight-network"])
    docker.services[in_flight.name] = in_flight
    docker.networks["in-flight-network"] = True

    assert reaper.sweep() == 0
    assert in_flight.name in docker.services
    assert "in-flight-network" in docker.networks


def test_sweep_keeps_resources_too_young_to_have_been_orphaned():
    """Second guard behind the pending_count() check: a managed resource
    that Docker says was created moments ago belongs to a creation that may
    still be in flight, so it is left for a later sweep rather than deleted
    out from under whoever is still building it."""
    reaper, _, docker, store, _ = make_reaper(reservation_timeout_seconds=300)
    fresh = ServiceSpec(name="fresh-service", image="img", networks=["fresh-network"])
    docker.services[fresh.name] = fresh
    docker.networks["fresh-network"] = True
    docker.resource_created_at[fresh.name] = time.time()
    docker.resource_created_at["fresh-network"] = time.time()

    assert reaper.sweep() == 0
    assert fresh.name in docker.services
    assert "fresh-network" in docker.networks

    # Once they're older than the creation timeout they're fair game.
    docker.resource_created_at[fresh.name] = time.time() - 3600
    docker.resource_created_at["fresh-network"] = time.time() - 3600
    assert reaper.sweep() == 2
    assert docker.services == {}
    assert docker.networks == {}


def test_sweep_never_releases_a_reservation_whose_containers_are_still_being_created():
    """The headline bug: a launch slow enough (a big image pull) to outlive
    RESERVATION_TIMEOUT_SECONDS used to lose its reservation row, and the
    orphan sweep on the very next pass -- now seeing pending_count() == 0
    because the row was gone -- deleted the containers and network the
    launch was still actively creating. CTFd had already shown the
    participant a URL by then. The reservation here is deliberately aged
    well past the timeout, and the timeout itself is set to 0 so that no
    age-based reasoning whatsoever can save it: the only thing protecting it
    is that its Docker resources are provably still there."""

    class SlowCreateDocker(FakeDockerOrchestratorClient):
        def __init__(self):
            super().__init__()
            self.first_service_created = threading.Event()
            self.allow_creation_to_finish = threading.Event()

        def create_service(self, spec):
            result = super().create_service(spec)
            # Block on the *first* service, once it is already live in
            # Docker: that live service is the evidence the reaper uses to
            # tell this creation apart from a crashed worker.
            if len(self.create_calls) == 1:
                self.first_service_created.set()
                assert self.allow_creation_to_finish.wait(timeout=10)
            return result

    outcome = {}

    def launch():
        try:
            outcome["result"] = controller.create_or_get(
                it.WEB_APP, "team-1", "juice", {"image": "img"}
            )
        except Exception as exc:  # pragma: no cover - surfaced via outcome
            outcome["error"] = exc

    # A real on-disk db, not the ":memory:" default every other test here
    # uses: the stores hand each thread its own sqlite3 connection, and
    # ":memory:" would silently hand this one a private, empty database
    # instead of the reservation the reaper is about to look at.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "in-flight.db")
        docker = SlowCreateDocker()
        store = InstanceStore(db_path=db_path)
        range_store = RangeStore(db_path=db_path)
        ports = PortAllocator(32000, 32767, db_path=db_path)
        controller = InstanceController(
            docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, 30, 3
        )
        reaper = Reaper(
            controller, store, range_store, grace_minutes=120, interval_seconds=9999,
            reservation_timeout_seconds=0,
        )

        launcher = threading.Thread(target=launch)
        launcher.start()
        assert docker.first_service_created.wait(timeout=5), outcome.get("error")
        store._conn().execute(
            "UPDATE instances SET created_at = created_at - 3600 WHERE owner_id = ? AND instance_key = ?",
            ("team-1", "juice"),
        )

        reaped = reaper.sweep()

        assert reaped == 0
        assert store.reservation_pending("team-1", "juice") is True

        docker.allow_creation_to_finish.set()
        launcher.join(timeout=10)
        assert not launcher.is_alive()

        # The creation completed normally: the store row is real and the
        # container the participant was already given a URL for is still
        # there.
        assert "error" not in outcome, outcome.get("error")
        plan, created = outcome["result"]
        assert created is True
        record = store.get("team-1", "juice")
        assert record is not None
        assert record.plan.access == plan.access
        assert set(docker.services) == {svc.name for svc in plan.services}
        assert plan.network in docker.networks
        for resource in (store, range_store, ports):
            resource.close()


def test_sweep_keeps_a_target_reservation_while_its_owner_range_is_still_being_created():
    """The target-attacker sibling of the test above, and the gap it left:
    a target's own resources are all created *after* the shared per-owner
    range (controller._create_range_target builds the range attacker first,
    the target last), so during a slow range creation -- a slow image pull on
    the shared attacker, say -- the instance row is pending with none of its
    own resources live, even though the launch demonstrably cannot have
    finished. reservation_resource_names() never mentions the shared range
    attacker, so age alone used to release this row: finalize() then raised
    ReservationLostError and CTFd reported 503 for a launch still under way,
    leaving the target briefly orphaned. The range reservation being
    protected -- its attacker is live -- is the evidence that has to carry
    the instance row with it. timeout is 0 and the row is aged an hour, so
    nothing but that evidence can save it."""

    class SlowRangeDocker(FakeDockerOrchestratorClient):
        def __init__(self):
            super().__init__()
            self.range_attacker_created = threading.Event()
            self.allow_creation_to_finish = threading.Event()

        def create_service(self, spec):
            result = super().create_service(spec)
            # The *first* service of a target-attacker launch is the shared
            # range attacker, and it is live in Docker the moment this
            # returns -- exactly the evidence the reaper should key on. The
            # gateway (and everything the target itself needs) comes after.
            if len(self.create_calls) == 1:
                self.range_attacker_created.set()
                assert self.allow_creation_to_finish.wait(timeout=10)
            return result

    outcome = {}

    def launch():
        try:
            outcome["result"] = controller.create_or_get(
                it.TARGET_ATTACKER, "team-1", "otw",
                {"target_image": "t", "attacker_image": "k"},
            )
        except Exception as exc:  # pragma: no cover - surfaced via outcome
            outcome["error"] = exc

    # A real on-disk db for the same reason as above: the stores hand each
    # thread its own sqlite3 connection, and ":memory:" would silently hand
    # this one a private, empty database instead of the reservation the
    # reaper is about to look at.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "range-in-flight.db")
        docker = SlowRangeDocker()
        store = InstanceStore(db_path=db_path)
        range_store = RangeStore(db_path=db_path)
        ports = PortAllocator(32000, 32767, db_path=db_path)
        controller = InstanceController(
            docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, 30, 3
        )
        reaper = Reaper(
            controller, store, range_store, grace_minutes=120, interval_seconds=9999,
            reservation_timeout_seconds=0,
        )

        launcher = threading.Thread(target=launch)
        launcher.start()
        assert docker.range_attacker_created.wait(timeout=5), outcome.get("error")

        # Age the *instance* row past the timeout. Its own resources -- the
        # instance service, its gateway, its network, the range target -- are
        # all still ahead of it; only the shared range attacker exists.
        store._conn().execute(
            "UPDATE instances SET created_at = created_at - 3600 WHERE owner_id = ? AND instance_key = ?",
            ("team-1", "otw"),
        )

        reaped = reaper.sweep()

        assert reaped == 0
        assert store.reservation_pending("team-1", "otw") is True
        assert range_store.reservation_pending("team-1") is True

        docker.allow_creation_to_finish.set()
        launcher.join(timeout=10)
        assert not launcher.is_alive()

        # The launch completed normally: its reservation survived the sweep,
        # so finalize() found its row and the target the participant was
        # already given a URL for is still there.
        assert "error" not in outcome, outcome.get("error")
        plan, created = outcome["result"]
        assert created is True
        record = store.get("team-1", "otw")
        assert record is not None
        assert record.plan.range_owner_id == "team-1"
        # The target shares the range network (plan.network is None) -- the
        # range attacker and the target both exist side by side now.
        assert "chrange-team-1" in docker.networks
        assert {svc.name for svc in plan.services} <= set(docker.services)
        for resource in (store, range_store, ports):
            resource.close()
