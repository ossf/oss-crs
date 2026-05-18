# SPDX-License-Identifier: MIT
"""Cpuset parsing and mapping utilities.

This module provides functions for parsing cpuset strings (e.g., "0-3,5,8-11"),
mapping virtual CPU slots to real CPU pools, and scaling CPU allocations
proportionally across a real CPU pool.
"""

import re


def parse_cpuset(cpuset_str: str) -> set[int]:
    """Parse a cpuset string into a set of CPU numbers.

    Args:
        cpuset_str: A cpuset string like "0-3", "0,1,2,3", or "0-3,5,8-11"

    Returns:
        A set of integers representing the CPU numbers.

    Raises:
        ValueError: If the cpuset string format is invalid.

    Examples:
        >>> parse_cpuset("0-3")
        {0, 1, 2, 3}
        >>> parse_cpuset("0,1,2,3")
        {0, 1, 2, 3}
        >>> parse_cpuset("0-3,5,8-11")
        {0, 1, 2, 3, 5, 8, 9, 10, 11}
    """
    cpuset_str = cpuset_str.strip()
    pattern = r"^(\d+(-\d+)?)(,\d+(-\d+)?)*$"
    if not re.match(pattern, cpuset_str):
        raise ValueError(
            f"Invalid cpuset format: '{cpuset_str}'. "
            "Expected format like '0-3', '0,1,2,3', or '0-3,5,7-9'"
        )

    result = set()
    for part in cpuset_str.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            start_int, end_int = int(start), int(end)
            if start_int > end_int:
                raise ValueError(
                    f"Invalid cpuset range '{part}': start ({start_int}) > end ({end_int})"
                )
            result.update(range(start_int, end_int + 1))
        else:
            result.add(int(part))
    return result


def cpuset_to_str(cpus: set[int]) -> str:
    """Convert a set of CPU numbers back to a compact cpuset string.

    Args:
        cpus: A set of integers representing CPU numbers.

    Returns:
        A compact cpuset string representation.

    Examples:
        >>> cpuset_to_str({0, 1, 2, 3})
        '0-3'
        >>> cpuset_to_str({0, 1, 2, 3, 5, 8, 9, 10, 11})
        '0-3,5,8-11'
        >>> cpuset_to_str({1, 3, 5})
        '1,3,5'
    """
    if not cpus:
        raise ValueError("Cannot convert empty CPU set to string")

    sorted_cpus = sorted(cpus)
    ranges = []
    start = sorted_cpus[0]
    end = sorted_cpus[0]

    for cpu in sorted_cpus[1:]:
        if cpu == end + 1:
            end = cpu
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = cpu
            end = cpu

    # Handle the last range
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")

    return ",".join(ranges)


def map_cpuset(virtual_cpuset: str, cpu_mapping: dict[int, int]) -> str:
    """Map a virtual cpuset to real CPUs using the provided mapping.

    Args:
        virtual_cpuset: A virtual cpuset string (e.g., "0-3")
        cpu_mapping: A dictionary mapping virtual CPU -> real CPU

    Returns:
        A new cpuset string with virtual CPUs mapped to real CPUs.

    Raises:
        ValueError: If any virtual CPU is not in the mapping.
    """
    virtual_cpus = parse_cpuset(virtual_cpuset)
    real_cpus = set()

    for vcpu in virtual_cpus:
        if vcpu not in cpu_mapping:
            raise ValueError(
                f"Virtual CPU {vcpu} from cpuset '{virtual_cpuset}' "
                f"is not in the CPU mapping"
            )
        real_cpus.add(cpu_mapping[vcpu])

    return cpuset_to_str(real_cpus)


def create_cpu_mapping(
    virtual_cpusets: list[str],
    real_pool: str,
) -> dict[int, int]:
    """Create a mapping from virtual CPU slots to real CPUs.

    The mapping algorithm:
    1. Parse all virtual cpusets into a unified set of virtual CPUs
    2. Parse the real pool into a set of available CPUs
    3. Validate that pool size >= number of unique virtual CPUs
    4. Create sorted mapping: sorted_virtual[i] -> sorted_real[i]

    Args:
        virtual_cpusets: List of cpuset strings from the compose config
        real_pool: The real CPU pool to map to (e.g., "20-31")

    Returns:
        A dictionary mapping virtual CPU number -> real CPU number

    Raises:
        ValueError: If the real pool has fewer CPUs than required.

    Example:
        >>> create_cpu_mapping(["0-3", "4-7"], "20-27")
        {0: 20, 1: 21, 2: 22, 3: 23, 4: 24, 5: 25, 6: 26, 7: 27}
    """
    # Collect all unique virtual CPUs
    all_virtual_cpus: set[int] = set()
    for cpuset_str in virtual_cpusets:
        all_virtual_cpus.update(parse_cpuset(cpuset_str))

    # Parse real pool
    real_cpus = parse_cpuset(real_pool)

    # Validate pool size
    if len(real_cpus) < len(all_virtual_cpus):
        raise ValueError(
            f"CPU pool has {len(real_cpus)} CPUs ({real_pool}) but the configuration "
            f"requires at least {len(all_virtual_cpus)} unique virtual CPU slots"
        )

    # Create sorted mapping
    sorted_virtual = sorted(all_virtual_cpus)
    sorted_real = sorted(real_cpus)

    return {v: sorted_real[i] for i, v in enumerate(sorted_virtual)}


def scale_cpusets(
    allocations: dict[str, int],
    pool: str,
) -> dict[str, str]:
    """Scale CPU allocations proportionally to fit a real CPU pool.

    Each entry gets floor(original/total * pool_size) CPUs. Remainder CPUs
    are distributed to non-infra entries largest-allocation-first (ties broken
    by name). Every entry is guaranteed at least 1 CPU.

    Args:
        allocations: Mapping of entry name -> number of CPUs in template.
            Must include "oss_crs_infra" key.
        pool: Real CPU pool string (e.g., "20-31" or "1-4,10-13").

    Returns:
        Mapping of entry name -> cpuset string assigned from the pool.

    Raises:
        ValueError: If pool is too small (fewer CPUs than entries).
    """
    pool_cpus = sorted(parse_cpuset(pool))
    pool_size = len(pool_cpus)
    entry_names = list(allocations.keys())

    if pool_size < len(entry_names):
        raise ValueError(
            f"CPU pool has {pool_size} CPUs but there are {len(entry_names)} "
            f"entries — need at least 1 CPU per entry"
        )

    total_original = sum(allocations.values())

    # Floor-proportional allocation, minimum 1 per entry
    scaled: dict[str, int] = {}
    for name in entry_names:
        scaled[name] = max(1, int(allocations[name] / total_original * pool_size))

    # If we over-allocated (due to min-1 guarantees), shrink largest entries
    while sum(scaled.values()) > pool_size:
        # Shrink largest non-minimum entry
        shrinkable = [n for n in entry_names if scaled[n] > 1]
        if not shrinkable:
            break
        shrinkable.sort(key=lambda n: (-scaled[n], n))
        scaled[shrinkable[0]] -= 1

    # Distribute remainder to CRS entries (non-infra), largest-first
    remainder = pool_size - sum(scaled.values())
    if remainder > 0:
        crs_names = [n for n in entry_names if n != "oss_crs_infra"]
        if not crs_names:
            crs_names = entry_names
        # Sort by descending original allocation, then by name for stability
        crs_names.sort(key=lambda n: (-allocations[n], n))
        for i in range(remainder):
            scaled[crs_names[i % len(crs_names)]] += 1

    # Assign contiguous slices from the pool
    result: dict[str, str] = {}
    offset = 0
    for name in entry_names:
        count = scaled[name]
        assigned = set(pool_cpus[offset : offset + count])
        result[name] = cpuset_to_str(assigned)
        offset += count

    return result


def default_cpu_allocation(
    crs_names: list[str],
    pool: str,
) -> dict[str, str]:
    """Allocate CPUs from a pool using default ratios.

    Infrastructure gets min(4, max(1, pool_size // 4)). The remaining CPUs
    are split evenly among CRS entries, with remainder distributed to the
    first entries in the list.

    Args:
        crs_names: Names of the CRS entries (not including oss_crs_infra).
        pool: Real CPU pool string.

    Returns:
        Mapping of entry name -> cpuset string (includes "oss_crs_infra").

    Raises:
        ValueError: If pool is too small.
    """
    pool_cpus = sorted(parse_cpuset(pool))
    pool_size = len(pool_cpus)
    total_entries = 1 + len(crs_names)  # infra + CRSes

    if pool_size < total_entries:
        raise ValueError(
            f"CPU pool has {pool_size} CPUs but there are {total_entries} "
            f"entries — need at least 1 CPU per entry"
        )

    infra_count = min(4, max(1, pool_size // 4))
    remaining = pool_size - infra_count

    # Ensure at least 1 CPU per CRS
    if remaining < len(crs_names):
        # Steal from infra to guarantee 1 per CRS
        deficit = len(crs_names) - remaining
        infra_count -= deficit
        remaining = len(crs_names)

    per_crs = remaining // len(crs_names) if crs_names else 0
    crs_remainder = remaining - per_crs * len(crs_names) if crs_names else 0

    result: dict[str, str] = {}
    offset = 0

    result["oss_crs_infra"] = cpuset_to_str(
        set(pool_cpus[offset : offset + infra_count])
    )
    offset += infra_count

    for i, name in enumerate(crs_names):
        count = per_crs + (1 if i < crs_remainder else 0)
        result[name] = cpuset_to_str(set(pool_cpus[offset : offset + count]))
        offset += count

    return result
