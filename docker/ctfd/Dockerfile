FROM ctfd/ctfd:3.8.2@sha256:870e396fddf8dd1273252bae5842bda96e45d5ebbe83d1d737ef76c513055619

USER root

# Bakes in the instance-launcher plugin (Phase 4) so participants can launch
# challenge environments from inside the challenge page, and the
# hint-wallet plugin (see docs/P0-FIX-LOG-2026-07-23.md) so the Wargames
# release pipeline's signed catalog sync has somewhere public to land.
COPY plugins/instance-launcher /opt/CTFd/CTFd/plugins/instance-launcher
COPY plugins/wargame-stages /opt/CTFd/CTFd/plugins/wargame-stages
COPY plugins/hint-wallet /opt/CTFd/CTFd/plugins/hint-wallet

RUN pip install --no-cache-dir -r /opt/CTFd/CTFd/plugins/instance-launcher/requirements.txt \
    && pip install --no-cache-dir -r /opt/CTFd/CTFd/plugins/hint-wallet/requirements.txt

COPY docker-entrypoint-wrapper.sh /docker-entrypoint-wrapper.sh
RUN chmod +x /docker-entrypoint-wrapper.sh \
    && chown -R 1001:1001 /opt/CTFd/CTFd/plugins/instance-launcher \
       /opt/CTFd/CTFd/plugins/wargame-stages \
       /opt/CTFd/CTFd/plugins/hint-wallet

USER 1001

ENTRYPOINT ["/docker-entrypoint-wrapper.sh"]
