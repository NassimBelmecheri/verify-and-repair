import importlib.util
import json
import sys
from pathlib import Path

def main(argv=None):
    path = Path((argv or sys.argv[1:] or ["verification/smt_spec.py"])[0])
    if not path.exists():
        print(json.dumps({"status": "not_applicable", "reason": f"missing {path}"}))
        return 2
    try:
        import z3
    except Exception as e:
        print(json.dumps({"status": "infrastructure_error", "reason": str(e)}))
        return 3
    try:
        spec = importlib.util.spec_from_file_location("task_smt_spec", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        built = module.build()
        solver, symbols = (built["solver"], built.get("symbols", {})) if isinstance(built, dict) else built
        result = solver.check()
        if result == z3.sat:
            model = solver.model()
            ce = {k: str(model.eval(v, model_completion=True)) for k, v in symbols.items()}
            print(json.dumps({"status": "counterexample", "result": "sat", "counterexample": ce}))
            return 1
        if result == z3.unsat:
            print(json.dumps({"status": "verified", "result": "unsat"}))
            return 0
        print(json.dumps({"status": "unknown", "result": str(result)}))
        return 2
    except Exception as e:
        print(json.dumps({"status": "spec_error", "reason": repr(e)}))
        return 3

if __name__ == "__main__":
    raise SystemExit(main())
