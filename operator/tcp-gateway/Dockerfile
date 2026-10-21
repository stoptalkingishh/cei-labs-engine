# Pinned to a real digest, not the floating python:3.12-alpine tag. This is
# the single most security-sensitive image in the stack: it is the hardened
# forwarder on the `challenge-edge` network that mediates ALL player access to
# untrusted challenge targets, so the contents of its base layer are part of
# the trust boundary rather than just build reproducibility. An unpinned tag
# meant every rebuild could silently pull different upstream bytes with no
# record of what changed, and no way to tell a legitimate base refresh from a
# compromised publish of the tag. The digest below is the multi-arch manifest
# list index (not a single platform's digest), so it stays correct on both
# amd64 and arm64 hosts.
#
# Unlike operator/kali-novnc, nothing below installs packages from a rolling
# repo -- the image is a bare python:alpine plus this repo's server.py -- so
# pinning the base here pins the entire userland, and there is no
# reproducibility-vs-rolling-packages tradeoff to weigh.
#
# To re-pin, deliberately (not automatically): `docker pull
# python:3.12-alpine` and copy the digest that `docker image inspect
# --format='{{index .RepoDigests 0}}'` reports, or query the registry
# directly: `docker buildx imagetools inspect python:3.12-alpine`. Do not
# hand-edit the digest -- copy the reported one.
FROM python:3.15-alpine@sha256:2699b90f4d562a22e2dd076c82f964032f64814204bff36d271a54ba4cc2c7e2

COPY server.py /server.py

USER 65532:65532
ENTRYPOINT ["python", "/server.py"]
