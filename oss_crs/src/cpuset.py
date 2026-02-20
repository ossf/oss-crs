"""Cpuset parsing and mapping utilities.

This module provides functions for parsing cpuset strings (e.g., "0-3,5,8-11")
and mapping virtual CPU slots to real CPU pools.
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
