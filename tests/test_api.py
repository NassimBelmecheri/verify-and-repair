import json

import pytest

from verify_and_repair import api, cli
from verify_and_repair.api import TaskConfig
from verify_and_repair.models import RunRecord


@pytest.fixture
def config_path(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "logic.py").write_text("def inc(x):\n    return x - 1\n")
    path = tmp_path / "verify-and-repair.json"
    path.write_text(
        json.dumps(
            {
                "repo": ".",
                "implementation_files": ["src\\logic.py"],
                "working_tests": "python -m pytest -q tests",
            }
        )
    )
    return path


def test_relative_config_and_windows_paths_are_portable(config_path):
    config = TaskConfig.load(config_path)
    assert config.repo == config_path.parent
    assert config.implementation_files == ("src/logic.py",)
    assert config.profile == "lean"
    assert config.budget.max_verifier_wall_seconds == 180


@pytest.mark.parametrize(
    "update",
    [
        {"hidden_tests": "private oracle"},
        {"implementation_files": ["src/logic.py", "src\\logic.py"]},
        {"implementation_files": ["../outside.py"]},
        {"profile": "unsupported"},
        {"profile": "tools"},
        {"profile": "localized"},
        {"local_solver_seconds": 3},
        {"backend": "unsupported"},
        {"copilot_exe": 42},
        {"reasoning_effort": "unsupported"},
        {"budget": {"max_model_requests": 1.5}},
        {"budget": {"max_model_requests": True}},
        {"budget": {"unknown": 10}},
    ],
)
def test_invalid_configuration_fails_before_any_model_call(config_path, update):
    value = json.loads(config_path.read_text())
    value.update(update)
    config_path.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        TaskConfig.load(config_path)


@pytest.mark.parametrize("backend", ["agency", "copilot"])
def test_lean_uses_existing_controller_and_keeps_private_evaluation_separate(
    config_path, tmp_path, monkeypatch, backend
):
    calls = []

    class FakeController:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.record = RunRecord(
                "1.5", "run", "task", "adaptive", str(kwargs["repo"]), "Fix inc", ""
            )
            self.route = "source"

        def run(self):
            assert self.route == "source"
            return self.record

    monkeypatch.setattr(api, "HybridController", FakeController)
    config = TaskConfig.load(config_path)
    config.backend = backend
    result = api.solve(config, "Fix inc", tmp_path / "out.json")
    assert result.config["profile"] == "lean"
    assert calls[0]["defer_hidden_evaluation"] is True
    assert calls[0]["llm_mode"] == backend
    assert "private oracle" not in json.dumps(calls[0], default=str)
    assert calls[0]["implementation_files"] == ("src/logic.py",)
    assert "local_solver_seconds" not in calls[0]


@pytest.mark.parametrize("profile", ["tools", "localized"])
def test_removed_api_profiles_fail_before_controller_creation(
    config_path, tmp_path, monkeypatch, profile
):
    def forbidden(**kwargs):
        pytest.fail("Removed profile must fail before model/controller work")

    monkeypatch.setattr(api, "HybridController", forbidden)
    config = TaskConfig.load(config_path)
    with pytest.raises(ValueError, match="supported profile"):
        api.solve(config, "Fix inc", tmp_path / "out.json", profile=profile)
    config.profile = profile
    with pytest.raises(ValueError, match="supported profile"):
        api.solve(config, "Fix inc", tmp_path / "out.json")


@pytest.mark.parametrize("command", ["configure", "solve"])
@pytest.mark.parametrize("profile", ["tools", "localized"])
def test_removed_cli_profiles_are_not_available(
    config_path, monkeypatch, command, profile
):
    args = (
        ["--repo", str(config_path.parent), "--implementation-file", "src/logic.py"]
        if command == "configure"
        else ["--config", str(config_path), "--task", "Fix inc"]
    )
    monkeypatch.setattr(
        "sys.argv", ["verify-and-repair", command, *args, "--profile", profile]
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_configure_refuses_to_overwrite_existing_config(config_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify-and-repair",
            "configure",
            "--repo",
            str(config_path.parent),
            "--implementation-file",
            "src/logic.py",
        ],
    )
    before = config_path.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and config_path.read_bytes() == before


def test_cli_solve_reports_failure_as_nonzero(config_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify-and-repair",
            "solve",
            "--config",
            str(config_path),
            "--task",
            "Fix inc",
        ],
    )
    record = RunRecord(
        "1.5", "run", "task", "adaptive", str(config_path.parent), "Fix inc", ""
    )
    record.termination_reason = "agency_sessions"
    monkeypatch.setattr(cli, "solve", lambda *args, **kwargs: record)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["verified"] and result["model_requests"] == 0


def test_configure_handles_repo_and_config_on_different_drives(
    config_path, monkeypatch
):
    target = config_path.parent / "alternate.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify-and-repair",
            "configure",
            "--repo",
            str(config_path.parent),
            "--config",
            str(target),
            "--implementation-file",
            "src/logic.py",
        ],
    )

    def different_drives(*args):
        raise ValueError("path is on another drive")

    monkeypatch.setattr(cli.os.path, "relpath", different_drives)
    cli.main()
    assert TaskConfig.load(target).repo == config_path.parent


def test_configure_and_solve_can_select_copilot(config_path, monkeypatch, capsys):
    target = config_path.parent / "copilot.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify-and-repair",
            "configure",
            "--repo",
            str(config_path.parent),
            "--implementation-file",
            "src/logic.py",
            "--config",
            str(target),
            "--backend",
            "copilot",
            "--copilot-exe",
            r"C:\tools\copilot.exe",
        ],
    )
    cli.main()
    loaded = TaskConfig.load(target)
    assert loaded.backend == "copilot"
    assert loaded.copilot_exe == r"C:\tools\copilot.exe"
    capsys.readouterr()

    def fake_solve(config, *args, **kwargs):
        assert config.backend == "agency"
        record = RunRecord("1.5", "run", "task", "adaptive", str(config.repo), "", "")
        record.declared_verified = True
        return record

    monkeypatch.setattr(cli, "solve", fake_solve)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify-and-repair",
            "solve",
            "--config",
            str(target),
            "--backend",
            "agency",
            "--task",
            "Fix inc",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["backend"] == "agency"
