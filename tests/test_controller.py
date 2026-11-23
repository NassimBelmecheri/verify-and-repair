import json

import pytest

from verify_and_repair import controller as module
from verify_and_repair.backends import COPILOT_REPAIR_TOOLS, copilot_arguments
from verify_and_repair.controller import Controller
from verify_and_repair.execution import CommandResult
from verify_and_repair.models import Budget, Event
from verify_and_repair.policy import Candidate


@pytest.fixture
def harness(tmp_path, monkeypatch):
    class Harness:
        def __init__(self):
            self.repairs = 0
            self.packets = []
            self.timeouts = []
            self.request_counts = [9, 2, 1]
            self.agency_commands = []
            self.usage_missing = False
            self.unchanged = False
            self.ruff_failure = True

        def result(self, command, cwd, timeout):
            self.timeouts.append(timeout)
            assert timeout > 0
            argv = (
                module.split_command(command) if isinstance(command, str) else command
            )
            name = " ".join(argv)
            if argv[0] == "agency":
                self.agency_commands.append(argv)
                self.packets.append(json.loads(argv[argv.index("--prompt") + 1]))
                count = self.request_counts[self.repairs]
                self.repairs += 1
                if not self.usage_missing:
                    usage = {
                        "totalPremiumRequestCost": 1,
                        "modelMetrics": {
                            "model": {
                                "requests": {"count": count},
                                "usage": {"inputTokens": 100},
                            }
                        },
                    }
                    Path = module.Path
                    Path(argv[argv.index("--usage-output-file") + 1]).write_text(
                        json.dumps(usage)
                    )
                return CommandResult(argv, 0, "pass", 100, "", "")
            stage = 0 if self.unchanged else self.repairs
            detail = ""
            if "ruff" in name and self.ruff_failure and stage < 2:
                detail = "unused import"
            elif "mypy" in name and stage == 1:
                detail = "incompatible assignment"
            elif "visible" in name and stage == 0:
                detail = "assert capacity == 2"
            elif "hidden" in name:
                detail = "SECRET hidden failure"
            return CommandResult(
                argv, int(bool(detail)), "fail" if detail else "pass", 1, detail, ""
            )

    fake = Harness()
    monkeypatch.setattr(module, "run_command", fake.result)
    monkeypatch.setattr(module, "revision", lambda repo: f"revision-{fake.repairs}")
    monkeypatch.setattr(module, "changed_files", lambda repo: ["src\\reservation.py"])
    monkeypatch.setattr(module, "diff_summary", lambda repo: "reservation.py | +1")
    controller = Controller(
        task_id="test",
        repo=tmp_path,
        task_text="repair reservation",
        condition="adaptive",
        working_tests="visible",
        hidden_tests="hidden",
        llm_mode="agency",
        output=tmp_path / "result.json",
        budget=Budget(),
    )
    monkeypatch.setattr(controller, "preflight", lambda: {"ok": True})
    monkeypatch.setattr(controller, "resolve_agency", lambda: "agency")
    monkeypatch.setattr(
        controller, "candidates", lambda: [Candidate("smt-z3", "strong", 10, 0.85)]
    )
    return controller, fake


@pytest.mark.parametrize("condition", ["adaptive", "fixed", "baseline"])
def test_multi_repair_progression(harness, condition):
    controller, fake = harness
    controller.record.condition = condition
    if condition == "baseline":
        fake.ruff_failure = False
    record = controller.run()
    if condition == "baseline":
        assert record.agency_sessions == 1
    else:
        assert record.agency_sessions == 2
        second = fake.packets[1]["evidence_delta"]
        assert {item["verifier"] for item in second["resolved"]} == {"targeted-tests"}
        assert {item["verifier"] for item in second["remaining"]} == {"ruff"}
        assert {item["verifier"] for item in second["new"]} == {"mypy"}
    assert record.declared_verified
    assert record.status == "false_verification"
    assert record.model_requests == sum(fake.request_counts[: record.agency_sessions])
    assert record.llm_calls == record.agency_sessions
    repairs = [event for event in record.events if event.kind == "llm"]
    assert all("evidence_delta" in event.metadata for event in repairs)
    assert record.events[-1].kind == "evaluator"
    assert "SECRET" not in json.dumps(fake.packets)
    assert "hidden" not in controller.latest
    assert (
        json.loads(controller.output.read_text())["agency_sessions"]
        == record.agency_sessions
    )


def test_unchanged_evidence_suppresses_repair(harness):
    controller, fake = harness
    fake.unchanged = True
    record = controller.run()
    assert record.agency_sessions == 1
    assert not record.declared_verified
    assert record.termination_reason == "no_useful_action"
    repair = next(event for event in record.events if event.kind == "llm")
    assert not repair.metadata["evidence_delta"]["resolved"]
    assert repair.metadata["evidence_delta"]["remaining"]
    assert not repair.metadata["useful"]


def test_crosshair_without_checkable_functions_is_not_confirmation(harness):
    controller, _ = harness
    result = CommandResult(
        [], 0, "pass", 1, "", "WARNING: No checkable functions found"
    )
    event = controller.add_result(
        "symbolic-crosshair", result, "before", "after", "now"
    )
    assert event.outcome == "unknown"
    assert event.metadata["not_applicable"]


def test_external_grading_stays_unknown_until_independent_evaluation(harness):
    controller, fake = harness
    controller.defer_hidden_evaluation = True
    record = controller.run()
    assert fake.repairs == 2
    assert record.declared_verified
    assert record.hidden_correct is None
    assert record.false_verification is None
    assert not any(event.kind == "evaluator" for event in record.events)


def test_repair_sessions_disable_nested_agents_and_workflows(harness):
    controller, fake = harness
    controller.run()
    assert fake.agency_commands
    for argv in fake.agency_commands:
        index = argv.index("--excluded-tools")
        assert argv[index + 1 : index + 3] == ["task", "run_dynamic_workflow"]


@pytest.mark.parametrize(
    ("budget_field", "limit", "reason"),
    [
        ("max_agency_sessions", 1, "agency_sessions"),
        ("max_llm_calls", 1, "agency_sessions"),
        ("max_model_requests", 9, "model_requests"),
        ("max_actions", 4, "actions"),
        ("max_verifier_wall_seconds", 0.002, "verifier_time"),
        ("max_wall_seconds", 0, "wall_time"),
    ],
)
def test_budget_termination(harness, budget_field, limit, reason):
    controller, fake = harness
    setattr(controller.budget, budget_field, limit)
    record = controller.run()
    assert record.status == "budget_exhausted"
    assert record.termination_reason == reason
    assert record.agency_sessions <= 1
    assert len(record.events) <= controller.budget.max_actions
    assert all(
        timeout <= controller.budget.max_wall_seconds for timeout in fake.timeouts
    )


def test_request_overrun_is_explicit_and_stops_sessions(harness):
    controller, fake = harness
    fake.unchanged = False
    controller.budget.max_model_requests = 8
    record = controller.run()
    assert record.model_requests == 9
    assert record.model_request_overrun == 1
    assert record.agency_sessions == 1
    assert record.termination_reason == "model_requests"


def test_missing_usage_does_not_allow_unmetered_repair(harness):
    controller, fake = harness
    fake.usage_missing = True
    record = controller.run()
    assert not record.model_usage_complete
    assert record.agency_sessions == 1
    assert record.termination_reason == "model_usage_unavailable"
    assert (
        "usage_error"
        in next(event for event in record.events if event.kind == "llm").metadata[
            "agency_usage"
        ]
    )


def test_hidden_evidence_never_enters_packet(harness):
    controller, _ = harness
    hidden = Event(
        0, "evaluator", "hidden-evaluation", "now", 1, "fail", stdout_tail="SECRET"
    )
    controller.record.events.append(hidden)
    controller.latest["hidden-evaluation"] = hidden
    assert "SECRET" not in controller.packet()
    assert not controller.snapshot()


def test_preflight_is_outside_experimental_clock(harness, monkeypatch):
    controller, _ = harness
    clock = [0.0]
    monkeypatch.setattr(module.time, "perf_counter", lambda: clock[0])

    def preflight():
        clock[0] += 1000
        return {"ok": True}

    monkeypatch.setattr(controller, "preflight", preflight)
    record = controller.run()
    assert record.wall_seconds == 0
    assert record.agency_sessions == 2


def test_preflight_failure_never_starts_experiment(harness, monkeypatch):
    controller, fake = harness
    monkeypatch.setattr(
        controller, "preflight", lambda: {"ok": False, "problems": ["missing mypy"]}
    )
    record = controller.run()
    assert record.status == "infrastructure_failure"
    assert record.wall_seconds == 0
    assert not fake.timeouts


def test_copilot_capabilities_do_not_receive_agency_only_flags(tmp_path):
    source = copilot_arguments(
        "copilot",
        "public task",
        tmp_path / "usage.json",
        "mai-code-2",
        "medium",
        "source",
    )
    start = source.index("--available-tools") + 1
    end = source.index("--excluded-tools")
    assert set(source[start:end]) - set(source[end + 1 :]) == set()
    tools = copilot_arguments(
        "copilot",
        "public task",
        tmp_path / "usage.json",
        "mai-code-2",
        "medium",
        "tools",
    )
    start, end = tools.index("--available-tools") + 1, tools.index("--allow-tool")
    assert set(tools[start:end]) == set(COPILOT_REPAIR_TOOLS)
    assert not {"task", "run_dynamic_workflow", "web_fetch"} & set(tools[start:end])
    assert "copilot" not in source[1:] and "--no-aec" not in source


def test_boolean_native_request_count_is_unknown_not_one(harness, tmp_path):
    controller, _ = harness
    usage = tmp_path / "malformed-usage.json"
    usage.write_text(
        json.dumps({"modelMetrics": {"model": {"requests": {"count": True}}}})
    )
    assert "usage_error" in controller._read_usage(usage)
    assert not controller.record.model_usage_complete


def test_native_metrics_not_nested_agent_metrics_are_counted(harness):
    controller, _ = harness
    metrics = {"requests": {"count": 9}, "usage": {"inputTokens": 187327}}
    controller._accumulate_usage(
        {
            "modelMetrics": {"sol": metrics},
            "agentMetrics": {"main": {"modelMetrics": {"sol": metrics}}},
        }
    )
    assert controller.record.model_requests == 9
    assert controller.record.models_used == {"sol": 9}
    assert controller.record.model_input_tokens == 187327


def test_faster_expected_repair_is_selected_before_optional_verifier(harness):
    controller, _ = harness
    controller.policy.data["agency-repair"] = {
        "mean_seconds": 1,
        "useful_rate": 1,
        "count": 1,
        "useful_count": 1,
    }
    record = controller.run()
    assert record.events[3].name == "agency-repair"
    assert record.declared_verified


def test_parallel_checks_reserve_aggregate_verifier_budget(harness):
    controller, fake = harness
    controller.budget.max_verifier_wall_seconds = 0.03
    controller.tier0()
    assert fake.timeouts[:3] == [0.01, 0.01, 0.01]


def test_partial_post_repair_verification_keeps_delta_and_reports_incomplete(harness):
    controller, _ = harness
    controller.budget.max_actions = 6
    record = controller.run()
    repair = next(event for event in record.events if event.kind == "llm")
    assert not repair.metadata["verification_complete"]
    assert repair.metadata["evidence_delta"]["remaining"]
    assert record.termination_reason == "actions"
    assert not record.declared_verified


def test_strong_failure_is_rechecked_after_every_repair(harness, monkeypatch):
    controller, fake = harness
    fake.ruff_failure = False
    original = fake.result

    def run(command, cwd, timeout):
        argv = module.split_command(command) if isinstance(command, str) else command
        if argv == ["visible"]:
            return CommandResult(argv, 0, "pass", 1, "", "")
        if argv == ["strong"] and fake.repairs < 2:
            return CommandResult(
                argv, 1, "fail", 1, f"counterexample {fake.repairs}", ""
            )
        return original(command, cwd, timeout)

    monkeypatch.setattr(module, "run_command", run)
    record = controller.run()
    assert record.declared_verified
    assert record.agency_sessions == 2
    strong = [event for event in record.events if event.name == "smt-z3"]
    assert [event.outcome for event in strong] == ["fail", "fail", "pass"]


def test_wall_budget_terminates_during_a_later_repair(harness, monkeypatch):
    controller, fake = harness
    clock = [0.0]
    controller.budget.max_wall_seconds = 1
    monkeypatch.setattr(module.time, "perf_counter", lambda: clock[0])
    original = fake.result

    def run(command, cwd, timeout):
        argv = module.split_command(command) if isinstance(command, str) else command
        result = original(command, cwd, timeout)
        cost = 0.7 if argv[0] == "agency" else 0.01
        clock[0] += min(cost, timeout)
        if cost > timeout:
            result.outcome = "timeout"
            result.exit_code = None
        return result

    monkeypatch.setattr(module, "run_command", run)
    record = controller.run()
    assert record.termination_reason == "wall_time"
    assert record.wall_seconds == 1
    assert record.hidden_correct is None
    assert record.agency_sessions == 2
    assert any(
        event.kind == "llm" and event.outcome == "timeout" for event in record.events
    )
