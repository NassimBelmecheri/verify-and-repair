from verify_and_repair.policy import Candidate, LatencyPolicy, task_features


def test_fast_candidate(tmp_path):
    p = LatencyPolicy(tmp_path / "history.json", min_score=0)
    x = p.choose(
        [Candidate("fast", "", 10, 0.8), Candidate("slow", "", 10, 0.5)],
        {"fast": 5.0, "slow": 20.0},
        50,
    )
    assert x and x[2].name == "fast"


def test_features():
    f = task_features("interval capacity priority bug", ["reservation.py"])
    assert f["boundary"] and f["constraint"]


def test_repair_score_uses_observed_progress_not_patch_existence(tmp_path):
    p = LatencyPolicy(tmp_path / "history.json", llm_estimate=10)
    initial = p.llm_score()
    p.update("agency-repair", 10, False)
    assert p.llm_score() < initial
    p.update("agency-repair", 10, True)
    assert p.llm_score() > initial / 2


def test_backend_repair_histories_do_not_contaminate_each_other(tmp_path):
    p = LatencyPolicy(tmp_path / "history.json", llm_estimate=10)
    copilot_initial = p.llm_score(name="copilot-repair")
    p.update("agency-repair", 60, False)
    assert p.llm_score(name="copilot-repair") == copilot_initial
    p.update("copilot-repair", 20, False)
    assert p.llm_score(name="copilot-repair") < copilot_initial
