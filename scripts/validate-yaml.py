#!/usr/bin/env python3
"""Parse every YAML file in the repository and report the ones that fail.

Run from the repository root or from anywhere else: the walk is rooted at the
repository root derived from this file's own location.

Exits non-zero when any file fails to parse, and also when *zero* YAML files
are found. An empty scan is a broken scan, not a passing one -- this job once
reported "all YAML configurations parsed cleanly" while checking nothing at
all, because the skip-hidden-directories guard tested the components of
os.walk's root string: the walk starts at ".", so every root -- on every
platform, whatever ``os.sep`` -- contains a component starting with the
literal "." and every directory was pruned, hidden or not. The count is
printed on every run so that the next regression of that kind is visible
in the log.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_SUFFIXES = (".yml", ".yaml")
PRUNED_DIRECTORIES = ("__pycache__", "node_modules")
# Hidden directories hold VCS/tooling state (.git, .venv, ...) rather than
# repository configuration -- but .github/ is where every CI workflow lives
# and is the whole point of this job, so it is never pruned. Pruning it would
# silently drop all of .github/workflows/ and still report success.
KEPT_HIDDEN_DIRECTORIES = (".github",)


def is_pruned(name: str) -> bool:
    """Return True for directories that never hold repository YAML."""
    if name in KEPT_HIDDEN_DIRECTORIES:
        return False
    return name.startswith(".") or name in PRUNED_DIRECTORIES


def iter_yaml_files(root: Path):
    """Yield every YAML file under *root*, pruning non-configuration dirs.

    The prune has to rewrite ``dirs`` in place. Inspecting the components of
    ``os.walk``'s root string is exactly the bug this replaced: a walk rooted
    at ``"."`` yields roots like ``"./docker"`` on POSIX and ``".\\docker"``
    on Windows, so on *both* platforms one component is ``"."`` and the old
    guard pruned the entire tree -- not just hidden directories. (Measured on
    Windows: every walked root, hidden or not, was skipped.)
    """
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not is_pruned(d))
        for name in sorted(files):
            if name.endswith(YAML_SUFFIXES):
                yield Path(current) / name


def parse_file(path: Path) -> str | None:
    """Return a one-line error if *path* does not parse, otherwise None."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            # Load every document, so multi-document files are checked too.
            list(yaml.safe_load_all(handle))
    except Exception as exc:
        # PyYAML errors are multi-line; flatten them so each failure stays a
        # single readable bullet in the job summary.
        detail = " ".join(str(exc).split())
        return f"{path.relative_to(REPO_ROOT)}: {detail}"
    return None


def write_summary(lines: list[str]) -> None:
    """Append the job summary, but only when running under GitHub Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.writelines(lines)


def main() -> int:
    failures: list[str] = []
    checked = 0

    for path in iter_yaml_files(REPO_ROOT):
        checked += 1
        error = parse_file(path)
        if error:
            print(f"[-] Invalid YAML format in {error}", file=sys.stderr)
            failures.append(error)

    if checked == 0:
        message = f"no YAML files found under {REPO_ROOT}"
        print(
            f"[-] {message} -- the scanner is broken, not the repository",
            file=sys.stderr,
        )
        write_summary(
            [
                "### ❌ YAML Validation Failed\n",
                f"{message}. Refusing to report success on an empty scan.\n",
            ]
        )
        return 1

    if failures:
        write_summary(
            [
                "### ❌ YAML Validation Failed\n",
                f"{len(failures)} of {checked} YAML files failed parsing:\n",
            ]
            + [f"- `{error}`\n" for error in failures]
        )
        print(f"[-] {len(failures)} of {checked} YAML files failed parsing.")
        return 1

    write_summary(
        [
            "### ✅ YAML Validation Passed\n",
            f"All {checked} YAML configurations parsed cleanly "
            "without structural defects.\n",
        ]
    )
    print(f"[+] All {checked} YAML configurations parsed cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
