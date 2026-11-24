from verify_and_repair.evidence import evidence_delta, failures
from verify_and_repair.models import Event


def event(name, detail, *, outcome="fail", kind="verifier"):
    return Event(0, kind, name, "now", 1, outcome, stdout_tail=detail)


def test_delta_resolved_remaining_and_new():
    before = failures([event("pytest", "bad interval"), event("ruff", "unused name")])
    after = failures(
        [
            event("pytest", "ok", outcome="pass"),
            event("ruff", "unused name"),
            event("mypy", "incompatible assignment"),
        ]
    )
    delta = evidence_delta(before, after)
    assert {failure.verifier for failure in delta.resolved} == {"pytest"}
    assert {failure.verifier for failure in delta.remaining} == {"ruff"}
    assert {failure.verifier for failure in delta.new} == {"mypy"}


def test_normalization_ignores_timings_order_paths_and_hidden_results():
    before = failures(
        [
            event(
                "pytest",
                "FAILED tests/test_x.py::test_a\n1 failed in 0.14s\n"
                "C:\\repo\\src\\x.py:5 error at 0x1234",
            )
        ],
        "C:\\repo",
    )
    after = failures(
        [
            event(
                "pytest",
                "C:\\repo\\src\\x.py:5 error at 0xabcd\n"
                "1 failed in 10.21s\nFAILED tests/test_x.py::test_a",
            ),
            event("hidden-evaluation", "SECRET", kind="evaluator"),
        ],
        "C:\\repo",
    )
    assert before == after
    assert not evidence_delta(before, after).resolved


def test_semantically_different_numbers_are_not_normalized_away():
    assert failures([event("pytest", "assert capacity == 2")]) != failures(
        [event("pytest", "assert capacity == 3")]
    )


def test_individual_pytest_failures_survive_summary_and_trace_changes():
    before = failures(
        [
            event(
                "targeted-tests",
                "assert 1 == 2\nFAILED tests/test_x.py::test_a - AssertionError\n"
                "FAILED tests/test_x.py::test_b - assert 3 == 4\n2 failed in 0.1s",
            )
        ]
    )
    after = failures(
        [
            event(
                "targeted-tests",
                "different traceback\nFAILED tests/test_x.py::test_b - assert 3 == 4\n"
                "FAILED tests/test_x.py::test_c - AssertionError\n2 failed in 0.2s",
            )
        ]
    )
    delta = evidence_delta(before, after)
    assert len(delta.resolved) == len(delta.remaining) == len(delta.new) == 1
    assert "test_a" in next(iter(delta.resolved)).detail
    assert "test_b" in next(iter(delta.remaining)).detail
    assert "test_c" in next(iter(delta.new)).detail


def test_structured_evidence_uses_canonical_key_order():
    before = event("smt-z3", '{"x": 1, "y": 2}')
    after = event("smt-z3", '{"y": 2, "x": 1}')
    before.metadata["structured_evidence"] = {"x": 1, "y": 2}
    after.metadata["structured_evidence"] = {"y": 2, "x": 1}
    assert failures([before]) == failures([after])


def test_pytest_counterexamples_remain_attached_to_their_own_failure():
    detail = (
        "____ test_a ____\n"
        ">       assert inc(0) == 1\n"
        "E       assert -1 == 1\n"
        "____ test_b ____\n"
        ">       assert double(2) == 4\n"
        "E       assert 2 == 4\n"
        "FAILED tests/test_x.py::test_a - AssertionError\n"
        "FAILED tests/test_x.py::test_b - AssertionError\n"
    )
    snapshot = failures([event("pytest", detail)])
    first = next(f for f in snapshot if "test_a" in f.detail)
    second = next(f for f in snapshot if "test_b" in f.detail)
    assert "inc(0) == 1" in first.detail and "assert -1 == 1" in first.detail
    assert "double(2)" not in first.detail
    assert "double(2) == 4" in second.detail and "assert 2 == 4" in second.detail
    assert "inc(0)" not in second.detail
    assert failures([event("pytest", detail.replace("-1 == 1", "0 == 1"))]) != snapshot


def test_pytest_repeated_test_names_keep_distinct_counterexamples():
    snapshot = failures(
        [
            event(
                "pytest",
                "____ test_value ____\n> assert inc(0) == 1\nE assert -1 == 1\n"
                "____ test_value ____\n> assert inc(2) == 3\nE assert 1 == 3\n"
                "FAILED tests/test_a.py::test_value - AssertionError\n"
                "FAILED tests/test_b.py::test_value - AssertionError\n",
            )
        ]
    )
    first = next(f for f in snapshot if "test_a.py" in f.detail)
    second = next(f for f in snapshot if "test_b.py" in f.detail)
    assert "inc(0)" in first.detail and "inc(2)" not in first.detail
    assert "inc(2)" in second.detail and "inc(0)" not in second.detail
