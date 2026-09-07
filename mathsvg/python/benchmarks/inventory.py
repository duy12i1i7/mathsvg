#!/usr/bin/env python3
"""Inventory baseline executables without running a benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shlex
import shutil
import subprocess
import sys
import tomllib
from typing import Sequence

from mathsvg.python.benchmarks.freeze import sha256_file

ALIASES: dict[str, tuple[str, ...]] = {
    "7z": ("7z", "7zz"),
    "snzip": ("snzip", "snappy"),
}

VERSION_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "7z": ("i",),
    "7zz": ("i",),
    "zpaq": (),
}

# ZPAQ prints its version banner and exits 1 when invoked without an archive
# command.  That behaviour is part of the pinned CLI contract; every other
# version probe must exit successfully.
VERSION_RETURN_CODES: dict[str, frozenset[int]] = {
    "zpaq": frozenset({0, 1}),
}


class InventoryError(RuntimeError):
    """The baseline catalogue is invalid."""


def _first_executable(command_template: str) -> str:
    if command_template.startswith("UNFROZEN:"):
        raise InventoryError(
            "UNFROZEN command templates are forbidden; use an explicit "
            "availability='unavailable' row with a reason"
        )
    try:
        words = shlex.split(command_template)
    except ValueError as exc:
        raise InventoryError(f"invalid command template: {exc}") from exc
    if not words:
        raise InventoryError("empty command template")
    return words[0]


def _resolve(
    executable: str, search_path: str | None
) -> tuple[str | None, str, str, str]:
    candidates = ALIASES.get(executable, (executable,))
    failures: list[str] = []
    for candidate in candidates:
        resolved = shutil.which(candidate, path=search_path)
        if resolved:
            canonical = str(pathlib.Path(resolved).resolve())
            version, failure = _version(canonical, candidate)
            if not failure:
                return canonical, candidate, version, ""
            failures.append(f"{candidate}: {failure}")
    if failures:
        return None, executable, "", "; ".join(failures)
    return None, executable, "", f"executable {executable!r} was not found"


def _version(path: str, resolved_name: str) -> tuple[str, str]:
    arguments = VERSION_ARGUMENTS.get(resolved_name, ("--version",))
    try:
        process = subprocess.run(
            [path, *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
    except OSError as exc:
        return "", f"version probe could not execute: {exc}"
    except subprocess.TimeoutExpired:
        return "", "version probe timed out"
    accepted = VERSION_RETURN_CODES.get(resolved_name, frozenset({0}))
    output = ""
    for line in process.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            output = stripped[:500]
            break
    if process.returncode not in accepted:
        detail = output or "no diagnostic output"
        return "", f"version probe exited {process.returncode}: {detail}"
    if not output:
        return "", "version probe produced no version"
    return output, ""


def inventory(
    catalog_path: pathlib.Path, *, search_path: str | None = None
) -> dict[str, object]:
    with catalog_path.open("rb") as handle:
        catalog = tomllib.load(handle)
    if catalog.get("schema_version") != 1:
        raise InventoryError("unsupported baseline catalogue schema")
    baselines = catalog.get("baseline")
    if not isinstance(baselines, list):
        raise InventoryError("catalogue must contain [[baseline]] rows")

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for baseline in baselines:
        if not isinstance(baseline, dict):
            raise InventoryError("baseline row must be a table")
        identifier = baseline.get("id")
        command = baseline.get("command_template")
        availability = baseline.get("availability", "auto")
        unavailable_reason = baseline.get("unavailable_reason", "")
        if not isinstance(identifier, str) or not identifier:
            raise InventoryError("baseline id must be a non-empty string")
        if identifier in seen:
            raise InventoryError(f"duplicate baseline id: {identifier}")
        seen.add(identifier)
        if availability not in {"auto", "unavailable"}:
            raise InventoryError(
                f"{identifier}: availability must be auto or unavailable"
            )
        if not isinstance(command, str):
            raise InventoryError(f"{identifier}: missing command_template")
        if availability == "unavailable":
            if not isinstance(unavailable_reason, str) or not unavailable_reason:
                raise InventoryError(
                    f"{identifier}: explicit unavailability requires a reason"
                )
            rows.append(
                {
                    "id": identifier,
                    "family": baseline.get("family", ""),
                    "status": "unavailable",
                    "unavailable_reason": unavailable_reason,
                    "requested_executable": "",
                    "resolved_executable": "",
                    "executable_sha256": "",
                    "version": "",
                }
            )
            continue

        requested = _first_executable(command)
        resolved, _resolved_name, version, resolution_error = _resolve(
            requested, search_path
        )
        if resolved is None:
            rows.append(
                {
                    "id": identifier,
                    "family": baseline.get("family", ""),
                    "status": "unavailable",
                    "unavailable_reason": resolution_error,
                    "requested_executable": requested,
                    "resolved_executable": "",
                    "executable_sha256": "",
                    "version": "",
                }
            )
            continue
        rows.append(
            {
                "id": identifier,
                "family": baseline.get("family", ""),
                "status": "available",
                "unavailable_reason": "",
                "requested_executable": requested,
                "resolved_executable": resolved,
                "executable_sha256": sha256_file(pathlib.Path(resolved)),
                "version": version,
            }
        )

    body: dict[str, object] = {
        "schema_version": 1,
        "catalog_sha256": sha256_file(catalog_path),
        "machine": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "baselines": rows,
    }
    canonical = json.dumps(
        body, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    body["inventory_sha256"] = hashlib.sha256(canonical).hexdigest()
    return body


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = inventory(args.catalog, search_path=os.environ.get("PATH"))
        encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
    except (InventoryError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"inventory error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
