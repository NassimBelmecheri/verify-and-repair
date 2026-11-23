import os
import sys

from verify_and_repair.execution import run_command, split_command


def test_windows_quotes_are_removed_without_losing_backslashes(monkeypatch):
    from verify_and_repair import execution

    monkeypatch.setattr(execution.os, "name", "nt")
    assert split_command('python -m pytest "tests\\test reservation.py"') == [
        "python",
        "-m",
        "pytest",
        "tests\\test reservation.py",
    ]


def test_python_command_uses_current_environment(tmp_path):
    result = run_command('python -c "import sys; print(sys.executable)"', tmp_path, 10)
    assert result.outcome == "pass"
    assert result.stdout.strip() == sys.executable


def test_project_checks_preserve_the_callers_python_and_path(tmp_path, monkeypatch):
    from verify_and_repair import execution

    path = os.environ["PATH"]
    monkeypatch.setattr(
        execution.sys, "executable", str(tmp_path / "missing-python.exe")
    )
    result = run_command(
        "python -c \"import os; print(os.environ['PATH'])\"",
        tmp_path,
        10,
        use_current_python=False,
    )
    assert result.outcome == "pass"
    assert result.stdout.strip() == path


def test_child_environment_removes_inherited_approval_without_mutating_parent(
    tmp_path, monkeypatch
):
    import os

    monkeypatch.setenv("COPILOT_ALLOW_ALL", "true")
    result = run_command(
        [
            sys.executable,
            "-c",
            (
                "import os; print(os.getenv('COPILOT_ALLOW_ALL', 'absent')); "
                "print(os.getenv('COPILOT_PLUGIN_DIR_ONLY'))"
            ),
        ],
        tmp_path,
        10,
        env={"COPILOT_ALLOW_ALL": None, "COPILOT_PLUGIN_DIR_ONLY": "true"},
    )
    assert result.outcome == "pass" and result.stdout.splitlines() == ["absent", "true"]
    assert os.environ["COPILOT_ALLOW_ALL"] == "true"


def test_timeout_stops_process(tmp_path):
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(20)"], tmp_path, 0.05
    )
    assert result.outcome == "timeout"
    assert result.duration_ms < 5000


def test_subprocess_timeout_is_not_reported_as_pass(tmp_path, monkeypatch):
    from verify_and_repair import execution

    monkeypatch.setattr(
        execution.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FileNotFoundError("missing executable")
        ),
    )
    result = run_command(["missing"], tmp_path, 1)
    assert result.outcome == "error"
    assert "missing executable" in result.stderr
