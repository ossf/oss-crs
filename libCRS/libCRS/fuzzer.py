"""Fuzzer data types and command builders for libCRS."""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FuzzerHandle:
    """Handle to a running fuzzer instance."""
    fuzzer_id: str
    pid: int


@dataclass
class FuzzerStatus:
    """Status of a fuzzer instance."""
    state: str  # "running", "stopped", "crashed"
    runtime_seconds: float
    execs: int
    corpus_size: int
    crashes_found: int
    pid: int


@dataclass
class FuzzerResult:
    """Final result when a fuzzer is stopped."""
    exit_code: int
    runtime_seconds: float
    corpus_size: int
    crashes_found: int


def build_fuzzer_cmd(
    engine: str,
    harness: Path,
    corpus_dir: Path,
    crashes_dir: Path,
    timeout: int = 0,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build engine-specific command line for running a fuzzer.

    Args:
        engine: Fuzzing engine name (e.g., "libfuzzer", "afl")
        harness: Path to the harness binary
        corpus_dir: Directory containing/storing corpus files
        crashes_dir: Directory to store crash files
        timeout: Maximum fuzzing time in seconds (0 = unlimited)
        extra_args: Additional engine-specific arguments

    Returns:
        Command line as a list of strings
    """
    if engine == "libfuzzer":
        cmd = [str(harness)]

        # Artifact prefix for crashes
        cmd.append(f"-artifact_prefix={crashes_dir}/")

        # Timeout per input
        if timeout > 0:
            cmd.append(f"-max_total_time={timeout}")

        # Add extra args if provided
        if extra_args:
            cmd.extend(extra_args)

        # Corpus directory as positional argument
        cmd.append(str(corpus_dir))

        return cmd

    elif engine == "afl":
        cmd = ["afl-fuzz"]

        # Input/output directories
        cmd.extend(["-i", str(corpus_dir)])
        cmd.extend(["-o", str(crashes_dir)])

        # Timeout
        if timeout > 0:
            cmd.extend(["-V", str(timeout)])

        # Add extra args if provided
        if extra_args:
            cmd.extend(extra_args)

        # Target binary
        cmd.append("--")
        cmd.append(str(harness))

        return cmd

    else:
        raise ValueError(f"Unsupported fuzzing engine: {engine}")
