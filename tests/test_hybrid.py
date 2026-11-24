import json
from pathlib import Path

import pytest

from verify_and_repair import controller as base_module
from verify_and_repair import patch_controller as patch_module
from verify_and_repair.execution import CommandResult, run_command
from verify_and_repair.hybrid_controller import HybridController
from verify_and_repair.models import Budget

API_BEFORE = "def inc(x: int) -> int:\n    return x - 1\n"
API_AFTER = API_BEFORE.replace("x - 1", "x + 1")
HELPER_BEFORE = "def double(x: int) -> int:\n    return x\n"
HELPER_AFTER = HELPER_BEFORE.replace("return x", "return 2 * x")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "__init__.py").write_text("")
    (root / "src" / "api.py").write_text(API_BEFORE)
    (root / "src" / "helper.py").write_text(HELPER_BEFORE)
    (root / "tests" / "test_existing.py").write_text(
        "from src.api import inc\nfrom src.helper import double\n\n"
        "def test_inc():\n    assert inc(0) == 1\n\n"
        "def test_double():\n    assert double(2) == 4\n"
    )
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    assert run_command(["git", "init"], root, 10).outcome == "pass"
    return root


def sources(**files):
    return json.dumps(
        {
            "files": [
                {"path": name, "content": content} for name, content in files.items()
            ]
        }
    )


def make_controller(repo, tmp_path, monkeypatch, responses, tool_edits=(), **options):
    calls = []
    backend = options.pop("backend", "agency")
    actual = base_module.run_command

    def command(argv, cwd, timeout, **kwargs):
        if isinstance(argv, list) and argv[0] in {"agency", "copilot"}:
            if argv[1:] == ["--version"]:
                return CommandResult(argv, 0, "pass", 1, "agency", "")
            if backend == "copilot":
                assert "copilot" not in argv[1:]
                assert "--no-aec" not in argv
                assert kwargs["env"]["COPILOT_ALLOW_ALL"] is None
            source = "task_complete" in argv
            packet = json.loads(argv[argv.index("--prompt") + 1])
            assert "HIDDEN_SECRET" not in json.dumps(packet)
            calls.append((source, packet))
            count = 1 if source else 4
            usage = {
                "modelMetrics": {
                    "mai-code-2": {"requests": {"count": count}, "usage": {}}
                }
            }
            Path(argv[argv.index("--usage-output-file") + 1]).write_text(
                json.dumps(usage)
            )
            if source:
                assert Path(cwd) != repo
                content = responses.pop(0)
                output = "\n".join(
                    json.dumps(event)
                    for event in [
                        {
                            "type": "session.usage_record",
                            "data": {"usage": {"availableToolCount": 0}},
                        },
                        {"type": "assistant.message", "data": {"content": content}},
                    ]
                )
            else:
                assert Path(cwd) == repo
                for name, content in tool_edits:
                    (repo / name).write_text(content)
                output = '{"type":"assistant.message","data":{"content":"Repaired."}}'
            return CommandResult(argv, 0, "pass", 1, output, "")
        return actual(argv, cwd, timeout)

    monkeypatch.setattr(base_module, "run_command", command)
    controller = HybridController(
        task_id="repository-task",
        repo=repo,
        task_text="Fix inc and double",
        condition="adaptive",
        llm_mode=backend,
        working_tests="python -m pytest -q tests",
        hidden_tests="python -c \"print('HIDDEN_SECRET')\"",
        defer_hidden_evaluation=True,
        output=tmp_path / "results" / "run.json",
        budget=options.pop("budget", Budget()),
        agency_model="mai-code-2",
        implementation_files=("src/api.py", "src/helper.py"),
        context_files=("tests/test_existing.py",),
        **options,
    )
    monkeypatch.setattr(controller, "resolve_agent", lambda: backend)
    return controller, calls


@pytest.mark.parametrize("backend", ["agency", "copilot"])
def test_multifile_source_generation_without_contract_or_tools(
    repo, tmp_path, monkeypatch, backend
):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [sources(**{"src/api.py": API_AFTER, "src/helper.py": HELPER_AFTER})],
        backend=backend,
    )
    record = controller.run()
    assert record.declared_verified
    assert len(calls) == record.agent_sessions == record.model_requests == 1
    assert record.agency_sessions == (backend == "agency")
    assert record.copilot_sessions == (backend == "copilot")
    assert f"{backend}-repair" in controller.policy.data
    assert record.config["verification_claim"] == "visible_checks_only"
    assert record.config["symbolic_status"] == "not_applicable"
    assert record.hidden_correct is None
    assert all(event.name != "contract-crosshair" for event in record.events)


@pytest.mark.parametrize("backend", ["agency", "copilot"])
def test_two_repairs_send_resolved_and_remaining_public_failures(
    repo, tmp_path, monkeypatch, backend
):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [
            sources(**{"src/api.py": API_AFTER}),
            sources(**{"src/helper.py": HELPER_AFTER}),
        ],
        backend=backend,
    )
    record = controller.run()
    assert record.declared_verified and record.agent_sessions == 2
    usage_files = list((tmp_path / "results" / backend).glob("*-usage.json"))
    assert len(usage_files) == 2
    delta = calls[1][1]["evidence_delta"]
    assert len(delta["resolved"]) == len(delta["remaining"]) == 1
    assert not delta["new"]
    initial = calls[0][1]["evidence_delta"]["new"]
    assert any("assert inc(0) == 1" in failure["detail"] for failure in initial)
    assert any("assert double(2) == 4" in failure["detail"] for failure in initial)
    assert "assert inc(0) == 1" in delta["resolved"][0]["detail"]
    assert "assert double(2) == 4" in delta["remaining"][0]["detail"]
    assert all(source for source, _ in calls)
    assert all(
        event.metadata["verification_complete"]
        for event in record.events
        if event.kind == "llm"
    )


@pytest.mark.parametrize("backend", ["agency", "copilot"])
def test_unchanged_source_evidence_escalates_once_then_suppresses_tools(
    repo, tmp_path, monkeypatch, backend
):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [sources(**{"src/api.py": API_BEFORE + "# unchanged behavior\n"})],
        tool_edits=[("src/api.py", API_BEFORE + "# tool also failed\n")],
        backend=backend,
    )
    record = controller.run()
    assert [source for source, _ in calls] == [True, False]
    assert record.agent_sessions == 2 and record.model_requests == 5
    assert not record.declared_verified
    assert record.termination_reason == "no_useful_action"
    assert calls[1][1]["route_reason"] == "unchanged_visible_evidence"


@pytest.mark.parametrize("backend", ["agency", "copilot"])
def test_backend_neutral_session_budget_stops_before_second_repair(
    repo, tmp_path, monkeypatch, backend
):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [sources(**{"src/api.py": API_AFTER})],
        backend=backend,
        budget=Budget(max_agent_sessions=1, max_agency_sessions=3),
    )
    record = controller.run()
    assert len(calls) == record.agent_sessions == 1
    assert record.status == "budget_exhausted"
    assert record.termination_reason == (
        "agency_sessions" if backend == "agency" else "agent_sessions"
    )


def test_invalid_source_is_not_success_when_existing_checks_pass(
    repo, tmp_path, monkeypatch
):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        ["not JSON"],
        tool_edits=[("src/api.py", API_AFTER), ("src/helper.py", HELPER_AFTER)],
    )
    controller.checks = {"visible-tests": 'python -c "raise SystemExit(0)"'}
    record = controller.run()
    assert [source for source, _ in calls] == [True, False]
    assert record.declared_verified
    assert calls[1][1]["last_source_error"]


def test_large_context_routes_to_repository_tools_without_lean_call(
    repo, tmp_path, monkeypatch
):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [],
        tool_edits=[("src/api.py", API_AFTER), ("src/helper.py", HELPER_AFTER)],
        lean_context_bytes=1,
    )
    record = controller.run()
    assert record.declared_verified and [source for source, _ in calls] == [False]
    assert calls[0][1]["route_reason"] == "context_requires_exploration"


def test_tool_edit_of_protected_test_is_reported_without_rollback(
    repo, tmp_path, monkeypatch
):
    controller, _ = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [],
        tool_edits=[("tests/test_existing.py", "user_or_agent_edit = True\n")],
        lean_context_bytes=1,
    )
    record = controller.run()
    assert not record.declared_verified
    assert record.termination_reason == "protected_files_changed"
    assert record.events[-1].metadata["protection_error"]
    assert (
        repo / "tests" / "test_existing.py"
    ).read_text() == "user_or_agent_edit = True\n"


@pytest.mark.parametrize(
    "budget,reason",
    [
        (Budget(max_agency_sessions=0), "agency_sessions"),
        (Budget(max_model_requests=0), "model_requests"),
        (Budget(max_verifier_wall_seconds=0), "verifier_time"),
    ],
)
def test_hard_budgets_block_hybrid_inference(
    repo, tmp_path, monkeypatch, budget, reason
):
    controller, calls = make_controller(repo, tmp_path, monkeypatch, [], budget=budget)
    record = controller.run()
    assert not calls and record.termination_reason == reason


@pytest.mark.parametrize("status", ["proved", "unknown", "disproved", "error"])
def test_optional_symbolics_never_turn_unknown_into_proof(
    repo, tmp_path, monkeypatch, status
):
    (repo / "contracts.py").write_text(
        "from src.api import inc\n\ndef check_inc(x: int) -> int:\n"
        '    """post: __return__ == x + 1"""\n    return inc(x)\n'
    )
    (repo / "contracts.json").write_text(
        json.dumps(
            {
                "implementation_files": ["src/api.py", "src/helper.py"],
                "contract_files": ["contracts.py"],
                "targets": ["contracts.check_inc"],
                "claim": "Integer increment only; double is outside this claim",
            }
        )
    )
    monkeypatch.setattr(
        patch_module,
        "run_command",
        lambda *args: CommandResult(
            [],
            {"proved": 0, "unknown": 2, "disproved": 1, "error": 3}[status],
            "pass" if status == "proved" else "fail",
            1,
            json.dumps({"status": status, "checkable_count": 1}),
            "",
        ),
    )
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [sources(**{"src/api.py": API_AFTER, "src/helper.py": HELPER_AFTER})],
        contract_manifest=Path("contracts.json"),
        budget=Budget(max_agency_sessions=1),
    )
    record = controller.run()
    assert len(calls) == 1
    assert record.declared_verified == (status in {"proved", "unknown"})
    if status == "proved":
        assert (
            record.config["verification_claim"] == "visible_checks_and_scoped_contract"
        )
    elif status == "unknown":
        assert record.config["verification_claim"] == "visible_checks_only"
        assert record.config["symbolic_status"] == "unknown"
    elif status == "error":
        assert record.termination_reason == "symbolic_error"
