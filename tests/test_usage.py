import json

import pytest

from verify_and_repair.usage import (
    merge_receipts,
    task_report,
    usage_summary,
    validate_receipt,
)


def receipt(request_id="r1", **changes):
    return {
        "request_id": request_id,
        "model": "host-model",
        "phase": "generation",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_tokens": 700,
        "cache_write_tokens": 100,
        "reasoning_tokens": 30,
        "cost": 0.02,
        "currency": "USD",
        **changes,
    }


def test_usage_deduplicates_requests_and_keeps_cache_subcounts_separate():
    first = receipt()
    repair = receipt("r2", phase="repair", cost=0.01)
    ledger = {
        "receipts": merge_receipts([first], [first, repair]),
        "task_complete_attested": True,
        "accepted": True,
    }
    summary = usage_summary(ledger)
    assert summary["requests"] == 2
    assert summary["tokens"]["input_tokens"] == 2000
    assert summary["tokens"]["cache_read_tokens"] == 1400
    assert summary["tokens"]["output_tokens"] == 200
    assert summary["reported_cost_by_unit"] == {"USD": 0.03}
    assert summary["cost_per_accepted_task_by_unit"] == {"USD": 0.03}
    assert summary["phases"]["generation"]["requests"] == 1
    with pytest.raises(ValueError, match="Conflicting"):
        merge_receipts([first], [receipt(input_tokens=2000)])


@pytest.mark.parametrize(
    "changes",
    [
        {"input_tokens": True},
        {"cache_read_tokens": 1001},
        {"reasoning_tokens": 101},
        {"output_tokens": -1},
        {"phase": "unknown"},
        {"cost": float("nan")},
        {"currency": ""},
        {"unknown": "ignored?"},
    ],
)
def test_malformed_receipts_are_rejected(changes):
    with pytest.raises(ValueError):
        validate_receipt(receipt(**changes))


def test_missing_usage_cost_and_acceptance_never_become_zero_cost_success():
    empty = usage_summary({})
    assert empty["requests"] is None and empty["tokens"] is None
    assert empty["cost_per_accepted_task_by_unit"] is None
    exported = receipt()
    del exported["cost"], exported["currency"]
    summary = usage_summary(
        {"receipts": [exported], "task_complete_attested": True, "accepted": True}
    )
    assert summary["cost_per_accepted_task_by_unit"] is None
    assert not summary["cost_coverage_complete"]
    partial = usage_summary({"receipts": [receipt()], "accepted": True})
    assert partial["cost_per_accepted_task_by_unit"] is None


def test_different_billing_units_are_never_combined():
    summary = usage_summary(
        {"receipts": [receipt(), receipt("r2", currency="AIU", cost=1.5)]}
    )
    assert summary["reported_cost_by_unit"] == {"USD": 0.02, "AIU": 1.5}
    assert summary["coverage"] == "partial_or_missing"


def test_cost_per_accepted_task_includes_rejected_attempt_costs_and_missing_coverage():
    states = [
        {
            "usage": {
                "receipts": [receipt()],
                "task_complete_attested": True,
                "accepted": True,
            }
        },
        {
            "usage": {
                "receipts": [receipt("r2", cost=0.03)],
                "task_complete_attested": True,
                "accepted": False,
            }
        },
    ]
    report = task_report(states)
    assert report["accepted_tasks"] == 1 and report["tasks"] == 2
    assert report["cost_per_accepted_task_by_unit"] == {"USD": 0.05}
    states.append({"usage": {}})
    assert task_report(states)["cost_per_accepted_task_by_unit"] is None


def test_overlapping_request_exports_cannot_create_a_misleading_task_average():
    state = {
        "usage": {
            "receipts": [receipt()],
            "task_complete_attested": True,
            "accepted": True,
        }
    }
    report = task_report([state, state])
    assert report["overlapping_request_ids"]
    assert report["cost_per_accepted_task_by_unit"] is None
    assert report["requests"] == 1


def test_cli_imports_all_task_phases_idempotently_without_model_calls(
    tmp_path, monkeypatch, capsys
):
    from verify_and_repair import cli
    from verify_and_repair.harness import HarnessConfig, HarnessSessions

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "logic.py").write_text("value = 1\n")
    config = repo / "harness.json"
    config.write_text(
        json.dumps(
            {
                "repo": ".",
                "implementation_files": ["logic.py"],
                "working_tests": "python -V",
            }
        )
    )
    sessions = HarnessSessions(HarnessConfig.load(config))
    session_id = sessions.begin("Implement task")["session_id"]
    state_path = sessions._path(session_id)
    state = sessions._load(state_path)
    sessions._feedback(state, "verified", "public_checks_passed")
    sessions._save(state_path, state)
    export = tmp_path / "host-usage.json"
    export.write_text(
        json.dumps(
            [receipt(), receipt("r2", phase="repair"), receipt("r3", phase="review")]
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify-and-repair",
            "account",
            "--config",
            str(config),
            "--session-id",
            session_id,
            "--receipts",
            str(export),
            "--task-complete",
            "--accepted",
            "yes",
        ],
    )
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["requests"] == 3 and report["accepted"]
    assert report["coverage"] == "task_complete_attested"
    assert report["cost_per_accepted_task_by_unit"] == {"USD": 0.06}
    cli.main()
    repeated = json.loads(capsys.readouterr().out)
    assert repeated == report
    assert sessions.verify(session_id)["host_model_requests"] == 3
    assert sessions.usage_report()["cost_per_accepted_task_by_unit"] == {"USD": 0.06}
    export.write_text(json.dumps([receipt("new-request")]))
    with pytest.raises(ValueError, match="finalized"):
        sessions.import_usage(session_id, export)
