"""Memory parsing and scaling utilities.

This module provides functions for parsing memory strings (e.g., "8G", "1024M"),
converting back to human-readable format, and scaling memory allocations
proportionally.
"""

import re

_UNITS = {
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
}


def parse_memory(mem_str: str) -> int:
    """Parse a memory string into bytes.

    Args:
        mem_str: A memory string like "8G", "16GB", "1024M", "2048MB".

    Returns:
        Number of bytes as an integer.

    Raises:
        ValueError: If the format is invalid.
    """
    mem_str = mem_str.strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([A-Za-z]+)$", mem_str)
    if not match:
        raise ValueError(
            f"Invalid memory format: '{mem_str}'. "
            "Expected format like '8G', '16GB', '1024M', '2048MB'"
        )
    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit not in _UNITS:
        raise ValueError(
            f"Unknown memory unit: '{match.group(2)}'. "
            f"Supported units: {', '.join(sorted(set(_UNITS.keys())))}"
        )
    return int(value * _UNITS[unit])


def memory_to_str(num_bytes: int) -> str:
    """Convert bytes to a human-readable memory string.

    Uses the largest unit that produces a whole number.

    Args:
        num_bytes: Number of bytes.

    Returns:
        A compact memory string (e.g., "8G", "512M").
    """
    if num_bytes <= 0:
        raise ValueError("Memory must be positive")

    for unit, suffix in [
        (1024**4, "T"),
        (1024**3, "G"),
        (1024**2, "M"),
        (1024, "K"),
    ]:
        if num_bytes >= unit and num_bytes % unit == 0:
            return f"{num_bytes // unit}{suffix}"
    return f"{num_bytes}B"


def scale_memory(
    allocations: dict[str, str],
    total: str,
) -> dict[str, str]:
    """Scale memory allocations proportionally to fit a total.

    Each entry gets floor(original/sum * total_bytes), rounded down to the
    nearest MB. Remainder is distributed to non-infra entries largest-first.

    Args:
        allocations: Mapping of entry name -> memory string (e.g., "8G").
        total: Total memory string to distribute (e.g., "64G").

    Returns:
        Mapping of entry name -> scaled memory string.
    """
    total_bytes = parse_memory(total)
    original_bytes = {name: parse_memory(mem) for name, mem in allocations.items()}
    original_total = sum(original_bytes.values())
    entry_names = list(allocations.keys())
    mb = 1024**2

    # Floor-proportional allocation (in MB granularity), minimum 1 MB per entry
    scaled: dict[str, int] = {}
    for name in entry_names:
        raw = int(original_bytes[name] / original_total * total_bytes)
        scaled[name] = max(mb, (raw // mb) * mb)

    # Distribute remainder to CRS entries, largest-first
    remainder = total_bytes - sum(scaled.values())
    if remainder > 0:
        crs_names = [n for n in entry_names if n != "oss_crs_infra"]
        if not crs_names:
            crs_names = entry_names
        crs_names.sort(key=lambda n: (-original_bytes[n], n))
        # Distribute in MB chunks
        remainder_mb = remainder // mb
        for i in range(remainder_mb):
            scaled[crs_names[i % len(crs_names)]] += mb

    return {name: memory_to_str(scaled[name]) for name in entry_names}


def default_memory_allocation(
    crs_names: list[str],
    total: str,
) -> dict[str, str]:
    """Allocate memory using default ratios.

    Infrastructure gets min(16G, 25% of total). The rest is split evenly
    among CRS entries.

    Args:
        crs_names: Names of the CRS entries (not including oss_crs_infra).
        total: Total memory string.

    Returns:
        Mapping of entry name -> memory string (includes "oss_crs_infra").
    """
    total_bytes = parse_memory(total)
    mb = 1024**2
    gb16 = 16 * 1024**3

    infra_bytes = min(gb16, total_bytes // 4)
    infra_bytes = max(mb, (infra_bytes // mb) * mb)  # At least 1 MB, MB-aligned

    remaining = total_bytes - infra_bytes

    result: dict[str, str] = {}
    result["oss_crs_infra"] = memory_to_str(infra_bytes)

    if crs_names:
        per_crs = (remaining // len(crs_names) // mb) * mb
        per_crs = max(mb, per_crs)
        crs_remainder = remaining - per_crs * len(crs_names)

        for i, name in enumerate(crs_names):
            extra = mb if i < crs_remainder // mb else 0
            result[name] = memory_to_str(per_crs + extra)

    return result
