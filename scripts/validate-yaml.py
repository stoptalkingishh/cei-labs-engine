#!/usr/bin/env python3
"""Parse every YAML file in the repository and report structural defects.

Runs standalone (no GitHub environment required) so the walk can be exercised
locally; the GITHUB_STEP_SUMMARY job summary is written only when that variable
is set. Exits non-zero when a file fails to parse or when no YAML file was
discovered at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_SUFFIXES = (".yml", ".yaml")


def discover_yaml_files(root: Path) -> list[Path]:
    """Return every YAML file under *root*, skipping dot-prefixed directories.

    Hidden directories are pruned in place through ``dirs[:]`` rather than by
    inspecting the path components of the walk root. On POSIX ``os.walk(".")``
    yields ``"."`` and then ``"./subdir"``, whose first component is the literal
    ``"."``; every root therefore starts with a dot and a component-based test
    discards the entire tree while still exiting 0. The bug is invisible on
    Windows, where ``os.sep`` is a backslash, so the pruning must never depend on
    how the walk spells its roots.
    """
    discovered: list[Path] = []
    for parent, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith(YAML_SUFFIXES):
                discovered.append(Path(parent) / name)
    return sorted(discovered)


def collect_failures(paths: Iterable[Path]) -> list[str]:
    """Parse each file and return one message per failure.

    Every file is attempted so a single run reports the full breakage set
    instead of stopping at the first error. PyYAML signals parse problems with
    several unrelated exception types, hence the broad catch.
    """
    failures: list[str] = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as handle:
                # Multi-document files are legal; drain every document.
                list(yaml.safe_load_all(handle))
        except Exception as exc:
            failures.append(f"{os.path.relpath(path, REPO_ROOT)}: {exc}")
    return failures


def write_step_summary(markdown: str) -> None:
    """Append *markdown* to the job summary, if running inside GitHub Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(markdown)


def main() -> int:
    files = discover_yaml_files(REPO_ROOT)
    checked = len(files)

    # Zero files must never read as success. The original inline walk skipped
    # every root on POSIX, so CI reported a green check while validating nothing
    # at all; failing loudly here makes that whole class of regression visible
    # instead of silent.
    if checked == 0:
        message = (
            "No YAML files were discovered under the repository root. The walk "
            "pruned every directory, so this run validated nothing and must not "
            "be reported as a pass."
        )
        print(f"[-] {message}", file=sys.stderr)
        write_step_summary(f"### ❌ YAML Validation Failed\n\n{message}\n")
        return 1

    failures = collect_failures(files)
    print(f"[+] Checked {checked} YAML file(s) under {REPO_ROOT}.")

    if failures:
        for failure in failures:
            print(f"[-] Invalid YAML format in {failure}", file=sys.stderr)
        write_step_summary(
            "### ❌ YAML Validation Failed\n"
            f"{len(failures)} of {checked} YAML file(s) failed parsing validation:\n"
            + "".join(f"- `{failure}`\n" for failure in failures)
        )
        return 1

    write_step_summary(
        "### ✅ YAML Validation Passed\n"
        f"All {checked} YAML configurations parsed cleanly without structural "
        "defects.\n"
    )
    print("[+] All YAML configurations parsed cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
