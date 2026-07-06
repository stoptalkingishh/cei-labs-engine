FROM ctfd/ctfd:3.8.2

USER root

# Bakes in the instance-launcher plugin (Phase 4) so participants can launch
# challenge environments from inside the challenge page.
COPY plugins/instance-launcher /opt/CTFd/CTFd/plugins/instance-launcher

RUN pip install --no-cache-dir -r /opt/CTFd/CTFd/plugins/instance-launcher/requirements.txt

COPY docker-entrypoint-wrapper.sh /docker-entrypoint-wrapper.sh
RUN chmod +x /docker-entrypoint-wrapper.sh \
    && chown -R 1001:1001 /opt/CTFd/CTFd/plugins/instance-launcher

USER 1001

ENTRYPOINT ["/docker-entrypoint-wrapper.sh"]
