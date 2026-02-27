"""Unit tests for required_inputs validation in CRSCompose."""

from pathlib import Path

from oss_crs.src.crs_compose import CRSCompose


class _FakeCRSConfig:
    def __init__(self, required_inputs):
        self.required_inputs = required_inputs


class _FakeCRS:
    def __init__(self, name, required_inputs):
        self.name = name
        self.config = _FakeCRSConfig(required_inputs)


def _make_compose(crs_list):
    """Create a minimal CRSCompose-like object with a crs_list for validation."""
    compose = object.__new__(CRSCompose)
    compose.crs_list = crs_list
    return compose


def test_no_required_inputs_always_passes():
    compose = _make_compose([_FakeCRS("crs-a", None)])
    assert compose._validate_required_inputs() is True


def test_empty_required_inputs_always_passes():
    compose = _make_compose([_FakeCRS("crs-a", [])])
    assert compose._validate_required_inputs() is True


def test_required_diff_passes_when_provided(tmp_path):
    compose = _make_compose([_FakeCRS("crs-a", ["diff"])])
    diff_file = tmp_path / "ref.diff"
    diff_file.write_text("patch")
    assert compose._validate_required_inputs(diff=diff_file) is True


def test_required_diff_fails_when_missing():
    compose = _make_compose([_FakeCRS("crs-a", ["diff"])])
    assert compose._validate_required_inputs() is False


def test_required_pov_passes_with_pov_file(tmp_path):
    compose = _make_compose([_FakeCRS("crs-a", ["pov"])])
    pov_file = tmp_path / "crash.pov"
    pov_file.write_text("data")
    assert compose._validate_required_inputs(pov=pov_file) is True


def test_required_pov_passes_with_pov_dir(tmp_path):
    compose = _make_compose([_FakeCRS("crs-a", ["pov"])])
    assert compose._validate_required_inputs(pov_dir=tmp_path) is True


def test_required_seed_passes_when_provided(tmp_path):
    compose = _make_compose([_FakeCRS("crs-a", ["seed"])])
    assert compose._validate_required_inputs(seed_dir=tmp_path) is True


def test_required_bug_candidate_passes_with_file(tmp_path):
    compose = _make_compose([_FakeCRS("crs-a", ["bug-candidate"])])
    bc = tmp_path / "report.sarif"
    bc.write_text("{}")
    assert compose._validate_required_inputs(bug_candidate=bc) is True


def test_required_bug_candidate_passes_with_dir(tmp_path):
    compose = _make_compose([_FakeCRS("crs-a", ["bug-candidate"])])
    assert compose._validate_required_inputs(bug_candidate_dir=tmp_path) is True


def test_multiple_crs_all_satisfied(tmp_path):
    diff_file = tmp_path / "ref.diff"
    diff_file.write_text("patch")
    compose = _make_compose([
        _FakeCRS("dgf", ["diff", "bug-candidate"]),
        _FakeCRS("afl", None),
    ])
    bc = tmp_path / "report.sarif"
    bc.write_text("{}")
    assert compose._validate_required_inputs(diff=diff_file, bug_candidate=bc) is True


def test_multiple_crs_one_unsatisfied(tmp_path):
    diff_file = tmp_path / "ref.diff"
    diff_file.write_text("patch")
    compose = _make_compose([
        _FakeCRS("dgf", ["diff", "bug-candidate"]),
        _FakeCRS("afl", None),
    ])
    # Only diff provided, bug-candidate missing
    assert compose._validate_required_inputs(diff=diff_file) is False


def test_required_pov_fails_when_missing():
    compose = _make_compose([_FakeCRS("crs-a", ["pov"])])
    assert compose._validate_required_inputs() is False


def test_required_seed_fails_when_missing():
    compose = _make_compose([_FakeCRS("crs-a", ["seed"])])
    assert compose._validate_required_inputs() is False


def test_required_bug_candidate_fails_when_missing():
    compose = _make_compose([_FakeCRS("crs-a", ["bug-candidate"])])
    assert compose._validate_required_inputs() is False


def test_all_four_inputs_required_and_satisfied(tmp_path):
    compose = _make_compose([
        _FakeCRS("crs-a", ["diff", "pov", "seed", "bug-candidate"])
    ])
    diff_file = tmp_path / "ref.diff"
    diff_file.write_text("patch")
    pov_file = tmp_path / "crash.pov"
    pov_file.write_text("data")
    seed_subdir = tmp_path / "seeds"
    seed_subdir.mkdir()
    bc = tmp_path / "report.sarif"
    bc.write_text("{}")
    assert compose._validate_required_inputs(
        diff=diff_file, pov=pov_file, seed_dir=seed_subdir, bug_candidate=bc
    ) is True


def test_error_message_includes_crs_name_and_missing_inputs(capsys):
    compose = _make_compose([_FakeCRS("my-dgf", ["diff", "seed"])])
    result = compose._validate_required_inputs()
    assert result is False
    captured = capsys.readouterr()
    assert "my-dgf" in captured.out
    assert "diff" in captured.out
    assert "seed" in captured.out
    assert "--diff" in captured.out
    assert "--seed" in captured.out


def test_multiple_crs_both_unsatisfied(capsys):
    compose = _make_compose([
        _FakeCRS("crs-a", ["diff"]),
        _FakeCRS("crs-b", ["pov"]),
    ])
    result = compose._validate_required_inputs()
    assert result is False
    captured = capsys.readouterr()
    assert "crs-a" in captured.out
    assert "crs-b" in captured.out
