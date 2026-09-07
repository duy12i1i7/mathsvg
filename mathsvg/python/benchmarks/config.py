"""Validation for checked-in MathSVG profile budgets."""

from __future__ import annotations

import pathlib
import tomllib
from typing import Any

PROFILE_NAMES = frozenset(
    {"fast", "balanced", "max", "structured", "repository"}
)
ABLATION_ALGORITHMS = frozenset(
    {
        "whole-block-entropy",
        "whole-block-functions",
        "interval-functions",
        "coordinates",
    }
)
ENCODER_RSS_LIMITS = {
    "fast": 256 * 1024 * 1024,
    "balanced": 512 * 1024 * 1024,
    "max": 1024 * 1024 * 1024,
    "structured": 512 * 1024 * 1024,
    "repository": 1024 * 1024 * 1024,
}
DECODER_RSS_LIMIT = 256 * 1024 * 1024


class ConfigError(ValueError):
    """A profile conflicts with its frozen safety/search contract."""


def _table(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"missing [{name}] table")
    return value


def _positive(table: dict[str, Any], field: str) -> int:
    value = table.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{field} must be a positive integer")
    return value


def load_profile(path: pathlib.Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if config.get("schema_version") != 1:
        raise ConfigError("unsupported profile schema")
    profile = config.get("profile")
    if profile not in PROFILE_NAMES:
        raise ConfigError(f"invalid profile: {profile!r}")
    if config.get("status") not in {"development", "frozen"}:
        raise ConfigError("status must be development or frozen")
    if config.get("dsl_version") != 1 or config.get("container_version") != 1:
        raise ConfigError("profile must explicitly target DSL/container v1")

    blocks = _table(config, "blocks")
    superblock = _positive(blocks, "superblock_bytes")
    block = _positive(blocks, "block_bytes")
    microblock = _positive(blocks, "microblock_bytes")
    if not microblock <= block <= superblock:
        raise ConfigError("expected microblock <= block <= superblock")
    if block > 16 * 1024 * 1024:
        raise ConfigError("block exceeds the format-v1 restored block maximum")
    if block % microblock:
        raise ConfigError("block size must be a multiple of microblock size")

    limits = _table(config, "limits")
    encoder_rss = _positive(limits, "encoder_rss_bytes")
    decoder_rss = _positive(limits, "decoder_rss_bytes")
    if encoder_rss >= ENCODER_RSS_LIMITS[profile]:
        raise ConfigError(f"{profile} encoder RSS target is not strict")
    if decoder_rss >= DECODER_RSS_LIMIT:
        raise ConfigError("decoder RSS target is not strictly below 256 MiB")
    if _positive(limits, "max_nodes_per_block") > 1_048_576:
        raise ConfigError("node limit exceeds format v1")
    if _positive(limits, "max_graph_depth") > 256:
        raise ConfigError("graph depth exceeds format v1")

    search = _table(config, "search")
    for catalog_name in ("coordinate_catalog", "function_catalog"):
        catalog = search.get(catalog_name)
        if not isinstance(catalog, list) or not catalog:
            raise ConfigError(f"{catalog_name} must be a non-empty array")
        if not all(isinstance(item, str) and item for item in catalog):
            raise ConfigError(f"{catalog_name} entries must be strings")
        if len(catalog) != len(set(catalog)):
            raise ConfigError(f"{catalog_name} contains duplicates")
    for depth_name in ("coordinate_depth", "residual_depth", "symbolic_depth"):
        value = search.get(depth_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"{depth_name} must be a non-negative integer")
    for gate_name in (
        "whole_block_entropy",
        "whole_block_functions",
        "interval_functions",
    ):
        if not isinstance(search.get(gate_name), bool):
            raise ConfigError(f"{gate_name} must be a boolean")

    parallel = _table(config, "parallel")
    _positive(parallel, "default_threads")
    if parallel.get("deterministic_merge") is not True:
        raise ConfigError("deterministic_merge must be true")

    entropy = _table(config, "entropy")
    enhanced = profile != "fast"
    expected_entropy = {
        "baseline_policy": "G1",
        "add_only_policy": "C8L" if enhanced else "none",
        "chain_depth": 8 if enhanced else 1,
        "one_byte_lazy": enhanced,
        "parser_scratch_bytes": 524_288 if enhanced else 262_144,
        "maximum_additional_work_per_input_byte_per_walk": 25 if enhanced else 0,
        "maximum_policy_walks": 2 if enhanced else 0,
    }
    for field, expected in expected_entropy.items():
        value = entropy.get(field)
        if type(value) is not type(expected) or value != expected:
            raise ConfigError(
                f"{profile} entropy contract mismatch for {field}: "
                f"expected {expected!r}, got {value!r}"
            )

    stop = _table(config, "stop")
    gain = stop.get("minimum_oracle_gain_fraction")
    multiplier = stop.get("maximum_search_multiplier_below_threshold")
    if gain != 0.005:
        raise ConfigError("oracle stop threshold must be exactly 0.005")
    if multiplier != 2.0:
        raise ConfigError("search multiplier stop threshold must be exactly 2.0")
    return config


def load_ablation_catalog(path: pathlib.Path) -> dict[str, tuple[str, ...]]:
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    if document.get("schema_version") != 1:
        raise ConfigError("unsupported ablation schema")
    if document.get("status") not in {"development", "frozen"}:
        raise ConfigError("ablation status must be development or frozen")
    rows = document.get("ablation")
    if not isinstance(rows, list) or not rows:
        raise ConfigError("ablation catalogue must be a non-empty array")

    catalog: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigError("ablation row must be a table")
        identifier = row.get("id")
        disabled = row.get("disable")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier == "none"
            or any(
                not (character.islower() or character.isdigit() or character == "-")
                for character in identifier
            )
        ):
            raise ConfigError("ablation id must be canonical lowercase kebab-case")
        if identifier in catalog:
            raise ConfigError(f"duplicate ablation id: {identifier}")
        if (
            not isinstance(disabled, list)
            or not disabled
            or not all(isinstance(item, str) for item in disabled)
        ):
            raise ConfigError(f"{identifier} disable must be a non-empty string array")
        canonical = tuple(sorted(set(disabled)))
        if len(canonical) != len(disabled):
            raise ConfigError(f"{identifier} disable contains duplicates")
        unknown = set(canonical) - ABLATION_ALGORITHMS
        if unknown:
            raise ConfigError(
                f"{identifier} disables unsupported algorithms: {sorted(unknown)}"
            )
        catalog[identifier] = canonical
    return catalog


def load_all(config_root: pathlib.Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for profile in sorted(PROFILE_NAMES):
        path = config_root / profile / "profile.toml"
        config = load_profile(path)
        if config["profile"] != profile:
            raise ConfigError(f"{path}: directory/profile mismatch")
        loaded[profile] = config
    return loaded
