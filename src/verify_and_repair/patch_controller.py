from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any

from .contracts import ContractPlan, contained_file
from .controller import BudgetReached, Controller
from .evidence import evidence_delta
from .execution import CommandResult, revision, run_command, split_command, utc_now
from .patching import apply_sources, response_message, workspace_state


class PatchController(Controller):
    def __init__(
        self,
        *,
        contract_manifest,
        symbolic_seconds=5.0,
        verification_gate="symbolic",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.manifest_path = Path(contract_manifest)
        self.symbolic_seconds = symbolic_seconds
        self.verification_gate = verification_gate
        self.plan: ContractPlan | None = None
        self.cache: dict[str, dict[str, Any]] = {}
        self.context_state: dict[str, str] = {}
        self.protected: dict[str, str] = {}
        self.symbolic_status = "not_applicable"
        self.last_patch_error = None
        self.record.config.update(
            {
                "repair_mode": "patch",
                "contract_manifest": str(contract_manifest),
                "symbolic_seconds": symbolic_seconds,
                "verification_gate": verification_gate,
                "no_agent_tools": True,
                "response_format": "source-files-v1",
                "claim_scope": (
                    "Frozen public examples only; not a symbolic proof"
                    if verification_gate == "public-tests"
                    else "All obligations under the manifest's explicit preconditions and bounds"
                ),
            }
        )

    def preflight(self):
        result = super().preflight()
        problems = result["problems"]
        if self.verification_gate not in {"symbolic", "public-tests"}:
            problems.append(
                f"Unknown patch verification gate: {self.verification_gate}"
            )
        if self.record.condition != "adaptive" or self.llm_mode not in {
            "agency",
            "copilot",
        }:
            problems.append(
                "Patch-only repair requires condition=adaptive and an Agency or Copilot backend"
            )
        if not math.isfinite(self.symbolic_seconds) or self.symbolic_seconds <= 0:
            problems.append("symbolic-seconds must be positive and finite")
        if self.output.resolve().is_relative_to(self.repo):
            problems.append(
                "Patch-only artifacts must be outside the coding repository"
            )
        try:
            self.plan = ContractPlan.load(self.repo, self.manifest_path)
            state = workspace_state(self.repo)
            required = {
                *self.plan.editable,
                *self.plan.contexts,
                self.plan.manifest.relative_to(self.repo).as_posix(),
            }
            if not required <= state.keys():
                problems.append(
                    "Implementation, contracts and manifest must not be Git-ignored"
                )
            self.protected = {
                name: digest
                for name, digest in state.items()
                if name not in self.plan.editable
            }
            self.record.config["contract_claim"] = self.plan.claim
        except (OSError, ValueError, TypeError, SyntaxError) as error:
            problems.append(str(error))
        result["ok"] = not problems
        return result

    def require_plan(self) -> ContractPlan:
        if self.plan is None:
            raise RuntimeError("Contract preflight has not completed")
        return self.plan

    def editable_files(self) -> tuple[str, ...]:
        return self.require_plan().editable

    def assert_protected(self, state):
        current = {
            name: digest
            for name, digest in state.items()
            if name not in self.editable_files()
        }
        if current != self.protected:
            raise RuntimeError(
                "A protected dependency, contract, configuration or test changed"
            )

    def verify(self):
        complete = False
        try:
            if self.verification_gate == "public-tests":
                argv = split_command(self.working_tests)
                if argv[0].lower() in {"python", "python.exe", sys.executable.lower()}:
                    prefix = self.output.parent / f"no-bytecode-{uuid.uuid4().hex}"
                    argv[1:1] = ["-B", "-X", f"pycache_prefix={prefix}"]
                event = self.command("visible-tests", argv, 30)
                complete = event.outcome in {"pass", "fail"}
                return
            self.require_budget()
            plan = self.require_plan()
            state = workspace_state(self.repo)
            self.assert_protected(state)
            key = plan.fingerprint(state, self.symbolic_seconds)
            before, started = revision(self.repo), utc_now()
            cached = key in self.cache
            if cached:
                result = CommandResult(
                    [], 0, "pass", 0, json.dumps(self.cache[key]), ""
                )
            else:
                result = run_command(
                    [
                        sys.executable,
                        "-m",
                        "verify_and_repair.contracts",
                        "--repo",
                        str(self.repo),
                        "--manifest",
                        str(plan.manifest),
                        "--seconds",
                        str(max(0.01, self.symbolic_seconds / (2 * len(plan.targets)))),
                    ],
                    self.repo,
                    min(
                        self.symbolic_seconds,
                        self.remaining(),
                        self.verifier_remaining(),
                    ),
                )
            try:
                if result.outcome == "timeout":
                    raise ValueError("Symbolic process exceeded its wall budget")
                structured = json.loads(result.stdout.strip().splitlines()[-1])
                status = structured["status"]
                if status not in {
                    "proved",
                    "disproved",
                    "unknown",
                    "not_applicable",
                    "error",
                }:
                    raise ValueError(f"Unknown symbolic status: {status}")
                if status == "proved" and not structured.get("checkable_count"):
                    raise ValueError(
                        "A proof must contain at least one checkable obligation"
                    )
                if status == "proved" and result.exit_code != 0:
                    raise ValueError("Symbolic process did not complete successfully")
            except (ValueError, KeyError, TypeError, IndexError) as error:
                status = "unknown" if result.outcome == "timeout" else "error"
                structured = {
                    "status": status,
                    "reason": str(error),
                    "stderr": result.stderr[-1000:],
                }
            if not cached and status in {"proved", "disproved"}:
                self.cache[key] = structured
            result.outcome = {
                "proved": "pass",
                "disproved": "fail",
                "error": "error",
            }.get(status, "unknown")
            event = self.add_result(
                "contract-crosshair", result, before, revision(self.repo), started
            )
            event.metadata.update(
                {
                    "structured_evidence": structured,
                    "cache_hit": cached,
                    "fingerprint": key,
                }
            )
            self.symbolic_status = status
            complete = status in {"proved", "disproved"}
            self.persist()
        finally:
            self.finish_repair(complete)

    def select(self):
        if self.last_patch_error:
            raise BudgetReached("patch_rejected")
        if self.verification_gate == "public-tests":
            event = self.latest.get("visible-tests")
            if event is not None and event.outcome == "pass":
                return "done", None
            if event is None or event.outcome != "fail":
                raise BudgetReached("public_tests_infrastructure")
        else:
            if self.symbolic_status == "proved":
                return "done", None
            if self.symbolic_status != "disproved":
                raise BudgetReached(f"symbolic_{self.symbolic_status}")
        if self.snapshot() in self.sent_evidence:
            return "stalled", None
        self.require_budget()
        self.require_budget("llm")
        return "repair", None

    def packet(self):
        plan = self.require_plan()
        self.context_state = workspace_state(self.repo)
        self.assert_protected(self.context_state)
        return json.dumps(
            {
                "task": self.record.task_text,
                "implementation": {
                    name: contained_file(self.repo, name).read_text(encoding="utf-8")
                    for name in plan.editable
                },
                "trusted_contracts": {
                    name: contained_file(self.repo, name).read_text(encoding="utf-8")
                    for name in plan.contexts
                },
                "claim": plan.claim,
                "evidence_delta": evidence_delta(
                    self.previous_repair, self.snapshot()
                ).compact(),
                "response_format": (
                    'Return ONLY JSON: {"files": [{"path": "supplied implementation path", '
                    '"content": "complete replacement Python source"}]}. '
                    "Include only changed files, using their exact supplied paths. "
                    "Provide the complete module source, not a diff or a partial snippet. "
                    "Preserve public APIs, docstrings and unrelated code. "
                    "Do not return Git hunks, line numbers or commentary. All context is supplied. "
                    "Do not call tools, generate tests, validate the patch, delegate, or write a review. "
                    "The controller applies the patch and checks the actual implementation against "
                    + (
                        "frozen public examples only."
                        if self.verification_gate == "public-tests"
                        else "frozen contracts."
                    )
                ),
            },
            ensure_ascii=True,
        )

    def agency_arguments(self, agency_exe, prompt, usage_path):
        # Agency drops empty allowlists; exclude the sole explicitly allowed tool.
        return [
            agency_exe,
            "copilot",
            "--no-aec",
            "--no-default-mcps",
            "--no-config-plugins",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--available-tools",
            "task_complete",
            "--excluded-tools",
            "task_complete",
            "--max-autopilot-continues",
            "0",
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
        ]

    def agency_directory(self):
        path = (
            self.output.parent
            / "patch-requests"
            / f"{self.record.run_id}-{self.record.agent_sessions}"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def repair_capability(self):
        return "source"

    def repair_metadata(self, result, usage):
        raw_path = (
            self.artifact_directory()
            / f"{self.record.run_id}-{self.record.agent_sessions}-response.jsonl"
        )
        raw_path.write_text(result.stdout, encoding="utf-8")
        metadata: dict[str, Any] = {
            "patch_applied": False,
            "response_format": "source-files-v1",
        }
        try:
            if result.outcome != "pass" or not self.record.model_usage_complete:
                raise ValueError(
                    "Patch inference failed or native usage was unavailable"
                )
            models = usage.get("modelMetrics", {})
            if not models or any(
                name.lower() != self.agency_model.lower() for name in models
            ):
                raise ValueError("Patch inference did not use only the requested model")
            requests = sum(metric["requests"]["count"] for metric in models.values())
            content, generation = response_message(
                result.stdout, expected_requests=requests
            )
            metadata.update(generation)
            self.require_budget()
            patch, application = self.apply_model_response(content)
            metadata.update(application)
            patch_path = raw_path.with_suffix(".patch")
            patch_path.write_bytes(patch.encode("utf-8"))
            metadata["patch_file"] = str(patch_path)
            return metadata
        except (OSError, ValueError, TypeError, SyntaxError, BudgetReached) as error:
            self.last_patch_error = str(error)
            result.outcome = "error"
            result.stderr += f"\nPatch rejected: {error}"
            return {**metadata, "patch_error": str(error)}

    def apply_model_response(self, content):
        return apply_sources(
            self.repo,
            content,
            self.editable_files(),
            self.context_state,
            self.output.parent / "patch-validation",
            min(10, self.remaining()),
        )
