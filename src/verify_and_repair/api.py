from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .backends import BACKENDS
from .contracts import contained_file
from .execution import CommandResult, run_command, split_command
from .hybrid_controller import HybridController
from .models import Budget, RunRecord
from .patching import _unique_json_object

PROFILES = ("lean",)


@dataclass
class TaskConfig:
    repo: Path
    implementation_files: tuple[str, ...]
    working_tests: str
    context_files: tuple[str, ...] = ()
    profile: str = "lean"
    model: str = "gpt-6.1-sol"
    reasoning_effort: str = "medium"
    agency_exe: str | None = None
    backend: str = "agency"
    copilot_exe: str | None = None
    contract_manifest: Path | None = None
    lint_command: str | None = None
    typecheck_command: str | None = None
    lean_context_bytes: int = 16000
    symbolic_seconds: float = 5
    budget: Budget = field(
        default_factory=lambda: Budget(max_verifier_wall_seconds=180)
    )

    @classmethod
    def load(cls, path: Path) -> TaskConfig:
        path = path.resolve()
        raw = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object
        )
        allowed = set(cls.__dataclass_fields__)
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError(
                "Task config must be an object with only documented fields"
            )
        required = {"repo", "implementation_files", "working_tests"}
        if not required <= raw.keys():
            raise ValueError(
                "Task config requires repo, implementation_files and working_tests"
            )
        for name in (
            "repo",
            "working_tests",
            "profile",
            "model",
            "reasoning_effort",
            "backend",
        ):
            if name in raw and (
                not isinstance(raw[name], str) or not raw[name].strip()
            ):
                raise ValueError(f"{name} must be a nonempty string")
        for name in (
            "agency_exe",
            "copilot_exe",
            "contract_manifest",
            "lint_command",
            "typecheck_command",
        ):
            if raw.get(name) is not None and (
                not isinstance(raw[name], str) or not raw[name].strip()
            ):
                raise ValueError(f"{name} must be a nonempty string or null")
        for name in ("implementation_files", "context_files"):
            entries = raw.get(name, [])
            if not isinstance(entries, list) or any(
                not isinstance(item, str) or not item for item in entries
            ):
                raise ValueError(f"{name} must be an array of paths")
            normalized = tuple(item.replace("\\", "/") for item in entries)
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"{name} cannot contain duplicate paths")
            raw[name] = normalized
        limits = raw.get("budget", {})
        if not isinstance(limits, dict) or set(limits) - set(
            Budget.__dataclass_fields__
        ):
            raise ValueError("Unknown budget fields")
        for name, value in limits.items():
            if value is not None and (type(value) not in {int, float}):
                raise ValueError(f"Budget {name} must be numeric")
            if value is None and name not in {
                "max_agency_sessions",
                "max_agent_sessions",
            }:
                raise ValueError(f"Budget {name} cannot be null")
            if (
                name
                in {
                    "max_actions",
                    "max_llm_calls",
                    "max_agency_sessions",
                    "max_agent_sessions",
                    "max_model_requests",
                }
                and value is not None
                and type(value) is not int
            ):
                raise ValueError(f"Budget {name} must be an integer")
        for name in ("lean_context_bytes", "symbolic_seconds"):
            if name in raw and type(raw[name]) not in {int, float}:
                raise ValueError(f"{name} must be numeric")
        if "lean_context_bytes" in raw and type(raw["lean_context_bytes"]) is not int:
            raise ValueError("lean_context_bytes must be an integer")
        raw["budget"] = Budget(**{"max_verifier_wall_seconds": 180, **limits})
        raw["repo"] = (path.parent / raw["repo"]).resolve()
        if raw.get("contract_manifest"):
            raw["contract_manifest"] = Path(raw["contract_manifest"])
        config = cls(**raw)
        if config.profile not in PROFILES:
            raise ValueError(
                "Only the lean production profile is supported; "
                "remove tools/localized selection. Lean retains automatic tools fallback."
            )
        if config.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}")
        if config.reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("Unsupported reasoning_effort")
        if not config.implementation_files:
            raise ValueError("At least one implementation file is required")
        for name in (*config.implementation_files, *config.context_files):
            contained_file(config.repo, name)
        return config


def verify(
    repo: Path,
    working_tests: str,
    *,
    lint_command: str | None = None,
    typecheck_command: str | None = None,
    timeout_seconds: float = 180,
) -> dict[str, CommandResult]:
    """Run project checks without constructing a repair controller or calling a model."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError("The project directory must exist")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Check timeout must be positive and finite")
    commands = {"visible-tests": working_tests}
    if lint_command is not None:
        commands["lint"] = lint_command
    if typecheck_command is not None:
        commands["typecheck"] = typecheck_command
    arguments = {name: split_command(command) for name, command in commands.items()}
    for name, argv in arguments.items():
        if not argv:
            raise ValueError(f"Empty check command: {name}")
    return {
        name: run_command(argv, repo, timeout_seconds, use_current_python=False)
        for name, argv in arguments.items()
    }


def solve(
    config: TaskConfig,
    task: str,
    output: Path,
    *,
    profile: str | None = None,
    task_id: str | None = None,
    history_file: Path | None = None,
) -> RunRecord:
    profile = profile or config.profile
    if profile not in PROFILES or config.backend not in BACKENDS or not task.strip():
        raise ValueError("A supported profile, backend and nonempty task are required")
    controller = HybridController(
        task_id=task_id or hashlib.sha256(task.encode()).hexdigest()[:12],
        repo=config.repo,
        task_text=task,
        condition="adaptive",
        llm_mode=config.backend,
        working_tests=config.working_tests,
        hidden_tests='python -c "raise SystemExit(99)"',
        defer_hidden_evaluation=True,
        output=output,
        budget=config.budget,
        history_file=history_file,
        implementation_files=config.implementation_files,
        context_files=config.context_files,
        contract_manifest=config.contract_manifest,
        lean_context_bytes=config.lean_context_bytes,
        symbolic_seconds=config.symbolic_seconds,
        lint_command=config.lint_command,
        typecheck_command=config.typecheck_command,
        agency_model=config.model,
        reasoning_effort=config.reasoning_effort,
        agency_exe=config.agency_exe,
        copilot_exe=config.copilot_exe,
    )
    controller.record.config["profile"] = profile
    return controller.run()
