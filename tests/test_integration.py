import json

from verify_and_repair.controller import Controller
from verify_and_repair.execution import run_command
from verify_and_repair.models import Budget
from verify_and_repair.policy import Candidate


def test_real_preflight_visible_checks_evaluator_and_persistence(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert run_command(["git", "init"], repo, 10).outcome == "pass"
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    evaluator = repo / "evaluator"
    evaluator.mkdir()
    (evaluator / "test_hidden.py").write_text("HIDDEN_DIAGNOSTIC_SENTINEL = (\n")
    controller = Controller(
        task_id="smoke",
        repo=repo,
        task_text="verify",
        condition="adaptive",
        working_tests="python -m pytest -q tests",
        hidden_tests='python -c "raise SystemExit(0)"',
        llm_mode="mock",
        output=tmp_path / "result.json",
        budget=Budget(),
    )
    monkeypatch.setattr(
        controller,
        "candidates",
        lambda: [Candidate("property-tests", "python -m pytest -q tests", 30, 0.9)],
    )
    record = controller.run()
    assert record.status == "solved", [
        (event.name, event.stderr_tail, event.stdout_tail) for event in record.events
    ]
    assert record.config["preflight"]["ok"]
    assert record.agency_sessions == 0
    assert [event.name for event in record.events] == [
        "ruff",
        "targeted-tests",
        "mypy",
        "property-tests",
        "hidden-evaluation",
    ]
    assert json.loads(controller.output.read_text())["status"] == "solved"
    assert "HIDDEN_DIAGNOSTIC_SENTINEL" not in controller.packet()
    assert all(
        "evaluator" not in item["detail"]
        for group in json.loads(controller.packet())["evidence_delta"].values()
        for item in group
    )
