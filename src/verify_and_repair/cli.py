import argparse
import json
import os
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from .api import TaskConfig, solve, verify
from .backends import BACKENDS
from .contracts import contained_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="verify-and-repair")
    commands = parser.add_subparsers(dest="cmd", required=True)
    configure = commands.add_parser(
        "configure", help="Create a reusable public task configuration"
    )
    configure.add_argument("--repo", type=Path, required=True)
    configure.add_argument("--implementation-file", action="append", required=True)
    configure.add_argument("--context-file", action="append", default=[])
    configure.add_argument("--working-tests", default="python -m pytest -q tests")
    configure.add_argument("--config", type=Path)
    configure.add_argument("--model", default="gpt-6.1-sol")
    configure.add_argument("--agency-exe")
    configure.add_argument("--copilot-exe")
    configure.add_argument("--backend", choices=BACKENDS, default="agency")

    verification = commands.add_parser(
        "verify", help="Run project checks only; no model calls or automatic repair"
    )
    verification.add_argument("--repo", type=Path, required=True)
    verification.add_argument("--working-tests", required=True)
    verification.add_argument("--lint-command")
    verification.add_argument("--typecheck-command")
    verification.add_argument(
        "--timeout-seconds", type=float, default=180, help="Timeout per check command"
    )
    verification.add_argument("--output", type=Path)

    mcp = commands.add_parser(
        "mcp", help="Serve counterexample feedback to any local stdio MCP harness"
    )
    mcp.add_argument("--config", type=Path, required=True)
    mcp.add_argument("--state-dir", type=Path)

    repair = commands.add_parser(
        "solve", help="Lean repair with automatic tools fallback; public checks only"
    )
    repair.add_argument("--config", type=Path, required=True)
    task = repair.add_mutually_exclusive_group(required=True)
    task.add_argument("--task")
    task.add_argument("--task-file", type=Path)
    repair.add_argument("--backend", choices=BACKENDS)
    repair.add_argument("--copilot-exe")
    repair.add_argument("--agency-exe")
    repair.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.cmd == "mcp":
        from .mcp_server import serve

        try:
            serve(args.config, args.state_dir)
        except (ValueError, OSError, TypeError, RuntimeError) as error:
            parser.error(str(error))
        return

    if args.cmd == "configure":
        repo = args.repo.resolve()
        target = (args.config or repo / "verify-and-repair.json").resolve()
        if target.exists():
            parser.error(f"Config already exists; refusing to overwrite: {target}")
        try:
            configured_repo = os.path.relpath(repo, target.parent)
        except ValueError:
            configured_repo = str(repo)
        data = {
            "repo": configured_repo,
            "implementation_files": [
                name.replace("\\", "/") for name in args.implementation_file
            ],
            "context_files": [name.replace("\\", "/") for name in args.context_file],
            "working_tests": args.working_tests,
            "profile": "lean",
            "model": args.model,
            "reasoning_effort": "medium",
            "backend": args.backend,
        }
        if args.agency_exe:
            data["agency_exe"] = args.agency_exe
        if args.copilot_exe:
            data["copilot_exe"] = args.copilot_exe
        try:
            for name in (*data["implementation_files"], *data["context_files"]):
                contained_file(repo, name)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
        print(json.dumps({"config": str(target), "profile": "lean"}))
        return

    if args.cmd == "verify":
        try:
            repo = args.repo.resolve()
            output = (
                args.output
                or repo.parent
                / "verify-and-repair-results"
                / f"verify-{uuid.uuid4().hex}.json"
            ).resolve()
            if output.is_relative_to(repo):
                raise ValueError("Verification artifacts must be outside the project")
            if output.exists():
                raise ValueError(
                    f"Output already exists; refusing to overwrite: {output}"
                )
            checks = verify(
                repo,
                args.working_tests,
                lint_command=args.lint_command,
                typecheck_command=args.typecheck_command,
                timeout_seconds=args.timeout_seconds,
            )
            verified = all(result.outcome == "pass" for result in checks.values())
            report = {
                "verified": verified,
                "claim": "public_checks_only",
                "repo": str(repo),
                "checks": {name: asdict(result) for name, result in checks.items()},
                "agent_sessions": 0,
                "model_requests": 0,
                "output": str(output),
                "private_evaluation": "not run; public checks are not proof of task correctness",
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2)
        except (ValueError, OSError, TypeError) as error:
            parser.error(str(error))
        print(json.dumps(report, indent=2))
        if not verified:
            infrastructure_error = any(
                result.outcome in {"error", "timeout"} for result in checks.values()
            )
            raise SystemExit(2 if infrastructure_error else 1)
        return

    try:
        config = TaskConfig.load(args.config)
        config = replace(
            config,
            backend=args.backend or config.backend,
            agency_exe=args.agency_exe or config.agency_exe,
            copilot_exe=args.copilot_exe or config.copilot_exe,
        )
        text = (
            args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
        )
        output = (
            args.output
            or config.repo.parent
            / "verify-and-repair-results"
            / f"{uuid.uuid4().hex}.json"
        )
        record = solve(config, text, output)
    except (ValueError, OSError, TypeError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "verified": record.declared_verified,
                "claim": record.config.get("verification_claim"),
                "termination_reason": record.termination_reason,
                "agency_sessions": record.agency_sessions,
                "agent_sessions": record.agent_sessions,
                "copilot_sessions": record.copilot_sessions,
                "backend": config.backend,
                "model_requests": record.model_requests,
                "native_aiu": record.nano_aiu / 1e9,
                "wall_seconds": record.wall_seconds,
                "output": str(output),
                "private_evaluation": "not run; public checks are not proof of task correctness",
            },
            indent=2,
        )
    )
    if not record.declared_verified:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
