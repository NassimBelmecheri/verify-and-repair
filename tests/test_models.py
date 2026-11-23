from verify_and_repair.models import RunRecord

def test_counters_start_zero():
    r=RunRecord("1.4","r","t","adaptive",".","x","now")
    assert r.agency_attempts == r.agency_successful_calls == r.llm_calls == 0
