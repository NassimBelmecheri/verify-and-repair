from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import PurePosixPath

from .contracts import ContractPlan, contained_file
from .controller import BudgetReached, Controller
from .evidence import Snapshot, evidence_delta, failures
from .execution import split_command
from .patch_controller import PatchController
from .patching import workspace_state


class HybridController(PatchController):
    def __init__(
        self,
        *,
        implementation_files,
        context_files=(),
        contract_manifest=None,
        lean_context_bytes=16000,
        lint_command=None,
        typecheck_command=None,
        **kwargs,
    ):
        super().__init__(contract_manifest=contract_manifest or "", **kwargs)
        self.source_files = tuple(implementation_files)
        self.context_files = tuple(context_files)
        self.contract_enabled = contract_manifest is not None
        self.lean_context_bytes = lean_context_bytes
        self.checks = {"visible-tests": self.working_tests}
        if lint_command:
            self.checks["lint"] = lint_command
        if typecheck_command:
            self.checks["typecheck"] = typecheck_command
        self.route = "source"
        self.route_evidence: dict[str, set[Snapshot]] = {
            "source": set(),
            "tools": set(),
        }
        self.route_reason = "initial_proposal"
        self.protection_error = None
        self.last_repair_changed = False
        self.record.config.update(
            {
                "repair_mode": "hybrid",
                "optional_contracts": self.contract_enabled,
                "implementation_files": self.source_files,
                "context_files": self.context_files,
                "lean_context_bytes": lean_context_bytes,
                "repeat_suppression": "evidence_per_capability; source-to-tools transition at most once",
                "verification_claim": "visible_checks_only",
                "symbolic_status": "not_applicable",
            }
        )

    def editable_files(self) -> tuple[str, ...]:
        return self.source_files

    def preflight(self):
        result = Controller.preflight(self)
        problems = result["problems"]
        if self.record.condition != "adaptive" or self.llm_mode not in {
            "agency",
            "copilot",
        }:
            problems.append(
                "Hybrid repair requires condition=adaptive and an Agency or Copilot backend"
            )
        if self.output.resolve().is_relative_to(self.repo):
            problems.append("Hybrid artifacts must be outside the coding repository")
        if not math.isfinite(self.lean_context_bytes) or self.lean_context_bytes <= 0:
            problems.append("lean-context-bytes must be positive and finite")
        if not math.isfinite(self.symbolic_seconds) or self.symbolic_seconds <= 0:
            problems.append("symbolic-seconds must be positive and finite")
        try:
            if not self.source_files or len(set(self.source_files)) != len(
                self.source_files
            ):
                raise ValueError(
                    "Implementation files must be a nonempty unique allowlist"
                )
            if set(self.source_files) & set(self.context_files):
                raise ValueError("Read-only context cannot be editable")
            for name in (*self.source_files, *self.context_files):
                contained_file(self.repo, name)
            for name in self.source_files:
                path = PurePosixPath(name.replace("\\", "/"))
                if (
                    path.as_posix() != name
                    or path.suffix != ".py"
                    or any(
                        part in {"tests", "test", "evaluator", ".git", ".github"}
                        for part in path.parts
                    )
                    or path.name.startswith("test_")
                ):
                    raise ValueError(f"Ineligible implementation path: {name}")
            for name, command in self.checks.items():
                if not split_command(command):
                    raise ValueError(f"Empty check command: {name}")
            if self.contract_enabled:
                self.plan = ContractPlan.load(self.repo, self.manifest_path)
                if set(self.plan.editable) != set(self.source_files):
                    raise ValueError(
                        "Contract and hybrid implementation allowlists must match"
                    )
                if set(self.plan.contexts) & set(self.source_files):
                    raise ValueError("Contracts cannot be editable")
            state = workspace_state(self.repo)
            required = {*self.source_files, *self.context_files}
            if self.plan:
                required.update(self.plan.contexts)
                required.add(self.plan.manifest.relative_to(self.repo).as_posix())
            if not required <= state.keys():
                raise ValueError(
                    "Implementation, context and contracts must not be Git-ignored"
                )
            self.protected = {
                name: value
                for name, value in state.items()
                if name not in self.source_files
            }
        except (OSError, ValueError, TypeError, SyntaxError) as error:
            problems.append(str(error))
        result["ok"] = not problems
        return result

    def snapshot(self):
        events = [
            event
            for name, event in self.latest.items()
            if name != "contract-crosshair" or self.symbolic_status == "disproved"
        ]
        return failures(events, str(self.repo))

    def verify(self):
        complete = False
        try:
            if self.protection_error:
                raise BudgetReached("protected_files_changed")
            self.assert_protected(workspace_state(self.repo))
            for name, command in self.checks.items():
                argv = split_command(command)
                if argv[0].lower() in {"python", "python.exe", sys.executable.lower()}:
                    prefix = self.output.parent / f"no-bytecode-{uuid.uuid4().hex}"
                    argv[1:1] = ["-B", "-X", f"pycache_prefix={prefix}"]
                event = self.command(name, argv, 120)
                if event.outcome not in {"pass", "fail"} or (
                    name == "visible-tests" and event.exit_code not in {0, 1}
                ):
                    raise BudgetReached(f"{name}_infrastructure")
                self.assert_protected(workspace_state(self.repo))
            complete = True
            if self.contract_enabled and all(
                self.latest[name].outcome == "pass" for name in self.checks
            ):
                PatchController.verify(self)
                self.record.config["symbolic_status"] = self.symbolic_status
                if self.symbolic_status == "error":
                    raise BudgetReached("symbolic_error")
        finally:
            self.finish_repair(complete)

    def _context(self):
        names = dict.fromkeys(
            (
                *self.source_files,
                *self.context_files,
                *(self.plan.contexts if self.plan else ()),
            )
        )
        return {
            name: contained_file(self.repo, name).read_text(encoding="utf-8")
            for name in names
        }

    def select(self):
        if self.protection_error:
            raise BudgetReached("protected_files_changed")
        self.require_budget()
        if (
            self.last_repair_changed
            and not self.last_patch_error
            and all(self.latest[name].outcome == "pass" for name in self.checks)
            and self.symbolic_status != "disproved"
        ):
            self.record.config["verification_claim"] = (
                "visible_checks_and_scoped_contract"
                if self.symbolic_status == "proved"
                else "visible_checks_only"
            )
            return "done", None
        current = self.snapshot()
        if self.route == "source":
            context_size = len(
                json.dumps(self._context(), ensure_ascii=True).encode("utf-8")
            )
            reason = (
                "source_response_rejected"
                if self.last_patch_error
                else "unchanged_visible_evidence"
                if current in self.route_evidence["source"]
                else "context_requires_exploration"
                if context_size > self.lean_context_bytes
                else None
            )
            if reason:
                self.route = "tools"
                self.route_reason = reason
        if current in self.route_evidence[self.route]:
            return "stalled", None
        self.require_budget("llm")
        self.sent_evidence = self.route_evidence[self.route]
        return "repair", None

    def packet(self):
        current = self.snapshot()
        delta = evidence_delta(self.previous_repair, current).compact()
        common = {
            "task": self.record.task_text,
            "evidence_delta": delta,
            "allowed_implementation_files": self.source_files,
            "public_checks": self.checks,
            "symbolic_claim": self.plan.claim if self.plan else None,
            "route_reason": self.route_reason,
        }
        self.context_state = workspace_state(self.repo)
        self.assert_protected(self.context_state)
        if self.route == "tools":
            return json.dumps(
                {
                    **common,
                    "last_source_error": self.last_patch_error,
                    "instruction": (
                        "Explore this repository and make one minimal repair using normal coding tools. "
                        "Modify only the allowlisted existing implementation files. "
                        "Do not generate or modify tests, contracts, configuration or other files. "
                        "Do not inspect hidden evaluators, other workspaces, past sessions or private data. "
                        "Do not delegate or access the internet. The controller runs the public checks; "
                        "stop after the repair."
                    ),
                }
            )
        context = self._context()
        return json.dumps(
            {
                **common,
                "implementation": {name: context[name] for name in self.source_files},
                "read_only_context": {
                    name: content
                    for name, content in context.items()
                    if name not in self.source_files
                },
                "response_format": (
                    'Return ONLY JSON: {"files":[{"path":"supplied implementation path",'
                    '"content":"complete replacement Python source"}]}. Include only changed files. '
                    "Preserve APIs and unrelated code. Do not return diffs or partial snippets. "
                    "Do not call tools, generate tests, validate code, delegate or write a review. "
                    "If supplied context is insufficient, return exactly "
                    '{"needs_exploration":true}; the controller will escalate, not guess.'
                ),
            },
            ensure_ascii=True,
        )

    def agency_arguments(self, agency_exe, prompt, usage_path):
        if self.route == "source":
            return PatchController.agency_arguments(
                self, agency_exe, prompt, usage_path
            )
        return [
            agency_exe,
            "copilot",
            "--no-aec",
            "--no-default-mcps",
            "--no-config-plugins",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--excluded-tools",
            "task",
            "run_dynamic_workflow",
            "session_store_sql",
            "store_memory",
            "web_fetch",
            "semantic_code_search",
            "--prompt",
            prompt,
            "--silent",
            "--output-format",
            "json",
            "--usage-output-file",
            str(usage_path),
            "--model",
            self.agency_model,
            "--reasoning-effort",
            self.reasoning_effort,
            "--allow-tool",
            "write",
            "--allow-tool",
            "shell(python:*)",
        ]

    def agency_directory(self):
        return (
            PatchController.agency_directory(self)
            if self.route == "source"
            else self.repo
        )

    def repair_capability(self):
        return self.route

    def repair_metadata(self, result, usage):
        if self.route == "source":
            metadata = PatchController.repair_metadata(self, result, usage)
        else:
            raw_path = self.artifact_directory() / (
                f"{self.record.run_id}-{self.record.agent_sessions}-response.jsonl"
            )
            raw_path.write_text(result.stdout, encoding="utf-8")
            metadata = {"native_response_file": str(raw_path)}
            try:
                self.assert_protected(workspace_state(self.repo))
                self.last_patch_error = None
            except RuntimeError as error:
                self.protection_error = str(error)
                result.outcome = "error"
                result.stderr += f"\nProtected files changed: {error}"
                metadata["protection_error"] = self.protection_error
        self.last_repair_changed = (
            bool(metadata.get("patch_applied"))
            if self.route == "source"
            else result.outcome == "pass"
            and workspace_state(self.repo) != self.context_state
        )
        return {
            **metadata,
            "repair_capability": self.route,
            "route_reason": self.route_reason,
        }
