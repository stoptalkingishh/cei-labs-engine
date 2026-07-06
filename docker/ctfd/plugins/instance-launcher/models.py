"""docker/ctfd/plugins/instance-launcher/models.py

A standalone table mapping a CTFd challenge to an orchestrator instance spec.
Deliberately NOT a Challenges subclass (unlike e.g. CTFd's own
dynamic_challenges plugin) — that would require reimplementing the standard
type's full admin create/update UI (flags, hints, tags, files) to avoid
losing those features. Keeping this as an independent table means every
challenge stays a normal "standard" CTFd challenge; this plugin only adds
metadata on the side plus its own admin page for editing it.
"""
from CTFd.models import db


class InstanceChallengeConfig(db.Model):
    __tablename__ = "instance_launcher_configs"

    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True)
    # "web-app" | "single-target" | "target-attacker" — see docker/orchestrator/README.md
    instance_type = db.Column(db.String(32), nullable=False)

    # web-app / single-target
    image = db.Column(db.String(256))
    port = db.Column(db.Integer)

    # target-attacker
    target_image = db.Column(db.String(256))
    attacker_image = db.Column(db.String(256))
    attacker_port = db.Column(db.Integer)

    # Multiple challenges can share one deployment (e.g. a boot2root box with
    # several flags at different privilege levels) by setting the SAME
    # instance_group — see resolved_instance_key(). All configs sharing a
    # group should declare the same instance_type/images; whichever challenge
    # in the group is launched first is what actually creates the container.
    instance_group = db.Column(db.String(128), nullable=True)

    # Per-challenge opt-out of the post-solve auto-shutdown countdown. When
    # several challenges share an instance_group, the countdown only starts
    # once EVERY challenge in the group with shutdown_on_solve=True has been
    # solved by that team — see solve_hook.py.
    shutdown_on_solve = db.Column(db.Boolean, nullable=False, default=True)

    challenge = db.relationship("Challenges", foreign_keys=[challenge_id])

    def resolved_instance_key(self) -> str:
        """The identity used to talk to the orchestrator. Challenges sharing
        an instance_group resolve to the SAME key, so launching any of them
        creates-or-reuses one shared container."""
        if self.instance_group:
            return f"group-{self.instance_group}"
        return f"challenge-{self.challenge_id}"

    def to_orchestrator_spec(self) -> dict:
        if self.instance_type == "target-attacker":
            spec = {"target_image": self.target_image, "attacker_image": self.attacker_image}
            if self.attacker_port:
                spec["attacker_port"] = self.attacker_port
            return spec
        spec = {"image": self.image}
        if self.port:
            spec["port"] = self.port
        return spec
