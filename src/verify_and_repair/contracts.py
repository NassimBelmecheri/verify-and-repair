from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import json
import math
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


def contained_file(repo: Path, name: str) -> Path:
    relative = PurePosixPath(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in name:
        raise ValueError(f"Expected a repository-relative file: {name}")
    path = repo.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(repo.resolve()) or path.is_symlink():
        raise ValueError(f"File escapes repository or is a symlink: {name}")
    if not path.is_file():
        raise ValueError(f"Missing file: {name}")
    return path


@dataclass(frozen=True)
class ContractPlan:
    repo: Path
    manifest: Path
    editable: tuple[str, ...]
    contexts: tuple[str, ...]
    targets: tuple[str, ...]
    claim: str

    @classmethod
    def load(cls, repo: Path, manifest: Path) -> ContractPlan:
        repo = repo.resolve()
        manifest = (manifest if manifest.is_absolute() else repo / manifest).resolve()
        if not manifest.resolve().is_relative_to(repo):
            raise ValueError(
                "The public contract manifest must be inside the repository"
            )
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("The contract manifest must be a JSON object")
        groups = {}
        for field in ("implementation_files", "contract_files", "targets"):
            values = raw.get(field)
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ValueError(f"{field} must be a nonempty string list")
            groups[field] = tuple(
                dict.fromkeys(value.replace("\\", "/") for value in values)
            )
        editable = groups["implementation_files"]
        contexts = groups["contract_files"]
        if set(editable) & set(contexts):
            raise ValueError("Contracts cannot be editable implementation files")
        functions = set()
        for name in (*editable, *contexts):
            path = contained_file(repo, name)
            if path.suffix != ".py" or any(
                part in {"evaluator", ".git", "tests"}
                for part in PurePosixPath(name).parts
            ):
                raise ValueError(f"Ineligible implementation/contract path: {name}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
            if name in contexts:
                module = ".".join(PurePosixPath(name).with_suffix("").parts)
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef):
                        doc = ast.get_docstring(node) or ""
                        if any(
                            line.strip().startswith("post:")
                            for line in doc.splitlines()
                        ):
                            functions.add(f"{module}.{node.name}")
        if not set(groups["targets"]) <= functions:
            raise ValueError(
                "Every target must name a public top-level PEP316 contract with a postcondition"
            )
        claim = raw.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError(
                "The manifest must describe the domain/bounds of its claim"
            )
        return cls(repo, manifest, editable, contexts, groups["targets"], claim)

    def fingerprint(self, state: dict[str, str], seconds: float) -> str:
        payload = {
            "files": state,
            "manifest": self.manifest.read_text(encoding="utf-8"),
            "seconds": seconds,
            "python": sys.version,
            "crosshair": importlib.metadata.version("crosshair-tool"),
            "z3": importlib.metadata.version("z3-solver"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check(plan: ContractPlan, seconds: float) -> dict[str, Any]:
    # A fresh bytecode namespace prevents same-size, same-second edits using stale .pyc files.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"verify-and-repair-contract-{uuid.uuid4().hex}"
    )
    sys.path.insert(0, str(plan.repo))
    from crosshair.core_and_libs import analyze_function, run_checkables
    from crosshair.options import AnalysisKind, AnalysisOptionSet

    options = AnalysisOptionSet(
        analysis_kind=(AnalysisKind.PEP316,),
        enabled=True,
        report_all=True,
        per_condition_timeout=seconds,
        per_path_timeout=min(1.0, seconds),
    )
    obligations = []
    for target in plan.targets:
        module_name, function_name = target.rsplit(".", 1)
        function = getattr(importlib.import_module(module_name), function_name)
        checkables = analyze_function(function, options)
        messages = run_checkables(checkables)
        states = [message.state.value for message in messages]
        if not checkables:
            status = "not_applicable"
        elif any(state in {"post_fail", "exec_err"} for state in states):
            status = "disproved"
        elif any(state in {"syntax_err", "import_err", "post_err"} for state in states):
            status = "error"
        elif (
            states
            and all(state == "confirmed" for state in states)
            and len(messages) == len(checkables)
        ):
            status = "proved"
        else:
            status = "unknown"
        obligations.append(
            {
                "target": target,
                "status": status,
                "checkable_count": len(checkables),
                "messages": [
                    {
                        "state": message.state.value,
                        "message": message.message,
                        "condition": message.condition_src,
                    }
                    for message in messages
                ],
            }
        )
    statuses = {item["status"] for item in obligations}
    status = next(
        (
            name
            for name in ("error", "disproved", "not_applicable", "unknown")
            if name in statuses
        ),
        "proved",
    )
    return {
        "status": status,
        "backend": "CrossHair/Z3",
        "claim": plan.claim,
        "obligations": obligations,
        "checkable_count": sum(item["checkable_count"] for item in obligations),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=2)
    args = parser.parse_args()
    try:
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError("Symbolic timeout must be positive and finite")
        result = check(ContractPlan.load(args.repo, args.manifest), args.seconds)
    except (OSError, ValueError, ImportError, AttributeError, TypeError) as error:
        result = {"status": "error", "error": str(error)}
    print(json.dumps(result))
    return {"proved": 0, "disproved": 1, "unknown": 2, "not_applicable": 2, "error": 3}[
        result["status"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
