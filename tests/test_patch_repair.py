import difflib
import json
import sys
from pathlib import Path

import pytest

from verify_and_repair import controller as base_module
from verify_and_repair import patch_controller as patch_module
from verify_and_repair import patching as patching_module
from verify_and_repair.contracts import ContractPlan
from verify_and_repair.execution import CommandResult, run_command
from verify_and_repair.models import Budget
from verify_and_repair.patch_controller import PatchController
from verify_and_repair.patching import (
    apply_patch,
    apply_sources,
    response_patch,
    workspace_state,
)

BEFORE = "def inc(x: int) -> int:\n    return x - 1\n"
AFTER = "def inc(x: int) -> int:\n    return x + 1\n"
CONTRACT = '''from src.logic import inc


def check_inc(x: int) -> int:
    """
    post: __return__ == x + 1
    """
    return inc(x)
'''


def diff(before, after, name="src/logic.py"):
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    )


def source_payload(content, name="src/logic.py"):
    return json.dumps(
        {"files": [{"path": name, "content": content}]}, ensure_ascii=False
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "__init__.py").write_text("")
    (root / "src" / "logic.py").write_text(BEFORE)
    (root / "contracts.py").write_text(CONTRACT)
    (root / ".gitignore").write_text("__pycache__/\n")
    (root / "contracts.json").write_text(
        json.dumps(
            {
                "implementation_files": ["src/logic.py"],
                "contract_files": ["contracts.py"],
                "targets": ["contracts.check_inc"],
                "claim": "All Python integers x",
            }
        )
    )
    assert run_command(["git", "init"], root, 10).outcome == "pass"
    return root


def symbolic(repo, seconds=2):
    result = run_command(
        [
            sys.executable,
            "-m",
            "verify_and_repair.contracts",
            "--repo",
            str(repo),
            "--manifest",
            str(repo / "contracts.json"),
            "--seconds",
            str(seconds),
        ],
        repo,
        15,
    )
    assert result.stdout, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_actual_implementation_changes_disproof_to_proof(repo):
    assert symbolic(repo)["status"] == "disproved"
    (repo / "src" / "logic.py").write_text(AFTER)
    result = symbolic(repo)
    assert result["status"] == "proved"
    assert result["checkable_count"] == 1


def test_no_contract_and_unsatisfiable_preconditions_are_not_proofs(repo):
    (repo / "contracts.py").write_text("def check_inc(x: int) -> int:\n    return x\n")
    with pytest.raises(ValueError, match="postcondition"):
        ContractPlan.load(repo, Path("contracts.json"))
    (repo / "contracts.py").write_text(
        CONTRACT.replace("post:", "pre: x > 0\n    pre: x < 0\n    post:")
    )
    assert symbolic(repo)["status"] == "unknown"


def test_cache_key_includes_dependency_contract_and_solver_budget(repo):
    plan = ContractPlan.load(repo, Path("contracts.json"))
    initial = plan.fingerprint(workspace_state(repo), 5)
    (repo / "helper.py").write_text("VALUE = 1\n")
    assert plan.fingerprint(workspace_state(repo), 5) != initial
    changed = plan.fingerprint(workspace_state(repo), 5)
    (repo / "contracts.py").write_text(CONTRACT + "\n")
    assert plan.fingerprint(workspace_state(repo), 5) != changed
    assert plan.fingerprint(workspace_state(repo), 2) != plan.fingerprint(
        workspace_state(repo), 5
    )


@pytest.mark.parametrize("name", ["contracts.py", "../escape.py", "tests/test_x.py"])
def test_patch_cannot_modify_nonimplementation_paths(repo, tmp_path, name):
    original = (repo / "src" / "logic.py").read_bytes()
    with pytest.raises(ValueError):
        apply_patch(
            repo,
            diff(BEFORE, AFTER, name),
            ("src/logic.py",),
            workspace_state(repo),
            tmp_path / "validation",
            5,
        )
    assert (repo / "src" / "logic.py").read_bytes() == original


def test_patch_is_syntax_checked_before_any_real_file_changes(repo, tmp_path):
    with pytest.raises(SyntaxError):
        apply_patch(
            repo,
            diff(BEFORE, "def inc(:\n"),
            ("src/logic.py",),
            workspace_state(repo),
            tmp_path / "validation",
            5,
        )
    assert (repo / "src" / "logic.py").read_text() == BEFORE
    apply_patch(
        repo,
        diff(BEFORE, AFTER),
        ("src/logic.py",),
        workspace_state(repo),
        tmp_path / "validation",
        5,
    )
    assert (repo / "src" / "logic.py").read_text() == AFTER


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_patch_supports_native_checkout_newlines(repo, tmp_path, newline):
    source = repo / "src" / "logic.py"
    source.write_bytes(BEFORE.replace("\n", newline).encode())
    apply_patch(
        repo,
        diff(BEFORE, AFTER),
        ("src/logic.py",),
        workspace_state(repo),
        tmp_path / "validation",
        5,
    )
    assert source.read_text() == AFTER
    assert source.read_bytes() == AFTER.replace("\n", newline).encode()


def test_concurrent_edit_rejects_patch_without_overwriting_it(repo, tmp_path):
    state = workspace_state(repo)
    (repo / "src" / "logic.py").write_text("user_edit = True\n")
    with pytest.raises(ValueError, match="changed"):
        apply_patch(
            repo,
            diff(BEFORE, AFTER),
            ("src/logic.py",),
            state,
            tmp_path / "validation",
            5,
        )
    assert (repo / "src" / "logic.py").read_text() == "user_edit = True\n"


def test_hunk_counts_are_recomputed_without_another_model_request(repo, tmp_path):
    patch = diff(BEFORE, AFTER).replace("@@ -1,2 +1,2 @@", "@@ -1,99 +1,99 @@")
    apply_patch(
        repo,
        patch,
        ("src/logic.py",),
        workspace_state(repo),
        tmp_path / "validation",
        5,
    )
    assert (repo / "src" / "logic.py").read_text() == AFTER


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_source_replacement_preserves_newlines_and_generates_valid_audit_diff(
    repo, tmp_path, newline, final_newline
):
    source = repo / "src" / "logic.py"
    before = BEFORE if final_newline else BEFORE.rstrip("\n")
    after = AFTER + '"""\n@@\n--- not a file header\ncaf\u00e9\n"""\n'
    if not final_newline:
        after = after.rstrip("\n")
    source.write_bytes(before.replace("\n", newline).encode())
    patch, metadata = apply_sources(
        repo,
        source_payload(after),
        ("src/logic.py",),
        workspace_state(repo),
        tmp_path / "validation",
        5,
    )
    assert metadata["patch_applied"]
    assert source.read_bytes() == after.replace("\n", newline).encode()
    audit = tmp_path / "audit.patch"
    audit.write_bytes(patch.encode())
    result = run_command(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "apply",
            "--reverse",
            "--check",
            str(audit),
        ],
        repo,
        5,
    )
    assert result.outcome == "pass", result.stderr


@pytest.mark.parametrize(
    "response",
    [
        diff(BEFORE, AFTER).replace("@@ -1,2 +1,2 @@", "@@"),
        source_payload(AFTER, "../escape.py"),
        source_payload(AFTER, "contracts.py"),
        json.dumps({"files": [{"path": "src/logic.py", "content": 42}]}),
        json.dumps({"files": [{"path": "src/logic.py", "content": AFTER}] * 2}),
        '{"files": [], "files": [{"path":"src/logic.py","content":"pass"}]}',
        '{"files": [{"path":"src/logic.py","content":"pass","content":"x=1"}]}',
        json.dumps({"files": [], "extra": "not permitted"}),
    ],
)
def test_invalid_source_envelopes_never_modify_workspace(repo, tmp_path, response):
    state = workspace_state(repo)
    with pytest.raises((ValueError, TypeError)):
        apply_sources(
            repo, response, ("src/logic.py",), state, tmp_path / "validation", 5
        )
    assert workspace_state(repo) == state


def test_all_replacement_files_compile_before_any_write(repo, tmp_path):
    helper = repo / "src" / "other.py"
    helper.write_text("VALUE = 1\n")
    response = json.dumps(
        {
            "files": [
                {"path": "src/logic.py", "content": AFTER},
                {"path": "src/other.py", "content": "def broken(:\n"},
            ]
        }
    )
    state = workspace_state(repo)
    with pytest.raises(SyntaxError):
        apply_sources(
            repo,
            response,
            ("src/logic.py", "src/other.py"),
            state,
            tmp_path / "validation",
            5,
        )
    assert workspace_state(repo) == state


def test_source_replacement_rechecks_concurrent_changes_before_commit(
    repo, tmp_path, monkeypatch
):
    state = workspace_state(repo)
    actual_state = workspace_state
    checks = 0

    def concurrent_state(root):
        nonlocal checks
        checks += 1
        if checks == 2:
            (root / "src" / "logic.py").write_text("# concurrent edit\n" + BEFORE)
        return actual_state(root)

    monkeypatch.setattr(patching_module, "workspace_state", concurrent_state)
    with pytest.raises(ValueError, match="changed during"):
        apply_sources(
            repo,
            source_payload(AFTER),
            ("src/logic.py",),
            state,
            tmp_path / "validation",
            5,
        )
    assert (repo / "src" / "logic.py").read_text() == "# concurrent edit\n" + BEFORE


@pytest.mark.parametrize(
    "response,timeout,error",
    [
        (source_payload(BEFORE), 5, "no implementation change"),
        (source_payload(AFTER), 0, "time budget exhausted"),
        (source_payload("#" + "\u00e9" * 50_000), 5, "100 KB"),
    ],
    ids=["no-op", "budget", "utf8-byte-limit"],
)
def test_source_replacement_limits_preserve_original(
    repo, tmp_path, response, timeout, error
):
    state = workspace_state(repo)
    with pytest.raises(ValueError, match=error):
        apply_sources(
            repo, response, ("src/logic.py",), state, tmp_path / "validation", timeout
        )
    assert workspace_state(repo) == state


def test_json_fence_is_only_unwrapped_not_fuzzily_repaired(repo, tmp_path):
    response = "```json\n" + source_payload(AFTER) + "\n```"
    apply_sources(
        repo,
        response,
        ("src/logic.py",),
        workspace_state(repo),
        tmp_path / "validation",
        5,
    )
    assert (repo / "src" / "logic.py").read_text() == AFTER


def native_response(patch, count=0):
    return "\n".join(
        json.dumps(item)
        for item in [
            {
                "type": "session.usage_record",
                "data": {"usage": {"availableToolCount": count}},
            },
            {"type": "assistant.message", "data": {"content": patch}},
        ]
    )


def test_patch_protocol_rejects_tool_exposure_and_tool_calls():
    patch = diff(BEFORE, AFTER)
    assert response_patch(native_response(patch))[0] == patch
    with pytest.raises(ValueError, match="zero exposed"):
        response_patch(native_response(patch, 3))
    with pytest.raises(ValueError, match="tool use"):
        response_patch(json.dumps({"type": "tool.execution_start"}) + "\n")


def test_native_durable_usage_must_cover_all_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session_id = "00000000-0000-0000-0000-000000000001"
    native = tmp_path / ".copilot" / "session-state" / session_id / "events.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text(native_response(diff(BEFORE, AFTER)))
    output = "\n".join(
        json.dumps(event)
        for event in [
            {"type": "session.start", "data": {"sessionId": session_id}},
            {"type": "assistant.message", "data": {"content": diff(BEFORE, AFTER)}},
        ]
    )
    _, metadata = response_patch(output, expected_requests=1)
    assert metadata["native_tool_evidence_file"] == str(native)
    with pytest.raises(ValueError, match="every model request"):
        response_patch(output, expected_requests=2)


@pytest.mark.parametrize("attempted_tool", [False, True])
def test_native_durable_evidence_honors_copilot_home_and_rejects_tools(
    tmp_path, monkeypatch, attempted_tool
):
    home = tmp_path / "isolated-copilot"
    monkeypatch.setenv("COPILOT_HOME", str(home))
    session_id = "00000000-0000-0000-0000-000000000002"
    native = home / "session-state" / session_id / "events.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text(
        native_response(diff(BEFORE, AFTER))
        + ('\n{"type":"tool.execution_start","data":{}}' if attempted_tool else "")
    )
    output = "\n".join(
        json.dumps(event)
        for event in [
            {"type": "session.start", "data": {"sessionId": session_id}},
            {"type": "assistant.message", "data": {"content": diff(BEFORE, AFTER)}},
        ]
    )
    if attempted_tool:
        with pytest.raises(ValueError, match="durable session attempted tool"):
            response_patch(output, expected_requests=1)
    else:
        _, metadata = response_patch(output, expected_requests=1)
        assert metadata["native_tool_evidence_file"] == str(native)


def make_controller(repo, tmp_path, monkeypatch, patches, gate="symbolic"):
    calls = []
    actual_command = base_module.run_command

    def command(argv, cwd, timeout):
        if isinstance(argv, list) and argv[0] == "agency":
            if argv[1:] == ["--version"]:
                return CommandResult(argv, 0, "pass", 1, "agency", "")
            assert "--agent" not in argv
            index = argv.index("--available-tools")
            assert argv[index + 1 : index + 4] == [
                "task_complete",
                "--excluded-tools",
                "task_complete",
            ]
            assert argv[argv.index("--max-autopilot-continues") + 1] == "0"
            assert Path(cwd) != repo
            assert not (Path(cwd) / "src").exists()
            packet = json.loads(argv[argv.index("--prompt") + 1])
            assert "trusted_contracts" in packet
            calls.append(packet)
            usage = {
                "modelMetrics": {"mai-code-2": {"requests": {"count": 1}, "usage": {}}}
            }
            Path(argv[argv.index("--usage-output-file") + 1]).write_text(
                json.dumps(usage)
            )
            return CommandResult(
                argv, 0, "pass", 1, native_response(patches[len(calls) - 1]), ""
            )
        return actual_command(argv, cwd, timeout)

    monkeypatch.setattr(base_module, "run_command", command)
    controller = PatchController(
        task_id="inc",
        repo=repo,
        task_text="Repair inc",
        condition="adaptive",
        working_tests='python -c "raise SystemExit(0)"',
        hidden_tests='python -c "raise SystemExit(0)"',
        llm_mode="agency",
        output=tmp_path / "results" / "run.json",
        budget=Budget(),
        agency_model="mai-code-2",
        contract_manifest=Path("contracts.json"),
        symbolic_seconds=8,
        verification_gate=gate,
    )
    monkeypatch.setattr(controller, "resolve_agency", lambda: "agency")
    return controller, calls


def test_public_test_ablation_repairs_without_invoking_symbolic_backend(
    repo, tmp_path, monkeypatch
):
    controller, calls = make_controller(
        repo, tmp_path, monkeypatch, [source_payload(AFTER)], gate="public-tests"
    )
    controller.working_tests = (
        'python -c "from src.logic import inc; assert inc(0) == 1"'
    )
    monkeypatch.setattr(
        patch_module,
        "run_command",
        lambda *args: pytest.fail("Symbolic backend invoked"),
    )
    record = controller.run()
    assert record.status == "solved"
    assert record.agency_sessions == len(calls) == 1
    assert all(event.name != "contract-crosshair" for event in record.events)
    assert [
        event.outcome for event in record.events if event.name == "visible-tests"
    ] == ["fail", "pass"]
    assert controller.symbolic_status != "proved"


def test_patch_only_counterexample_progression_and_native_requests(
    repo, tmp_path, monkeypatch
):
    middle = BEFORE.replace("x - 1", "x")
    controller, calls = make_controller(
        repo, tmp_path, monkeypatch, [source_payload(middle), source_payload(AFTER)]
    )
    result = controller.run()
    assert result.status == "solved", [
        (e.name, e.outcome, e.stderr_tail) for e in result.events
    ]
    assert result.agency_sessions == result.model_requests == len(calls) == 2
    assert all(
        e.metadata.get("patch_applied") for e in result.events if e.kind == "llm"
    )
    assert all(e.name not in {"ruff", "mypy", "targeted-tests"} for e in result.events)
    assert (repo / "contracts.py").read_text() == CONTRACT


def test_same_counterexample_suppresses_another_patch_request(
    repo, tmp_path, monkeypatch
):
    comment_only = BEFORE + "\n# not repaired\n"
    controller, calls = make_controller(
        repo, tmp_path, monkeypatch, [source_payload(comment_only)]
    )
    result = controller.run()
    assert len(calls) == 1
    assert not result.declared_verified
    assert result.termination_reason == "no_useful_action"


def test_invalid_model_source_is_counted_but_never_applied(repo, tmp_path, monkeypatch):
    controller, calls = make_controller(
        repo,
        tmp_path,
        monkeypatch,
        [diff(BEFORE, AFTER).replace("@@ -1,2 +1,2 @@", "@@")],
    )
    result = controller.run()
    assert result.agency_sessions == result.model_requests == len(calls) == 1
    assert result.model_usage_complete and not result.declared_verified
    assert result.termination_reason == "patch_rejected"
    assert (repo / "src" / "logic.py").read_text() == BEFORE
    event = next(e for e in result.events if e.kind == "llm")
    assert event.metadata["available_tool_count"] == 0
    assert not event.metadata["patch_applied"]
    assert event.metadata["patch_error"]


@pytest.mark.parametrize("outcome", ["unknown", "timeout"])
def test_unknown_does_not_trigger_speculative_model_repair(
    repo, tmp_path, monkeypatch, outcome
):
    controller, calls = make_controller(repo, tmp_path, monkeypatch, [])
    monkeypatch.setattr(
        patch_module,
        "run_command",
        lambda *args: CommandResult(
            [],
            2,
            outcome,
            1,
            json.dumps(
                {
                    "status": "proved" if outcome == "timeout" else "unknown",
                    "checkable_count": 1,
                }
            ),
            "",
        ),
    )
    result = controller.run()
    assert not calls
    assert not result.declared_verified
    assert result.termination_reason == "symbolic_unknown"


def test_unchanged_symbolic_obligation_uses_definite_result_cache(
    repo, tmp_path, monkeypatch
):
    controller, _ = make_controller(repo, tmp_path, monkeypatch, [])
    assert controller.preflight()["ok"]
    controller.verify()
    controller.verify()
    assert controller.record.events[0].metadata["cache_hit"] is False
    assert controller.record.events[1].metadata["cache_hit"] is True
    assert controller.record.events[1].duration_ms == 0
