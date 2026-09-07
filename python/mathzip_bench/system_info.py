"""Collect reproducibility metadata without third-party packages."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


def _first_line(command: list[str], timeout: float = 5.0) -> str | None:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout.strip()
    return output.splitlines()[0] if output else None


def _cpu_model() -> str | None:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware")) and ":" in line:
                return line.split(":", 1)[1].strip()
    if platform.system() == "Darwin":
        return _first_line(["sysctl", "-n", "machdep.cpu.brand_string"])
    return platform.processor() or None


def _memory_bytes() -> int | None:
    return _meminfo_bytes("MemTotal")


def _swap_bytes() -> int | None:
    return _meminfo_bytes("SwapTotal")


def _meminfo_bytes(field: str) -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="ascii", errors="replace").splitlines():
            if line.startswith(field + ":"):
                try:
                    return int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    break
    if field == "MemTotal" and hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PHYS_PAGES")) * int(
                os.sysconf("SC_PAGE_SIZE")
            )
        except (OSError, ValueError):
            pass
    return None


def _physical_cores() -> int | None:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        physical_id: str | None = None
        core_id: str | None = None
        pairs: set[tuple[str, str]] = set()
        for line in (
            cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines()
            + [""]
        ):
            if not line.strip():
                if physical_id is not None and core_id is not None:
                    pairs.add((physical_id, core_id))
                physical_id = core_id = None
            elif ":" in line:
                key, value = (part.strip() for part in line.split(":", 1))
                if key == "physical id":
                    physical_id = value
                elif key == "core id":
                    core_id = value
        if pairs:
            return len(pairs)
    try:
        completed = subprocess.run(
            ["lscpu", "-p=CORE,SOCKET"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    pairs = {
        tuple(line.split(",", 1))
        for line in completed.stdout.splitlines()
        if line and not line.startswith("#") and "," in line
    }
    return len(pairs) or None


def _cpu_affinity() -> dict[str, Any] | None:
    if not hasattr(os, "sched_getaffinity"):
        return None
    try:
        cpu_ids = sorted(os.sched_getaffinity(0))
    except OSError:
        return None
    return {"logical_cpu_count": len(cpu_ids), "logical_cpu_ids": cpu_ids}


def _cpu_governors() -> list[str] | None:
    governors: set[str] = set()
    for path in sorted(
        Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")
    ):
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value:
            governors.add(value)
    return sorted(governors) or None


def _decode_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _filesystem_metadata(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    result: dict[str, Any] = {
        "measured_path": str(path.resolve()),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "mount_point": None,
        "filesystem_type": None,
        "mount_source": None,
    }
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return result
    candidates: list[tuple[int, str, str, str]] = []
    resolved = str(path.resolve())
    for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        left = before.split()
        right = after.split()
        if len(left) < 5 or len(right) < 2:
            continue
        mount_point = _decode_mount_field(left[4])
        if resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/"):
            candidates.append(
                (len(mount_point), mount_point, right[0], _decode_mount_field(right[1]))
            )
    if candidates:
        _, mount_point, filesystem_type, source = max(candidates)
        result.update(
            {
                "mount_point": mount_point,
                "filesystem_type": filesystem_type,
                "mount_source": source,
            }
        )
    return result


def _power_metadata() -> list[dict[str, Any]] | None:
    root = Path("/sys/class/power_supply")
    if not root.is_dir():
        return None
    supplies: list[dict[str, Any]] = []
    for supply in sorted(root.iterdir()):
        entry: dict[str, Any] = {"name": supply.name}
        for field in ("type", "status", "online", "capacity"):
            try:
                entry[field] = (supply / field).read_text(encoding="ascii").strip()
            except OSError:
                entry[field] = None
        supplies.append(entry)
    return supplies or None


def _container_metadata() -> dict[str, Any]:
    cgroup_path = Path("/proc/1/cgroup")
    try:
        cgroup = cgroup_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        cgroup = None
    detected = Path("/.dockerenv").exists() or bool(
        cgroup
        and any(
            marker in cgroup.lower()
            for marker in ("docker", "containerd", "kubepods", "podman", "lxc")
        )
    )
    image_digest = os.environ.get("CONTAINER_IMAGE_DIGEST") or os.environ.get(
        "IMAGE_DIGEST"
    )
    return {
        "detected": detected,
        "cgroup": cgroup,
        "image_digest": image_digest,
        "image_digest_capture": (
            "CONTAINER_IMAGE_DIGEST/IMAGE_DIGEST environment variable"
            if image_digest
            else "unavailable: runtime did not expose an image digest"
        ),
    }


def _relevant_environment() -> dict[str, str | None]:
    keys = (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "RAYON_NUM_THREADS",
        "ZSTD_NBTHREADS",
        "XZ_DEFAULTS",
        "XZ_OPT",
        "BZIP2",
        "BZIP",
        "GZIP",
        "CFLAGS",
        "CXXFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
        "RUSTFLAGS",
        "RUSTDOCFLAGS",
        "MATHZIP_THREADS",
        "MALLOC_CONF",
        "MALLOC_ARENA_MAX",
        "GLIBC_TUNABLES",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LANG",
        "LC_ALL",
        "LC_NUMERIC",
        "PYTHONHASHSEED",
        "TZ",
    )
    return {key: os.environ.get(key) for key in keys}


def tool_versions(tool_names: Iterable[str]) -> dict[str, dict[str, Any]]:
    versions: dict[str, dict[str, Any]] = {}
    version_flags = {
        "7z": ["i"],
        "7zz": ["i"],
        "brotli": ["--version"],
        "bzip2": ["--version"],
        "gzip": ["--version"],
        "lz4": ["--version"],
        "xz": ["--version"],
        "zstd": ["--version"],
    }
    for name in sorted(set(tool_names)):
        executable = shutil.which(name)
        versions[name] = {
            "path": executable,
            "version": (
                _first_line([executable, *version_flags.get(name, ["--version"])])
                if executable
                else None
            ),
        }
    return versions


def collect_source_metadata(repository_root: Path) -> dict[str, Any]:
    """Record a revision and content hash for the exact source checkout.

    The tree hash covers tracked and untracked, non-ignored regular files. It
    remains useful before the first Git commit, while publication-grade runs
    additionally require a clean Git revision.
    """

    root = repository_root.resolve()
    revision = _git_text(root, ["rev-parse", "HEAD"])
    status_text = _git_text(
        root, ["status", "--porcelain=v1", "--untracked-files=normal"]
    )
    dirty = bool(status_text) if status_text is not None else None
    listed = _git_bytes(
        root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    if listed is not None:
        relative_paths = sorted(
            Path(os.fsdecode(value))
            for value in listed.split(b"\0")
            if value
        )
        selection_method = "git ls-files (tracked plus untracked, Git-ignored excluded)"
    else:
        excluded_prefixes = (
            ".git/",
            ".venv/",
            "target/",
            "datasets/data/",
            "datasets/generated/",
            "datasets/synthetic/",
            "datasets/synthetic_full/",
            "benchmarks/raw/",
            "benchmarks/results/",
            "benchmarks/plots/",
        )
        relative_paths = sorted(
            path.relative_to(root)
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and not any(
                path.relative_to(root).as_posix().startswith(prefix)
                for prefix in excluded_prefixes
            )
        )
        selection_method = (
            "filesystem walk with generated/build/VCS directories excluded"
        )

    digest = hashlib.sha256()
    file_count = 0
    manifest: list[dict[str, Any]] = []
    for relative in relative_paths:
        path = root / relative
        if not path.is_file():
            continue
        encoded_path = relative.as_posix().encode("utf-8", errors="surrogateescape")
        digest.update(len(encoded_path).to_bytes(8, "little"))
        digest.update(encoded_path)
        file_digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                file_digest.update(chunk)
        digest.update(size.to_bytes(8, "little"))
        digest.update(file_digest.digest())
        manifest.append(
            {
                "path": relative.as_posix(),
                "size_bytes": size,
                "sha256": file_digest.hexdigest(),
            }
        )
        file_count += 1
    return {
        "source_revision": revision,
        "source_dirty": dirty,
        "git_revision": revision,
        "git_dirty": dirty,
        "revision_available": revision is not None,
        "git_status_entry_count": len(status_text.splitlines())
        if status_text
        else 0,
        "source_tree_sha256": digest.hexdigest(),
        "source_file_count": file_count,
        "source_tree_manifest": manifest if revision is None else None,
        "source_tree_manifest_reason": (
            "embedded because no Git HEAD was available"
            if revision is None
            else "omitted because a Git revision identifies the clean base; tree hash still recorded"
        ),
        "selection_method": selection_method,
    }


def _git_text(root: Path, arguments: list[str]) -> str | None:
    result = _git_run(root, arguments)
    if result is None:
        return None
    return result.decode("utf-8", errors="replace").strip()


def _git_bytes(root: Path, arguments: list[str]) -> bytes | None:
    return _git_run(root, arguments)


def _git_run(root: Path, arguments: list[str]) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def collect_system_metadata(
    tool_names: Iterable[str] = (), workspace: Path | None = None
) -> dict[str, Any]:
    measured_path = (workspace or Path.cwd()).resolve()
    metadata = {
        "collected_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "hostname": platform.node(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": _cpu_model(),
        "logical_cores": os.cpu_count(),
        "physical_cores": _physical_cores(),
        "cpu_affinity": _cpu_affinity(),
        "cpu_governors": _cpu_governors(),
        "ram_bytes": _memory_bytes(),
        "swap_bytes": _swap_bytes(),
        "storage": _filesystem_metadata(measured_path),
        "power_supplies": _power_metadata(),
        "container": _container_metadata(),
        "relevant_environment": _relevant_environment(),
        "python_version": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "rustc_version": _first_line(["rustc", "--version"]),
        "cargo_version": _first_line(["cargo", "--version"]),
        "cc_version": _first_line(["cc", "--version"]),
        "compressors": tool_versions(tool_names),
    }
    unavailable: list[dict[str, str]] = []
    for field, reason in (
        ("physical_cores", "OS did not expose physical core topology"),
        ("cpu_affinity", "CPU affinity API unavailable"),
        ("cpu_governors", "cpufreq governor files unavailable"),
        ("swap_bytes", "OS did not expose swap capacity"),
        ("power_supplies", "power-supply status unavailable"),
    ):
        if metadata.get(field) is None:
            unavailable.append({"field": field, "reason": reason})
    if metadata["container"].get("image_digest") is None:
        unavailable.append(
            {
                "field": "container.image_digest",
                "reason": "container runtime did not expose an image digest",
            }
        )
    metadata["metric_unavailable"] = unavailable
    return metadata
