FROM ctfd/ctfd:3.8.9@sha256:84aff1e50b6a6f931b5da90c6f5b7c623ba4583217a9b00e9d4c1b7c53b9739f

USER root

# Bakes in the instance-launcher plugin (Phase 4) so participants can launch
# challenge environments from inside the challenge page, the hint-wallet
# plugin (see docs/P0-FIX-LOG-2026-07-23.md) so the Wargames release
# pipeline's signed catalog sync has somewhere public to land, and
# modal-theme, a CSS-only plugin that gives the challenge-detail modal
# subtle, professional styling (see plugins/modal-theme/__init__.py).
COPY plugins/instance-launcher /opt/CTFd/CTFd/plugins/instance-launcher
COPY plugins/wargame-stages /opt/CTFd/CTFd/plugins/wargame-stages
COPY plugins/hint-wallet /opt/CTFd/CTFd/plugins/hint-wallet
COPY plugins/modal-theme /opt/CTFd/CTFd/plugins/modal-theme
COPY plugins/submission-history /opt/CTFd/CTFd/plugins/submission-history
COPY plugins/typed-answer-flags /opt/CTFd/CTFd/plugins/typed-answer-flags

RUN pip install --no-cache-dir -r /opt/CTFd/CTFd/plugins/instance-launcher/requirements.txt \
    && pip install --no-cache-dir -r /opt/CTFd/CTFd/plugins/hint-wallet/requirements.txt \
    && pip install --no-cache-dir -r /opt/CTFd/CTFd/plugins/typed-answer-flags/requirements.txt

COPY docker-entrypoint-wrapper.sh /docker-entrypoint-wrapper.sh
RUN chmod +x /docker-entrypoint-wrapper.sh \
    && chown -R 1001:1001 /opt/CTFd/CTFd/plugins/instance-launcher \
       /opt/CTFd/CTFd/plugins/wargame-stages \
       /opt/CTFd/CTFd/plugins/hint-wallet \
       /opt/CTFd/CTFd/plugins/modal-theme \
       /opt/CTFd/CTFd/plugins/submission-history \
       /opt/CTFd/CTFd/plugins/typed-answer-flags

USER 1001

ENTRYPOINT ["/docker-entrypoint-wrapper.sh"]
