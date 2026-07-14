#!/usr/bin/env python3
"""Deterministic, stdlib-only lifecycle concurrency test.

Run from a temporary container attached to the orchestrator-internal network.
The orchestrator secret is read from a mounted file and never printed.
"""
import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


class Client:
    def __init__(self, base_url, auth_file):
        self.base_url = base_url.rstrip("/")
        self.auth = Path(auth_file).read_text(encoding="utf-8").strip()

    def call(self, method, path, body=None, timeout=45):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Orchestrator-Auth": self.auth, "Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                payload = json.loads(raw or b"{}")
                return response.status, time.monotonic() - started, payload
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw or b"{}")
            except Exception:
                payload = {"non_json_error": True, "content_type": exc.headers.get("Content-Type", "")}
            return exc.code, time.monotonic() - started, payload
        except Exception as exc:
            return -1, time.monotonic() - started, {"error": type(exc).__name__}


def summarize(results):
    latencies = [round(result[1], 4) for result in results]
    statuses = {}
    non_json = 0
    for status, _elapsed, payload in results:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
        non_json += int(bool(payload.get("non_json_error")))
    return {
        "statuses": statuses,
        "non_json_errors": non_json,
        "latency_seconds": {
            "min": min(latencies),
            "p50": statistics.median(latencies),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies),
        },
    }


def parallel(count, function):
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        return [future.result() for future in concurrent.futures.as_completed(
            [pool.submit(function, index) for index in range(count)]
        )]


def cold_stage(client, count, image, prefix):
    def create(index):
        return client.call("POST", "/instances", {
            "type": "single-target",
            "owner_id": f"{prefix}-cold-{count}-{index}",
            "instance_key": "box",
            "spec": {"image": image},
        })

    results = parallel(count, create)
    summary = summarize(results)
    cleanup = []
    for index in range(count):
        cleanup.append(client.call("DELETE", f"/instances/{prefix}-cold-{count}-{index}/box")[0])
    summary["cleanup_statuses"] = cleanup
    summary["passed"] = summary["statuses"] == {"201": count} and cleanup == [200] * count
    return summary


def identical_create_stage(client, count, image, prefix):
    owner = f"{prefix}-identical"
    client.call("DELETE", f"/instances/{owner}/box")

    def create(_index):
        return client.call("POST", "/instances", {
            "type": "single-target", "owner_id": owner, "instance_key": "box", "spec": {"image": image}
        })

    results = parallel(count, create)
    summary = summarize(results)
    summary["cleanup_status"] = client.call("DELETE", f"/instances/{owner}/box")[0]
    summary["passed"] = (
        summary["statuses"].get("201") == 1
        and summary["statuses"].get("200") == count - 1
        and len(summary["statuses"]) == 2
        and summary["cleanup_status"] == 200
    )
    return summary


def relaunch_stage(client, count, image, prefix):
    owner = f"{prefix}-relaunch"
    client.call("DELETE", f"/instances/{owner}/box")
    initial = client.call("POST", "/instances", {
        "type": "single-target", "owner_id": owner, "instance_key": "box", "spec": {"image": image}
    })

    def relaunch(_index):
        return client.call("POST", "/instances", {
            "type": "single-target",
            "owner_id": owner,
            "instance_key": "box",
            "spec": {"image": image},
            "relaunch": True,
        })

    results = parallel(count, relaunch)
    summary = summarize(results)
    status = client.call("GET", f"/instances/{owner}/box")[0]
    cleanup = client.call("DELETE", f"/instances/{owner}/box")[0]
    summary.update(initial_status=initial[0], final_status=status, cleanup_status=cleanup)
    summary["passed"] = (
        initial[0] == 201
        and summary["statuses"].get("201") == 1
        and summary["statuses"].get("200") == count - 1
        and len(summary["statuses"]) == 2
        and status == 200
        and cleanup == 200
    )
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8080"))
    parser.add_argument("--auth-file", default=os.environ.get("ORCH_AUTH_FILE", "/run/secrets/plugin_shared_secret"))
    parser.add_argument("--image", default=os.environ.get("TARGET_IMAGE", "ghcr.io/stoptalkingishh/cei-labs-engine/target-base-linux:latest"))
    parser.add_argument("--stages", default="1,5,10,20")
    parser.add_argument("--race-count", type=int, default=20)
    parser.add_argument("--relaunch-count", type=int, default=20)
    parser.add_argument("--prefix", default=f"acceptance-{int(time.time())}")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    client = Client(args.url, args.auth_file)
    health = client.call("GET", "/healthz", timeout=5)
    report = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prefix": args.prefix,
        "health_status": health[0],
        "cold": {},
    }
    if health[0] != 200:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1

    for count in [int(value) for value in args.stages.split(",") if value.strip()]:
        print(f"cold stage {count}", flush=True)
        report["cold"][str(count)] = cold_stage(client, count, args.image, args.prefix)
    print(f"identical create stage {args.race_count}", flush=True)
    report["identical_create"] = identical_create_stage(client, args.race_count, args.image, args.prefix)
    print(f"parallel relaunch stage {args.relaunch_count}", flush=True)
    report["parallel_relaunch"] = relaunch_stage(client, args.relaunch_count, args.image, args.prefix)
    report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["passed"] = (
        all(stage["passed"] for stage in report["cold"].values())
        and report["identical_create"]["passed"]
        and report["parallel_relaunch"]["passed"]
    )
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": args.report}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
