import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from verify_and_repair import harness
from verify_and_repair.execution import CommandResult


@pytest.fixture
def config(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "logic.js").write_text("export const value = 1;\n")
    (repo / "public-check.py").write_text("# frozen public check\n")
    return harness.HarnessConfig(
        repo=repo,
        implementation_files=("logic.js",),
        protected_files=("public-check.py",),
        working_tests="python public-check.py",
    )


def fake_checks(monkeypatch, outputs, duration_ms=1):
    calls = []

    def run(argv, cwd, timeout, **kwargs):
        calls.append((argv, timeout))
        assert kwargs["use_current_python"] is False
        outcome, text = outputs.pop(0)
        return CommandResult(
            argv, 0 if outcome == "pass" else 1, outcome, duration_ms, text, ""
        )

    monkeypatch.setattr(harness, "run_command", run)
    return calls


def test_same_agent_feedback_progresses_and_survives_server_restart(
    config, monkeypatch
):
    calls = fake_checks(
        monkeypatch,
        [
            (
                "fail",
                "FAILED test.js::first - assert 1 == 2\nFAILED test.js::second - assert 3 == 4",
            ),
            ("fail", "FAILED test.js::second - assert 3 == 4"),
            ("pass", "passed"),
        ],
    )
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Implement the requested behavior")["session_id"]
    first = sessions.verify(session_id)
    assert first["next_action"] == "repair"
    assert len(first["counterexamples"]) == 2
    assert first["child_agent_sessions"] == 0 and first["host_model_requests"] is None
    restarted = harness.HarnessSessions(config)
    second = restarted.verify(session_id)
    assert len(second["evidence_delta"]["resolved"]) == 1
    assert len(second["evidence_delta"]["remaining"]) == 1
    assert not second["evidence_delta"]["new"]
    final = restarted.verify(session_id)
    assert final["verified"] and final["next_action"] == "done"
    assert restarted.verify(session_id) == final
    assert len(calls) == 3


def test_unchanged_counterexamples_stop_without_resetting_session(config, monkeypatch):
    calls = fake_checks(
        monkeypatch, [("fail", "assert 1 == 2"), ("fail", "assert 1 == 2")]
    )
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    sessions.verify(session_id)
    stopped = sessions.verify(session_id)
    assert stopped["reason"] == "unchanged_counterexamples"
    assert stopped["next_action"] == "stop"
    assert harness.HarnessSessions(config).verify(session_id) == stopped
    assert len(calls) == 2


@pytest.mark.parametrize("limit", ["rounds", "wall", "verifier"])
def test_budgets_stop_before_requesting_another_host_repair(config, monkeypatch, limit):
    if limit == "rounds":
        config = replace(config, max_repairs=0)
    elif limit == "verifier":
        config = replace(config, max_verifier_wall_seconds=0.001)
    calls = fake_checks(monkeypatch, [("fail", "assert 1 == 2")])
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    if limit == "wall":
        path = sessions._path(session_id)
        state = json.loads(path.read_text())
        state["started_at"] -= 601
        sessions._save(path, state)
    stopped = sessions.verify(session_id)
    assert stopped["status"] == "budget_exhausted"
    assert stopped["next_action"] == "stop"
    assert stopped["repair_rounds_requested"] == 0
    assert len(calls) == (0 if limit == "wall" else 1)


@pytest.mark.parametrize("outcome", ["error", "timeout"])
def test_check_infrastructure_errors_never_request_code_repair(
    config, monkeypatch, outcome
):
    fake_checks(monkeypatch, [(outcome, "missing tool or timed out")])
    sessions = harness.HarnessSessions(config)
    result = sessions.verify(sessions.begin("Repair")["session_id"])
    assert result["status"] == "infrastructure_error"
    assert result["next_action"] == "stop"


def test_protected_tests_cannot_be_changed_to_make_checks_pass(config, monkeypatch):
    calls = fake_checks(monkeypatch, [])
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    (config.repo / "public-check.py").write_text("raise SystemExit(0)")
    assert sessions.verify(session_id)["next_action"] == "stop"
    assert not calls


def test_configuration_change_and_interrupted_checks_cannot_reset_budget(
    config, monkeypatch
):
    calls = fake_checks(monkeypatch, [])
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    changed = harness.HarnessSessions(replace(config, max_repairs=20))
    with pytest.raises(ValueError, match="another configuration"):
        changed.verify(session_id)
    state = json.loads(sessions._path(session_id).read_text())
    state["status"] = "checking"
    sessions._save(sessions._path(session_id), state)
    assert sessions.verify(session_id)["reason"] == "interrupted_verification"
    assert not calls


@pytest.mark.parametrize(
    "changes",
    [
        {"max_repairs": True},
        {"max_wall_seconds": float("inf")},
        {"working_tests": '"unclosed'},
        {"implementation_files": ("../outside.py",)},
        {"implementation_files": ("test_logic.py",)},
    ],
)
def test_invalid_scope_and_limits_are_rejected(config, changes):
    with pytest.raises(ValueError):
        harness.HarnessSessions(replace(config, **changes))


def test_artifacts_and_session_ids_stay_outside_the_project(config):
    with pytest.raises(ValueError, match="outside"):
        harness.HarnessSessions(config, config.repo / "results")
    with pytest.raises(ValueError):
        harness.HarnessSessions(config).verify("..\\other-session")


def test_active_or_interrupted_lock_stops_without_stealing_it(config, monkeypatch):
    calls = fake_checks(monkeypatch, [])
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    lock = sessions._path(session_id).with_suffix(".lock")
    lock.write_text("owned by an active or interrupted check")
    assert sessions.verify(session_id)["next_action"] == "stop"
    assert lock.read_text() == "owned by an active or interrupted check"
    assert not calls


@pytest.mark.parametrize(
    "problem", ["stale", "ambiguous", "scope", "protected", "syntax", "expired"]
)
def test_focused_edit_rejects_unsafe_changes_without_modifying_source(
    config, monkeypatch, problem
):
    source = config.repo / "logic.py"
    source.write_text("def inc(x):\n    return x - 1\n\n# return x - 1\n")
    config = replace(config, implementation_files=("logic.py",))
    fake_checks(monkeypatch, [("fail", "assert inc(0) == 1")])
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    sessions.verify(session_id)
    before = source.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    file = "logic.py"
    old, new = "    return x - 1\n", "    return x + 1\n"
    if problem == "stale":
        digest = "0" * 64
    elif problem == "ambiguous":
        old, new = "return x - 1", "return x + 1"
    elif problem == "scope":
        file = "public-check.py"
    elif problem == "protected":
        (config.repo / "public-check.py").write_text("changed")
    elif problem == "syntax":
        new = "    return (\n"
    elif problem == "expired":
        state = sessions._load(sessions._path(session_id))
        state["started_at"] -= 601
        sessions._save(sessions._path(session_id), state)
    with pytest.raises((ValueError, SyntaxError)):
        sessions.apply_edit(session_id, file, digest, old, new)
    assert source.read_bytes() == before
    assert not list(config.repo.glob(".vr-edit-*"))


def test_focused_edit_requires_actual_failed_check_not_just_a_new_session(config):
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    with pytest.raises(ValueError, match="pending repair"):
        sessions.apply_edit(session_id, "logic.js", "0" * 64, "value = 1", "value = 2")


def test_context_files_are_protected_and_old_session_defaults_remain_compatible(
    config, monkeypatch
):
    fake_checks(monkeypatch, [("fail", "assert value == 2")])
    sessions = harness.HarnessSessions(config)
    session_id = sessions.begin("Repair")["session_id"]
    path = sessions._path(session_id)
    state = sessions._load(path)
    del state["config"]["context_files"], state["config"]["max_context_bytes"]
    sessions._save(path, state)
    assert sessions.verify(session_id)["next_action"] == "repair"
    with pytest.raises(ValueError, match="Protected"):
        harness.HarnessSessions(replace(config, context_files=("logic.js",)))


def test_read_only_context_is_protected_even_when_not_in_protected_files(
    config, monkeypatch
):
    calls = fake_checks(monkeypatch, [])
    (config.repo / "types.py").write_text("Money = int\n")
    sessions = harness.HarnessSessions(replace(config, context_files=("types.py",)))
    session_id = sessions.begin("Repair")["session_id"]
    (config.repo / "types.py").write_text("Money = str\n")
    assert sessions.verify(session_id)["next_action"] == "stop"
    assert not calls


def test_real_stdio_mcp_roundtrip_repairs_with_the_hosts_edit(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo = tmp_path / "project"
    repo.mkdir()
    source = repo / "logic.py"
    source.write_text("def inc(x):\n    return x - 1\n")
    (repo / "test_logic.py").write_text(
        "from logic import inc\n\ndef test_inc():\n    assert inc(0) == 1\n"
    )
    config_path = repo / "harness.json"
    config_path.write_text(
        json.dumps(
            {
                "repo": ".",
                "implementation_files": ["logic.py"],
                "protected_files": ["test_logic.py"],
                "working_tests": f'"{sys.executable}" -m pytest -q test_logic.py',
            }
        )
    )

    async def check():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "verify_and_repair.cli", "mcp", "--config", str(config_path)],
            env={**os.environ, "PYTHONPATH": str(Path(harness.__file__).parents[1])},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "begin_repair_task",
                "verify_code",
                "apply_focused_edit",
            }
            started = await session.call_tool(
                "begin_repair_task", {"task": "Repair generated inc"}
            )
            assert not started.isError
            session_id = json.loads(started.content[0].text)["session_id"]
            failed = await session.call_tool("verify_code", {"session_id": session_id})
            feedback = json.loads(failed.content[0].text)
            assert feedback["next_action"] == "repair", feedback
            assert "inc(0) == 1" in feedback["counterexamples"][0]["detail"]
            snippet = feedback["repair_context"]["snippets"][0]
            assert snippet["path"] == "logic.py"
            applied = await session.call_tool(
                "apply_focused_edit",
                {
                    "session_id": session_id,
                    "file": "logic.py",
                    "sha256": snippet["sha256"],
                    "old": "return x - 1",
                    "new": "return x + 1",
                },
            )
            assert json.loads(applied.content[0].text)["applied"]
            passed = await session.call_tool("verify_code", {"session_id": session_id})
            assert json.loads(passed.content[0].text)["verified"]

    asyncio.run(check())
