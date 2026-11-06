# SPDX-License-Identifier: MIT
"""Run one CRS command directly on the host and manage its session."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from ..target import Target


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def add_local_command(subparsers, default_work_dir: Path) -> None:
    local = subparsers.add_parser(
        "local", help="Run a CRS checkout on the host machine"
    )
    actions = local.add_subparsers(dest="local_action", required=True)

    start = actions.add_parser("start", help="Start a host-local CRS session")
    stop = actions.add_parser("stop", help="Stop a host-local CRS session")
    for parser in (start, stop):
        parser.add_argument(
            "--fuzz-proj-path",
            type=Path,
            required=True,
            help="Path to the OSS-Fuzz project directory",
        )
        parser.add_argument(
            "--target-harness",
            default=None,
            help="Target harness name (omit for harness generation or source analysis)",
        )
        parser.add_argument(
            "--crs-path",
            type=Path,
            default=Path.cwd(),
            help="CRS checkout to run (default: current directory)",
        )
        parser.add_argument(
            "--work-dir",
            type=Path,
            default=default_work_dir,
            help="Working directory for local sessions and artifacts",
        )
    start.add_argument(
        "--target-source-path",
        type=Path,
        default=None,
        help="Optional host path to the target source checkout",
    )
    start.add_argument(
        "--command",
        dest="local_command",
        nargs=argparse.REMAINDER,
        required=True,
        help="Command to run in the CRS checkout; use '--' before command arguments",
    )


def _session_id(crs_path: Path, fuzz_proj_path: Path, harness: str | None) -> str:
    identity = "\0".join(
        (str(crs_path.resolve()), str(fuzz_proj_path.resolve()), harness or "")
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def _session_dir(args) -> Path:
    return (
        args.work_dir
        / "local"
        / _session_id(args.crs_path, args.fuzz_proj_path, args.target_harness)
    )


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _process_group_is_running(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _pid_belongs_to_session(pid: int, session_id: str) -> bool:
    """Avoid signaling a recycled or unrelated PID from stale state."""
    try:
        if os.getpgid(pid) != pid:
            return False
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False
    marker = f"OSS_CRS_LOCAL_SESSION_ID={session_id}".encode()
    return marker in environ


def _load_session(state_path: Path) -> dict | None:
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot read local session state {state_path}: {exc}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
        raise ValueError(f"Invalid local session state: {state_path}")
    return value


def _write_session(
    state_path: Path,
    pid: int,
    session_id: str,
    containers: list[str],
) -> None:
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "pid": pid,
                "session_id": session_id,
                "containers": containers,
                "started_at": time.time(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(state_path)


def _runtime_environment(
    args,
    session_dir: Path,
    target: Target,
    target_source_path: Path,
    service_urls: dict[str, str],
) -> dict[str, str]:
    target_env = target.get_target_env()

    dirs = {
        "OSS_CRS_BUILD_OUT_DIR": session_dir / "build-out",
        "OSS_CRS_REBUILD_OUT_DIR": session_dir / "rebuild-out",
        "OSS_CRS_SUBMIT_DIR": session_dir / "submit",
        "OSS_CRS_SHARED_DIR": session_dir / "shared",
        "OSS_CRS_LOG_DIR": session_dir / "logs",
        "OSS_CRS_FETCH_DIR": session_dir / "fetch",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)

    session_id = _session_id(args.crs_path, args.fuzz_proj_path, args.target_harness)
    env = os.environ.copy()
    env.update(
        {
            "HELPER": "True",
            "RUN_FUZZER_MODE": "interactive",
            "PROJECT_NAME": target.name,
            "FUZZING_ENGINE": target_env["engine"],
            "SANITIZER": target_env["sanitizer"],
            "ARCHITECTURE": target_env["architecture"],
            "FUZZING_LANGUAGE": target_env["language"],
            "OSS_CRS_RUN_ENV_TYPE": "local",
            "OSS_CRS_CURRENT_PHASE": "run",
            "OSS_CRS_NAME": args.crs_path.name,
            "OSS_CRS_SERVICE_NAME": args.crs_path.name,
            "OSS_CRS_RUN_ID": f"local-{session_id}",
            "OSS_CRS_TARGET": target.name,
            "OSS_CRS_PROJ_PATH": str(args.fuzz_proj_path),
            "OSS_CRS_TARGET_PROJ_DIR": str(args.fuzz_proj_path),
            "OSS_CRS_FUZZ_PROJ": str(args.fuzz_proj_path),
            "OSS_CRS_TARGET_SOURCE": str(target_source_path),
            "OSS_CRS_REPO_PATH": str(target_source_path),
            "OSS_CRS_SERVICE_URLS": json.dumps(service_urls),
            "BUILDER_MODULE": "builder-sidecar",
            "RUNNER_MODULE": "runner-sidecar",
            "OSS_CRS_LOCAL_SESSION_ID": session_id,
        }
    )
    if args.target_harness:
        env["OSS_CRS_TARGET_HARNESS"] = args.target_harness
    else:
        env.pop("OSS_CRS_TARGET_HARNESS", None)
    for key, value in dirs.items():
        env[key] = str(value)
    return env


def _run_docker(arguments: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"could not run Docker: {exc}") from exc


def _build_sidecar_image(image: str, context: Path) -> None:
    result = _run_docker(["build", "--tag", image, str(context)])
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to build {image} from {context}: {result.stderr.strip()}"
        )


def _start_sidecar(
    *,
    name: str,
    image: str,
    environment: dict[str, str],
    mounts: list[tuple[Path, str, bool]],
) -> str:
    _run_docker(["rm", "--force", name])
    command = [
        "run",
        "--detach",
        "--name",
        name,
        "--publish",
        "127.0.0.1::8080",
    ]
    for key, value in environment.items():
        command.extend(["--env", f"{key}={value}"])
    for host_path, container_path, read_only in mounts:
        mount = f"type=bind,src={host_path.resolve()},dst={container_path}" + (
            ",readonly" if read_only else ""
        )
        command.extend(
            [
                "--mount",
                mount,
            ]
        )
    command.append(image)
    result = _run_docker(command)
    if result.returncode != 0:
        raise RuntimeError(f"failed to start {name}: {result.stderr.strip()}")

    port_result = _run_docker(["port", name, "8080/tcp"])
    if port_result.returncode != 0 or not port_result.stdout.strip():
        raise RuntimeError(
            f"could not resolve the host port for {name}: {port_result.stderr.strip()}"
        )
    host_port = port_result.stdout.strip().splitlines()[0].rsplit(":", 1)[-1]
    service_url = f"http://127.0.0.1:{host_port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{service_url}/health", timeout=2) as response:
                if response.status == 200:
                    return service_url
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError(f"{name} did not become healthy at {service_url}")


def _start_sidecars(
    session_id: str,
    session_dir: Path,
    fuzz_proj_path: Path,
    target_base_image: str,
) -> tuple[list[str], dict[str, str]]:
    root = _repository_root()
    builder_image = "oss-crs-builder-sidecar:local"
    runner_image = "oss-crs-runner-sidecar:local"
    _build_sidecar_image(builder_image, root / "oss-crs-infra" / "builder-sidecar")
    _build_sidecar_image(runner_image, root / "oss-crs-infra" / "runner-sidecar")

    rebuild_out = session_dir / "rebuild-out"
    build_out = session_dir / "build-out"
    logs = session_dir / "logs"
    for path in (rebuild_out, build_out, logs):
        path.mkdir(parents=True, exist_ok=True)

    prefix = f"oss-crs-local-{session_id}"
    builder_name = f"{prefix}-builder"
    runner_name = f"{prefix}-runner"
    containers = [builder_name, runner_name]
    builder_url = _start_sidecar(
        name=builder_name,
        image=builder_image,
        environment={
            "REBUILD_OUT_DIR": "/rebuild_out",
            "HOST_REBUILD_OUT_DIR": str(rebuild_out.resolve()),
            "PROJECT_BASE_IMAGE": target_base_image,
            "BASE_IMAGE": target_base_image,
            "BASE_IMAGE_DEFAULT": target_base_image,
            "OSS_CRS_FUZZ_PROJ": "/OSS_CRS_FUZZ_PROJ",
            "SIDECAR_LOG_DIR": "/sidecar-logs",
        },
        mounts=[
            (Path("/var/run/docker.sock"), "/var/run/docker.sock", False),
            (rebuild_out, "/rebuild_out", False),
            (fuzz_proj_path, "/OSS_CRS_FUZZ_PROJ", True),
            (logs, "/sidecar-logs", False),
        ],
    )
    runner_url = _start_sidecar(
        name=runner_name,
        image=runner_image,
        environment={
            "OUT_DIR": "/out",
            "BUILD_OUT_DIR": "/build_out",
            "SIDECAR_LOG_DIR": "/sidecar-logs",
        },
        mounts=[
            (rebuild_out, "/out", False),
            (build_out, "/build_out", True),
            (logs, "/sidecar-logs", False),
        ],
    )
    return containers, {
        "builder-sidecar": builder_url,
        "runner-sidecar": runner_url,
    }


def _remove_sidecars(session_id: str, *, best_effort: bool = False) -> None:
    prefix = f"oss-crs-local-{session_id}-"
    for role in ("builder", "runner"):
        name = f"{prefix}{role}"
        try:
            result = _run_docker(["rm", "--force", name])
        except RuntimeError:
            if best_effort:
                return
            raise
        if result.returncode != 0 and "no such container" not in result.stderr.lower():
            if not best_effort:
                raise RuntimeError(f"failed to remove {name}: {result.stderr.strip()}")


def handle_local_start(args) -> bool:
    command = list(args.local_command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        print("Error: --command must include a program to run", file=sys.stderr)
        return False
    if not args.crs_path.is_dir():
        print(
            f"Error: CRS checkout is not a directory: {args.crs_path}",
            file=sys.stderr,
        )
        return False
    if not args.fuzz_proj_path.is_dir():
        print(
            f"Error: OSS-Fuzz project is not a directory: {args.fuzz_proj_path}",
            file=sys.stderr,
        )
        return False
    if args.target_source_path is not None and not args.target_source_path.is_dir():
        print(
            f"Error: target source checkout is not a directory: {args.target_source_path}",
            file=sys.stderr,
        )
        return False

    session_dir = _session_dir(args)
    state_path = session_dir / "session.json"
    session_dir.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen | None = None
    session_id = _session_id(args.crs_path, args.fuzz_proj_path, args.target_harness)
    containers: list[str] = []
    try:
        old_state = _load_session(state_path)
        if old_state and _pid_belongs_to_session(old_state["pid"], session_id):
            print(
                f"Error: local session is already running (pid {old_state['pid']})",
                file=sys.stderr,
            )
            return False
        _remove_sidecars(session_id)
        state_path.unlink(missing_ok=True)

        target = Target(
            work_dir=session_dir / "target",
            proj_path=args.fuzz_proj_path,
            repo_path=args.target_source_path,
            target_harness=args.target_harness,
        )
        target_base_image = target.build_docker_image()
        if target_base_image is None:
            raise RuntimeError("failed to build the OSS-Fuzz project image")
        if target._has_repo:
            target_source_path = target.repo_path.resolve()
        else:
            target_source_path = session_dir / "target-source"
            if not target.extract_workdir_to_host(
                target_source_path, target_base_image
            ):
                raise RuntimeError(
                    "failed to extract target source from the project image"
                )

        containers, service_urls = _start_sidecars(
            session_id,
            session_dir,
            args.fuzz_proj_path,
            target_base_image,
        )
        log_path = session_dir / "crs.log"
        with log_path.open("ab") as log_file:
            process = subprocess.Popen(
                command,
                cwd=args.crs_path,
                env=_runtime_environment(
                    args,
                    session_dir,
                    target,
                    target_source_path,
                    service_urls,
                ),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        time.sleep(0.1)
        if process.poll() is not None:
            _remove_sidecars(session_id, best_effort=True)
            print(
                f"Error: CRS command exited immediately with status {process.returncode}; "
                f"see {log_path}",
                file=sys.stderr,
            )
            return False
        _write_session(state_path, process.pid, session_id, containers)
    except (OSError, RuntimeError, ValueError) as exc:
        _remove_sidecars(session_id, best_effort=True)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
        print(f"Error: could not start local CRS session: {exc}", file=sys.stderr)
        return False

    print(f"Started {args.crs_path.name} (pid {process.pid})")
    print(f"Session files: {session_dir}")
    print(f"Command output: {log_path}")
    return True


def _stop_process_group(pid: int) -> None:
    try:
        if os.getpgid(pid) != pid:
            raise RuntimeError(
                f"process {pid} is no longer the session leader; refusing to signal it"
            )
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _process_group_is_running(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def handle_local_stop(args) -> bool:
    state_path = _session_dir(args) / "session.json"
    try:
        state = _load_session(state_path)
        if state is None:
            print("No local session is running for this CRS, project, and harness.")
            return True
        pid = state["pid"]
        expected_session_id = _session_id(
            args.crs_path, args.fuzz_proj_path, args.target_harness
        )
        if state.get("session_id") != expected_session_id:
            raise ValueError("session state does not match the requested session")
        if _pid_is_running(pid) and _pid_belongs_to_session(pid, expected_session_id):
            _stop_process_group(pid)
        _remove_sidecars(expected_session_id)
        state_path.unlink(missing_ok=True)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: could not stop local CRS session: {exc}", file=sys.stderr)
        return False
    print(f"Stopped local CRS session (pid {pid})")
    return True
