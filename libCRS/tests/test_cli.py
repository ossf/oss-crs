# SPDX-License-Identifier: MIT
"""Unit tests for libCRS command-line interface dispatching and argument validation."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from libCRS.base import DataType, SourceType
from libCRS.cli.main import DaemonContext, init_crs_utils, main


@pytest.fixture
def mock_crs_utils():
    utils = MagicMock()
    with patch("libCRS.cli.main.init_crs_utils", return_value=utils):
        yield utils


# ===========================================================================
# 1. Initialization and Environment Validation
# ===========================================================================


def test_init_crs_utils_local(monkeypatch):
    monkeypatch.setenv("OSS_CRS_RUN_ENV_TYPE", "local")
    monkeypatch.setattr("libCRS.common._OSS_CRS_RUN_ENV_TYPE", None)
    with patch("socket.gethostbyname", return_value="127.0.0.1"):
        utils = init_crs_utils()
        assert utils.__class__.__name__ == "LocalCRSUtils"


def test_init_crs_utils_unsupported(monkeypatch):
    monkeypatch.setattr("libCRS.cli.main.get_run_env_type", lambda: "unsupported_env")
    with pytest.raises(NotImplementedError, match="CRSUtils not implemented"):
        init_crs_utils()


def test_daemon_context_lifecycle(tmp_path: Path):
    log_file = tmp_path / "daemon.log"
    # Test DaemonContext without forking in test environment
    dc = DaemonContext(log_path=str(log_file))
    assert dc.log_path == str(log_file)
    assert dc.log_file is None


# ===========================================================================
# 2. Build Output Commands
# ===========================================================================


def test_cli_submit_build_output_minimal(mock_crs_utils):
    with patch.object(
        sys, "argv", ["libCRS", "submit-build-output", "out/binary", "bin/binary"]
    ):
        main()
    mock_crs_utils.submit_build_output.assert_called_once_with(
        "out/binary", Path("bin/binary"), rebuild_id=None
    )


def test_cli_submit_build_output_with_rebuild_id(mock_crs_utils):
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "submit-build-output",
            "out/lib.a",
            "lib/lib.a",
            "--rebuild-id",
            "12",
        ],
    ):
        main()
    mock_crs_utils.submit_build_output.assert_called_once_with(
        "out/lib.a", Path("lib/lib.a"), rebuild_id=12
    )


def test_cli_skip_build_output(mock_crs_utils):
    with patch.object(sys, "argv", ["libCRS", "skip-build-output", "target/output"]):
        main()
    mock_crs_utils.skip_build_output.assert_called_once_with("target/output")


def test_cli_download_build_output_defaults(mock_crs_utils):
    with patch.object(
        sys, "argv", ["libCRS", "download-build-output", "out/artifacts", "local/dest"]
    ):
        main()
    mock_crs_utils.download_build_output.assert_called_once_with(
        "out/artifacts", Path("local/dest"), rebuild_id=None
    )


def test_cli_download_build_output_explicit_rebuild_id(mock_crs_utils):
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "download-build-output",
            "out/artifacts",
            "local/dest",
            "--rebuild-id",
            "42",
        ],
    ):
        main()
    mock_crs_utils.download_build_output.assert_called_once_with(
        "out/artifacts", Path("local/dest"), rebuild_id=42
    )


# ===========================================================================
# 3. Source & Directory Registration Commands
# ===========================================================================


def test_cli_download_source_fuzz_proj(mock_crs_utils):
    with patch.object(
        sys, "argv", ["libCRS", "download-source", "fuzz-proj", "dest/fuzz"]
    ):
        main()
    mock_crs_utils.download_source.assert_called_once_with(
        SourceType.FUZZ_PROJ, Path("dest/fuzz")
    )


def test_cli_download_source_target_source(mock_crs_utils):
    with patch.object(
        sys, "argv", ["libCRS", "download-source", "target-source", "dest/target"]
    ):
        main()
    mock_crs_utils.download_source.assert_called_once_with(
        SourceType.TARGET_SOURCE, Path("dest/target")
    )


def test_cli_download_source_invalid_choice():
    with patch.object(
        sys, "argv", ["libCRS", "download-source", "invalid-source", "dest"]
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0


def test_cli_register_submit_dir(mock_crs_utils):
    with (
        patch("libCRS.cli.main.DaemonContext"),
        patch.object(
            sys, "argv", ["libCRS", "register-submit-dir", "pov", "local/povs"]
        ),
    ):
        main()
    mock_crs_utils.register_submit_dir.assert_called_once_with(
        DataType.POV, Path("local/povs")
    )


def test_cli_register_shared_dir(mock_crs_utils):
    with patch.object(
        sys,
        "argv",
        ["libCRS", "register-shared-dir", "local/cache", "shared_cache_dir"],
    ):
        main()
    mock_crs_utils.register_shared_dir.assert_called_once_with(
        Path("local/cache"), "shared_cache_dir"
    )


def test_cli_register_log_dir(mock_crs_utils):
    with patch.object(sys, "argv", ["libCRS", "register-log-dir", "work/logs"]):
        main()
    mock_crs_utils.register_log_dir.assert_called_once_with(Path("work/logs"))


def test_cli_register_fetch_dir(mock_crs_utils):
    with (
        patch("libCRS.cli.main.DaemonContext"),
        patch.object(
            sys, "argv", ["libCRS", "register-fetch-dir", "patch", "inbound/patches"]
        ),
    ):
        main()
    mock_crs_utils.register_fetch_dir.assert_called_once_with(
        DataType.PATCH, Path("inbound/patches")
    )


# ===========================================================================
# 4. Inbound / Outbound Data Transfer
# ===========================================================================


def test_cli_submit_file(mock_crs_utils):
    with patch.object(sys, "argv", ["libCRS", "submit", "seed", "corpus/input1"]):
        main()
    mock_crs_utils.submit.assert_called_once_with(DataType.SEED, Path("corpus/input1"))


def test_cli_submit_invalid_type():
    with patch.object(sys, "argv", ["libCRS", "submit", "unknown-type", "file"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0


def test_cli_submit_harness_fuzz_proj_only(mock_crs_utils):
    with patch.object(
        sys,
        "argv",
        ["libCRS", "submit-harness", "--fuzz-proj-dir", "fuzz/proj/dir"],
    ):
        main()
    mock_crs_utils.submit_harness.assert_called_once_with(
        Path("fuzz/proj/dir"), None, None
    )


def test_cli_submit_harness_with_all_options(mock_crs_utils):
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "submit-harness",
            "--fuzz-proj-dir",
            "fuzz/proj/dir",
            "--target-source-dir",
            "target/src/dir",
            "--name",
            "harness-v1",
        ],
    ):
        main()
    mock_crs_utils.submit_harness.assert_called_once_with(
        Path("fuzz/proj/dir"),
        Path("target/src/dir"),
        "harness-v1",
    )


def test_cli_fetch(mock_crs_utils, capsys):
    mock_crs_utils.fetch.return_value = ["file1.patch", "file2.patch"]
    with patch.object(sys, "argv", ["libCRS", "fetch", "patch", "local/patches"]):
        main()
    mock_crs_utils.fetch.assert_called_once_with(DataType.PATCH, Path("local/patches"))
    captured = capsys.readouterr()
    assert "file1.patch\nfile2.patch" in captured.out


# ===========================================================================
# 5. Build & Runner Sidecar Invocations
# ===========================================================================


def test_cli_apply_patch_build_minimal(mock_crs_utils):
    mock_crs_utils.apply_patch_build.return_value = 0
    with patch.object(
        sys,
        "argv",
        ["libCRS", "apply-patch-build", "patch.diff", "build_response_dir"],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    mock_crs_utils.apply_patch_build.assert_called_once_with(
        Path("patch.diff"),
        Path("build_response_dir"),
        builder=None,
        builder_name=None,
        rebuild_id=None,
    )


def test_cli_apply_patch_build_with_builder_and_rebuild_id(mock_crs_utils):
    mock_crs_utils.apply_patch_build.return_value = 42
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "apply-patch-build",
            "patch.diff",
            "build_response_dir",
            "--builder",
            "custom-builder",
            "--builder-name",
            "builder-inst",
            "--rebuild-id",
            "7",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 42
    mock_crs_utils.apply_patch_build.assert_called_once_with(
        Path("patch.diff"),
        Path("build_response_dir"),
        builder="custom-builder",
        builder_name="builder-inst",
        rebuild_id=7,
    )


def test_cli_build_project_minimal(mock_crs_utils):
    mock_crs_utils.build_project.return_value = 0
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "build-project",
            "--response-dir",
            "response/dir",
            "--fuzz-proj-dir",
            "fuzz/dir",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    mock_crs_utils.build_project.assert_called_once_with(
        Path("response/dir"),
        fuzz_proj_dir=Path("fuzz/dir"),
        target_source_dir=None,
        builder=None,
        builder_name=None,
        rebuild_id=None,
    )


def test_cli_build_project_with_target_source_and_rebuild_id(mock_crs_utils):
    mock_crs_utils.build_project.return_value = 1
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "build-project",
            "--response-dir",
            "response/dir",
            "--target-source-dir",
            "target/dir",
            "--rebuild-id",
            "99",
            "--builder",
            "b-sidecar",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
    mock_crs_utils.build_project.assert_called_once_with(
        Path("response/dir"),
        fuzz_proj_dir=None,
        target_source_dir=Path("target/dir"),
        builder="b-sidecar",
        builder_name=None,
        rebuild_id=99,
    )


def test_cli_run_pov(mock_crs_utils):
    mock_crs_utils.run_pov.return_value = 137
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "run-pov",
            "crash.bin",
            "pov_response_dir",
            "--harness",
            "my_fuzz_harness",
            "--rebuild-id",
            "55",
            "--builder",
            "custom-runner",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 137
    mock_crs_utils.run_pov.assert_called_once_with(
        Path("crash.bin"),
        "my_fuzz_harness",
        Path("pov_response_dir"),
        "55",
        "custom-runner",
    )


def test_cli_apply_patch_test(mock_crs_utils):
    mock_crs_utils.apply_patch_test.return_value = 0
    with patch.object(
        sys,
        "argv",
        [
            "libCRS",
            "apply-patch-test",
            "patch.diff",
            "test_response_dir",
            "--builder",
            "test-builder",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    mock_crs_utils.apply_patch_test.assert_called_once_with(
        Path("patch.diff"), Path("test_response_dir"), "test-builder"
    )


def test_cli_get_service_domain(mock_crs_utils, capsys):
    mock_crs_utils.get_service_domain.return_value = "builder-sidecar.my-crs"
    with patch.object(sys, "argv", ["libCRS", "get-service-domain", "builder-sidecar"]):
        main()
    mock_crs_utils.get_service_domain.assert_called_once_with("builder-sidecar")
    captured = capsys.readouterr()
    assert "builder-sidecar.my-crs" in captured.out


# ===========================================================================
# 6. MCP Commands & Payload Processing
# ===========================================================================


def test_cli_mcp_list_plain(capsys):
    mock_client = MagicMock()
    mock_client.list_tools.return_value = [
        {"name": "grep_tool", "description": "Search code patterns\nMultiline detail"},
        {"name": "read_tool", "description": ""},
    ]
    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(sys, "argv", ["libCRS", "mcp", "list"]),
    ):
        main()
    mock_client.list_tools.assert_called_once_with(None)
    captured = capsys.readouterr()
    assert "grep_tool - Search code patterns" in captured.out
    assert "read_tool" in captured.out


def test_cli_mcp_list_json(capsys):
    mock_client = MagicMock()
    tools_payload = [{"name": "tool1"}]
    mock_client.list_tools.return_value = tools_payload
    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(
            sys, "argv", ["libCRS", "mcp", "list", "--json", "--server", "srv1"]
        ),
    ):
        main()
    mock_client.list_tools.assert_called_once_with("srv1")
    captured = capsys.readouterr()
    assert '"name": "tool1"' in captured.out


def test_cli_mcp_describe_plain(capsys):
    mock_client = MagicMock()
    mock_client.describe.return_value = {
        "name": "fuzz_target",
        "description": "Fuzzing utility",
        "mcp_info": {"alias": "fuzzer_service"},
        "inputSchema": {
            "type": "object",
            "properties": {"timeout": {"type": "integer"}},
        },
    }
    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(sys, "argv", ["libCRS", "mcp", "describe", "fuzz_target"]),
    ):
        main()
    mock_client.describe.assert_called_once_with("fuzz_target", None)
    captured = capsys.readouterr()
    assert "Tool: fuzz_target" in captured.out
    assert "Server: fuzzer_service" in captured.out
    assert "Description: Fuzzing utility" in captured.out


def test_cli_mcp_describe_json(capsys):
    mock_client = MagicMock()
    mock_client.describe.return_value = {"name": "test_tool"}
    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(sys, "argv", ["libCRS", "mcp", "describe", "test_tool", "--json"]),
    ):
        main()
    captured = capsys.readouterr()
    assert '"name": "test_tool"' in captured.out


def test_cli_mcp_call_with_args_file(tmp_path: Path, capsys):
    args_file = tmp_path / "args.json"
    args_file.write_text('{"query": "CVE-2024"}')

    mock_client = MagicMock()
    mock_client.call_tool.return_value = {
        "content": [{"type": "text", "text": "Matched 1 result"}]
    }

    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(
            sys,
            "argv",
            ["libCRS", "mcp", "call", "search_cve", "--args-file", str(args_file)],
        ),
    ):
        main()

    mock_client.call_tool.assert_called_once_with(
        "search_cve", {"query": "CVE-2024"}, None, timeout=None
    )
    captured = capsys.readouterr()
    assert "Matched 1 result" in captured.out


def test_cli_mcp_call_invalid_args_file_exits():
    with patch.object(
        sys,
        "argv",
        ["libCRS", "mcp", "call", "any_tool", "--args-file", "nonexistent_file.json"],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2


def test_cli_mcp_call_invalid_json_string():
    with patch.object(
        sys,
        "argv",
        ["libCRS", "mcp", "call", "any_tool", "--args", "{invalid json"],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2


def test_cli_mcp_call_non_dict_json_args():
    with patch.object(
        sys,
        "argv",
        ["libCRS", "mcp", "call", "any_tool", "--args", '["list", "not", "dict"]'],
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2


def test_cli_mcp_call_error_flag_exits_1():
    mock_client = MagicMock()
    mock_client.call_tool.return_value = {
        "isError": True,
        "content": [{"type": "text", "text": "Execution failed"}],
    }
    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(sys, "argv", ["libCRS", "mcp", "call", "failing_tool"]),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


def test_cli_mcp_call_json_mode_with_error(capsys):
    mock_client = MagicMock()
    mock_client.call_tool.return_value = {
        "isError": True,
        "structuredContent": {"error": "bad request"},
    }
    with (
        patch("libCRS.cli.main._mcp_client_from_env", return_value=mock_client),
        patch.object(sys, "argv", ["libCRS", "mcp", "call", "tool", "--json"]),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
    captured = capsys.readouterr()
    assert '"isError": true' in captured.out
