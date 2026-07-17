# Worker drain and recovery acceptance test

Run `scripts/worker-drain-recovery-test.sh` from a Swarm manager with at
least two Ready/Active worker nodes. The harness creates an isolated Alpine
service constrained to workers, drains the worker selected by Swarm, and
requires the task to reach `Running` on a different worker before restoring
the original node to `Active`.

The test fails closed if there is no alternate worker, the task does not
migrate within 120 seconds, or node availability is not restored. An exit
trap restores a drained test node and removes the test service on success,
failure, or interruption. Existing application services are not modified.

Optional environment variables:

- `WORKER_DRAIN_TEST_SERVICE` changes the temporary service name.
- `WORKER_DRAIN_TEST_IMAGE` changes the test image (default `alpine:3.20`).
- `WORKER_DRAIN_TEST_TIMEOUT_SECONDS` changes the convergence timeout.

The expected evidence is the initial `docker node ls`, the drain update,
`docker service ps` showing the old task shut down on the drained worker and
the replacement running elsewhere, the active update, and the final PASS
line naming both nodes.
