# SPDX-License-Identifier: MIT
"""Tests for the standalone ``list-harnesses`` command."""

import argparse
from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.cli import list_harnesses as module
from oss_crs.src.cli import crs_compose as cli_module


def _make_project(tmp_path: Path, language: str = "c") -> Path:
    project = tmp_path / "sample-project"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM example/base-builder\n")
    (project / "build.sh").write_text("#!/bin/bash\n")
    (project / "project.yaml").write_text(f"language: {language}\n")
    return project


def _write_native_harness(path: Path) -> None:
    # A minimal ELF header marked ET_EXEC, followed by the libFuzzer entrypoint.
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # little endian
    header[16:18] = (2).to_bytes(2, "little")
    path.write_bytes(bytes(header) + b"\0LLVMFuzzerTestOneInput\0")


def test_find_harnesses_uses_native_entrypoint_marker(tmp_path: Path) -> None:
    _write_native_harness(tmp_path / "xml_fuzzer")
    _write_native_harness(tmp_path / "second_fuzzer")
    (tmp_path / "ordinary_executable").write_bytes(b"\x7fELF but no marker")
    (tmp_path / "target.dict").write_text("keyword=example")
    (tmp_path / "llvm-symbolizer").write_bytes(b"LLVMFuzzerTestOneInput")

    assert module.find_harnesses(tmp_path, "c++") == [
        "second_fuzzer",
        "xml_fuzzer",
    ]


def test_find_harnesses_recognizes_jvm_launcher(tmp_path: Path) -> None:
    (tmp_path / "JsonFuzzer").write_text(
        '#!/bin/bash\n./jazzer_driver --target_class=JsonFuzzer "$@"\n'
    )
    (tmp_path / "jazzer_driver").write_bytes(b"LLVMFuzzerTestOneInput")
    (tmp_path / "project.jar").write_bytes(b"jazzer_driver --target_class=Nope")

    assert module.find_harnesses(tmp_path, "jvm") == ["JsonFuzzer"]


def test_find_harnesses_recognizes_python_launcher(tmp_path: Path) -> None:
    (tmp_path / "fuzz_yaml").write_text(
        "#!/usr/bin/python3\nimport atheris\natheris.Fuzz()\n"
    )
    (tmp_path / "module.py").write_text("import atheris\natheris.Fuzz()\n")

    assert module.find_harnesses(tmp_path, "python") == ["fuzz_yaml"]


def test_find_harnesses_recognizes_javascript_launcher(tmp_path: Path) -> None:
    (tmp_path / "fuzz_parser").write_text(
        "#!/bin/bash\nnode ./node_modules/.bin/jazzer fuzz_parser.js\n"
    )
    (tmp_path / "node_helper").write_text("#!/bin/bash\nnode helper.js\n")

    assert module.find_harnesses(tmp_path, "javascript") == ["fuzz_parser"]


def test_list_harnesses_parser_does_not_require_compose_file(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    module.add_list_harnesses_command(subparsers, tmp_path / "default-work")

    args = parser.parse_args(
        ["list-harnesses", "--fuzz-proj-path", str(tmp_path / "project")]
    )

    assert args.target_proj_path == tmp_path / "project"
    assert args.work_dir == tmp_path / "default-work"
    assert not hasattr(args, "compose_file")


def test_cli_dispatches_list_harnesses_without_compose(
    tmp_path: Path, monkeypatch
) -> None:
    received = []

    def fake_handle(args):
        received.append(args)
        return True

    monkeypatch.setattr(cli_module, "handle_list_harnesses", fake_handle)
    monkeypatch.setattr(
        cli_module.sys,
        "argv",
        [
            "oss-crs",
            "list-harnesses",
            "--fuzz-proj-path",
            str(tmp_path / "project"),
        ],
    )

    assert cli_module.cli() is True
    assert received[0].target_proj_path == (tmp_path / "project").resolve()


def test_compile_uses_oss_fuzz_environment_and_compile_command(
    tmp_path: Path, monkeypatch
) -> None:
    project = _make_project(tmp_path)
    target = module.Target(tmp_path / "work", project, None)
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._compile_project(target, "sample:tag", tmp_path / "out") is True
    command = calls[0]
    assert command[:8] == [
        "docker",
        "run",
        "--privileged",
        "--shm-size=2g",
        "--platform",
        "linux/amd64",
        "--rm",
        "-e",
    ]
    assert "FUZZING_ENGINE=libfuzzer" in command
    assert "SANITIZER=address" in command
    assert "FUZZING_LANGUAGE=c" in command
    assert f"{(tmp_path / 'out').resolve()}:/out" in command
    assert f"{(tmp_path / 'work').resolve()}:/work" in command
    assert command[-2:] == ["sample:tag", "compile"]


def test_cached_build_is_reused_until_project_inputs_change(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project = _make_project(tmp_path)
    args = SimpleNamespace(
        target_proj_path=project,
        work_dir=tmp_path / ".oss-crs-workdir",
    )
    builds: list[bool] = []
    compiles: list[Path] = []

    monkeypatch.setattr(module, "_containing_repository_head", lambda _path: "oss-head")
    monkeypatch.setattr(module, "_remote_repository_head", lambda _url: "source-head")

    def fake_build(_target, *, force_rebuild=False, console=None):
        builds.append(force_rebuild)
        return "sample:tag"

    def fake_compile(_target, _image, out_dir):
        compiles.append(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_native_harness(out_dir / "sample_fuzzer")
        return True

    monkeypatch.setattr(module.Target, "build_docker_image", fake_build)
    monkeypatch.setattr(module, "_compile_project", fake_compile)

    assert module.handle_list_harnesses(args) is True
    assert module.handle_list_harnesses(args) is True
    assert builds == [True]
    assert len(compiles) == 1
    assert capsys.readouterr().out.splitlines() == ["sample_fuzzer", "sample_fuzzer"]

    (project / "build.sh").write_text("#!/bin/bash\necho changed\n")
    assert module.handle_list_harnesses(args) is True
    assert builds == [True, True]
    assert len(compiles) == 2


def test_cache_cleanup_uses_target_image_for_root_owned_files(
    tmp_path: Path, monkeypatch
) -> None:
    cache_dir = tmp_path / "cache"
    out_dir = cache_dir / "out"
    out_dir.mkdir(parents=True)
    commands: list[list[str]] = []

    monkeypatch.setattr(
        module.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(PermissionError)
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._clear_build_artifacts(cache_dir, "sample:tag")

    assert commands == [
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{cache_dir}:/data",
            "--entrypoint",
            "rm",
            "sample:tag",
            "-rf",
            "/data/out",
        ]
    ]


def test_repository_head_change_invalidates_cached_build(
    tmp_path: Path, monkeypatch
) -> None:
    project = _make_project(tmp_path)
    args = SimpleNamespace(
        target_proj_path=project,
        work_dir=tmp_path / ".oss-crs-workdir",
    )
    source_heads = iter(("source-head-1", "source-head-2"))
    build_count = 0

    monkeypatch.setattr(module, "_containing_repository_head", lambda _path: "oss-head")
    monkeypatch.setattr(
        module, "_remote_repository_head", lambda _url: next(source_heads)
    )

    def fake_build(_target, *, force_rebuild=False, console=None):
        nonlocal build_count
        assert force_rebuild is True
        build_count += 1
        return "sample:tag"

    def fake_compile(_target, _image, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        return True

    monkeypatch.setattr(module.Target, "build_docker_image", fake_build)
    monkeypatch.setattr(module, "_compile_project", fake_compile)

    assert module.handle_list_harnesses(args) is True
    assert module.handle_list_harnesses(args) is True
    assert build_count == 2
