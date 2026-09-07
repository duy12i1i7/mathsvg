"""Configuration loading with an optional PyYAML dependency.

The checked-in ``.yaml`` files intentionally use JSON syntax, which is valid
YAML and can therefore be read with the Python standard library alone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _expand(item) for key, item in value.items()}
    return value


def load_config(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConfigError(
                f"{path} is not JSON-compatible YAML and PyYAML is not installed"
            ) from exc
        try:
            value = yaml.safe_load(text)
        except Exception as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise ConfigError("top-level config value must be a mapping")
    expanded = _expand(value)
    version = expanded.get("schema_version")
    if version != 1:
        raise ConfigError(f"unsupported config schema_version {version!r}; expected 1")
    return expanded


def repository_root(script_file: str | Path) -> Path:
    path = Path(script_file).resolve()
    current = path if path.is_dir() else path.parent
    for depth, candidate in enumerate((current, *current.parents)):
        if depth >= 8:
            break
        if (candidate / "Cargo.toml").is_file() and (
            candidate / "YeuCau.md"
        ).is_file():
            return candidate
    raise ConfigError(
        f"could not locate repository root above {path}; "
        "expected Cargo.toml and YeuCau.md"
    )


def resolve_from(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()
