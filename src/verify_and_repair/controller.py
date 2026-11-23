from __future__ import annotations

import json
import math
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .backends import copilot_arguments, resolve_copilot
from .evidence import Snapshot, evidence_delta, failures
from .execution import (
    changed_files,
    diff_summary,
    revision,
    run_command,
    split_command,
    utc_now,
)
from .models import Event, RunRecord
from .policy import Candidate, LatencyPolicy, task_features

TAIL = 5000


class BudgetReached(RuntimeError):
    pass


class Controller:
    def __init__(
        self,
        *,
        task_id,
        repo,
        task_text,
        condition,
        working_tests,
        hidden_tests,
        llm_mode,
        output,
        budget,
        crosshair_target=".",
        smt_spec="verification/smt_spec.py",
        history_file=None,
        targeted_test_command=None,
        agency_model="gpt-6.1-sol",
        reasoning_effort="medium",
        agency_exe=None,
        copilot_exe=None,
        defer_hidden_evaluation=False,
    ):
        self.repo = Path(repo).resolve()
        self.working_tests = working_tests
        self.hidden_tests = hidden_tests
        self.llm_mode = llm_mode
        self.output = Path(output)
        self.budget = budget
        self.crosshair_target = crosshair_target
        self.smt_spec = smt_spec
        self.targeted = targeted_test_command or working_tests
        self.agency_model = agency_model
        self.reasoning_effort = reasoning_effort
        self.agency_exe_override = agency_exe
        self.copilot_exe_override = copilot_exe
        self.defer_hidden_evaluation = defer_hidden_evaluation
        self.start: float | None = None
        self.latest: dict[str, Event] = {}
        self.sent_evidence: set[Snapshot] = set()
        self.previous_repair: Snapshot = frozenset()
        self.pending_repair: Event | None = None
        self.policy = LatencyPolicy(
            Path(history_file or self.output.parent / "policy-history.json"),
            min_score=0.0,
        )
        self.record = RunRecord(
            "1.5",
            f"{condition}-{task_id}-{uuid.uuid4().hex[:8]}",
            task_id,
            condition,
            str(self.repo),
            task_text,
            utc_now(),
            config={
                "objective": "time_to_correct",
                "agency_model": agency_model,
                "backend": llm_mode,
                "model": agency_model,
                "reasoning_effort": reasoning_effort,
                "hidden_tests_redacted": True,
                "hidden_evaluation_deferred": defer_hidden_evaluation,
                "model_request_budget_enforcement": "session_boundary",
                "llm_calls_semantics": "agent_sessions (legacy alias)",
                "agency_api_duration_ms_semantics": "native API duration for the selected backend (legacy name)",
                "agency_excluded_tools": ["task", "run_dynamic_workflow"],
                "budget": budget.__dict__,
            },
        )

    def resolve_agency(self) -> str:
        if self.agency_exe_override:
            path = Path(self.agency_exe_override)
            if path.is_file():
                return str(path)
            raise RuntimeError(f"--agency-exe does not exist: {path}")
        found = shutil.which("agency") or shutil.which("agency.exe")
        if found:
            return found
        path = (
            Path.home()
            / "AppData"
            / "Roaming"
            / "agency"
            / "CurrentVersion"
            / "agency.exe"
        )
        if path.is_file():
            return str(path)
        raise RuntimeError("Agency CLI could not be located. Use --agency-exe.")

    def preflight(self) -> dict:
        problems = []
        if not self.repo.is_dir():
            problems.append(f"Repository directory does not exist: {self.repo}")
        elif (
            run_command(["git", "rev-parse", "--show-toplevel"], self.repo, 10).outcome
            != "pass"
        ):
            problems.append(f"Not a Git repository: {self.repo}")
        limits = (
            self.budget.max_wall_seconds,
            self.budget.max_verifier_wall_seconds,
            self.budget.max_actions,
            self.budget.session_limit,
            self.budget.max_model_requests,
            self.budget.target_latency_seconds,
        )
        if any(not math.isfinite(value) or value < 0 for value in limits):
            problems.append("Budgets must be finite and nonnegative.")
        if self.record.condition not in {"baseline", "fixed", "adaptive"}:
            problems.append(f"Unknown condition: {self.record.condition}")
        if self.llm_mode not in {"agency", "copilot", "mock"}:
            problems.append(f"Unknown LLM mode: {self.llm_mode}")
        for command in (self.working_tests, self.targeted, self.hidden_tests):
            try:
                argv = split_command(command)
                if not argv or (
                    argv[0].lower() not in {"python", "python.exe"}
                    and shutil.which(argv[0]) is None
                ):
                    problems.append(f"Command executable not found: {command}")
            except ValueError as error:
                problems.append(f"Invalid command: {error}")
        modules = ["pytest"]
        if self.record.condition != "baseline":
            modules += ["ruff", "mypy", "crosshair", "z3", "verify_and_repair"]
        if self.repo.is_dir():
            probe = run_command(
                [
                    sys.executable,
                    "-c",
                    (
                        "import importlib.util,json; print(json.dumps("
                        f"[m for m in {modules!r} if importlib.util.find_spec(m) is None]))"
                    ),
                ],
                self.repo,
                15,
            )
            if probe.outcome != "pass":
                problems.append(f"Python dependency probe failed: {probe.stderr}")
            else:
                missing = json.loads(probe.stdout)
                if missing:
                    problems.append(
                        f"Missing Python modules in {sys.executable}: {', '.join(missing)}"
                    )
        agency = None
        if self.llm_mode in {"agency", "copilot"}:
            try:
                agency = self.resolve_agent()
                agency_probe = (
                    run_command([agency, "--version"], self.repo, 15)
                    if self.repo.is_dir()
                    else None
                )
                if agency_probe is not None and agency_probe.outcome != "pass":
                    problems.append(
                        f"{self.llm_mode} executable probe failed: {agency_probe.stderr}"
                    )
            except RuntimeError as error:
                problems.append(str(error))
        return {
            "ok": not problems,
            "problems": problems,
            "agency_exe": agency if self.llm_mode == "agency" else None,
            "copilot_exe": agency if self.llm_mode == "copilot" else None,
            "python": sys.executable,
        }

    def elapsed(self):
        return 0.0 if self.start is None else time.perf_counter() - self.start

    def persist(self):
        self.record.wall_seconds = self.elapsed()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(
            json.dumps(self.record.to_dict(), indent=2), encoding="utf-8"
        )

    def remaining(self):
        return max(0.0, self.budget.max_wall_seconds - self.elapsed())

    def verifier_remaining(self):
        return max(
            0.0,
            self.budget.max_verifier_wall_seconds - self.record.verifier_wall_seconds,
        )

    def budget_reason(self, kind="verifier"):
        if self.remaining() <= 0:
            return "wall_time"
        if len(self.record.events) >= self.budget.max_actions:
            return "actions"
        if kind == "verifier" and self.verifier_remaining() <= 0:
            return "verifier_time"
        if kind == "llm":
            if self.record.agent_sessions >= self.budget.session_limit:
                return (
                    "agency_sessions" if self.llm_mode == "agency" else "agent_sessions"
                )
            if not self.record.model_usage_complete:
                return "model_usage_unavailable"
            if self.record.model_requests >= self.budget.max_model_requests:
                return "model_requests"
        return None

    def require_budget(self, kind="verifier"):
        reason = self.budget_reason(kind)
        if reason:
            raise BudgetReached(reason)

    def add_result(
        self, name, result, before, after, started, kind="verifier", group=None
    ):
        metadata = {"command": result.command, "parallel_group": group}
        if name == "smt-z3":
            try:
                metadata["structured_evidence"] = json.loads(
                    result.stdout.strip().splitlines()[-1]
                )
            except (ValueError, IndexError):
                metadata["evidence_parse_error"] = True
        if (
            name == "property-tests"
            and result.exit_code == 5
            or name == "smt-z3"
            and result.exit_code == 2
            and metadata.get("structured_evidence", {}).get("status")
            == "not_applicable"
        ):
            result.outcome = "unknown"
            metadata["not_applicable"] = True
        if (
            name == "symbolic-crosshair"
            and result.exit_code == 0
            and "no checkable functions" in (result.stdout + result.stderr).lower()
        ):
            result.outcome = "unknown"
            metadata["not_applicable"] = True
        event = Event(
            len(self.record.events),
            kind,
            name,
            started,
            result.duration_ms,
            result.outcome,
            result.exit_code,
            before,
            after,
            result.stdout[-TAIL:],
            result.stderr[-TAIL:],
            metadata,
        )
        self.record.events.append(event)
        if kind == "verifier":
            self.latest[name] = event
            self.record.verifier_wall_seconds += result.duration_ms / 1000
        self.persist()
        return event

    def command(self, name, command, timeout=120, kind="verifier"):
        self.require_budget(kind)
        before = revision(self.repo)
        started = utc_now()
        limit = min(timeout, self.remaining())
        if kind == "verifier":
            limit = min(limit, self.verifier_remaining())
        result = run_command(command, self.repo, limit)
        return self.add_result(name, result, before, revision(self.repo), started, kind)

    def tier0(self):
        jobs = [
            (
                "ruff",
                "python -m ruff check . --force-exclude --extend-exclude evaluator",
                30,
            ),
            ("targeted-tests", self.targeted, 90),
            ("mypy", 'python -m mypy --exclude "(^|/)evaluator(/|$)" .', 60),
        ]
        self.require_budget()
        slots = self.budget.max_actions - len(self.record.events)
        if slots < len(jobs):
            raise BudgetReached("actions")
        before, started = revision(self.repo), utc_now()
        # Reserve aggregate verifier time across concurrent jobs, not just wall time.
        limit = min(self.remaining(), self.verifier_remaining() / len(jobs))
        wall = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [
                executor.submit(run_command, cmd, self.repo, min(timeout, limit))
                for _, cmd, timeout in jobs
            ]
            results = [future.result() for future in futures]
        self.record.critical_path_seconds += time.perf_counter() - wall
        after = revision(self.repo)
        return [
            self.add_result(
                name, result, before, after, started, group=f"tier0-{started}"
            )
            for (name, _, _), result in zip(jobs, results)
        ]

    def snapshot(self):
        return failures(list(self.latest.values()), str(self.repo))

    def packet(self):
        current = self.snapshot()
        return json.dumps(
            {
                "task": self.record.task_text,
                "changed_files": changed_files(self.repo),
                "revision": revision(self.repo),
                "diff": diff_summary(self.repo),
                "evidence_delta": evidence_delta(
                    self.previous_repair, current
                ).compact(),
                "instruction": "Make one minimal repair addressing remaining and new visible failures, then stop. "
                "Do not inspect, execute, or modify hidden evaluators or evaluator files. "
                "Do not weaken tests or verification specs. "
                "Make the repair directly; do not delegate to subagents or workflows.",
            },
            indent=2,
        )

    def _read_usage(self, path):
        try:
            usage = json.loads(path.read_text(encoding="utf-8"))
            metrics = usage["modelMetrics"]
            if not isinstance(metrics, dict) or not metrics:
                raise ValueError("modelMetrics must be a nonempty object")
            for value in metrics.values():
                count = value["requests"]["count"]
                if type(count) is not int or count < 0:
                    raise ValueError("Invalid native model request count")
            return usage
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.record.model_usage_complete = False
            return {"usage_error": str(error)}

    def _accumulate_usage(self, usage):
        self.record.premium_request_cost += int(usage.get("totalPremiumRequestCost", 0))
        self.record.nano_aiu += int(usage.get("totalNanoAiu", 0))
        self.record.agency_api_duration_ms += int(usage.get("totalApiDurationMs", 0))
        self.record.prompt_input_tokens += int(
            usage.get("tokenDetails", {}).get("input", {}).get("tokenCount", 0)
        )
        for model, metrics in usage.get("modelMetrics", {}).items():
            count = int(metrics["requests"]["count"])
            self.record.model_requests += count
            self.record.models_used[model] = (
                self.record.models_used.get(model, 0) + count
            )
            for field, key in (
                ("model_input_tokens", "inputTokens"),
                ("output_tokens", "outputTokens"),
                ("cache_read_tokens", "cacheReadTokens"),
                ("cache_write_tokens", "cacheWriteTokens"),
                ("reasoning_tokens", "reasoningTokens"),
            ):
                setattr(
                    self.record,
                    field,
                    getattr(self.record, field)
                    + int(metrics.get("usage", {}).get(key, 0)),
                )
        self.record.model_request_overrun = max(
            0, self.record.model_requests - self.budget.max_model_requests
        )

    def agency_arguments(self, agency_exe, prompt, usage_path):
        return [
            agency_exe,
            "copilot",
            "--agent",
            "verification-agent",
            "--prompt",
            prompt,
            "--no-default-mcps",
            "--silent",
            "--output-format",
            "json",
            "--usage-output-file",
            str(usage_path),
            "--model",
            self.agency_model,
            "--reasoning-effort",
            self.reasoning_effort,
            "--excluded-tools",
            "task",
            "run_dynamic_workflow",
            "--allow-tool",
            "write",
            "--allow-tool",
            "shell(python:*)",
        ]

    def agency_directory(self):
        return self.repo

    def resolve_agent(self):
        return (
            resolve_copilot(self.copilot_exe_override)
            if self.llm_mode == "copilot"
            else self.resolve_agency()
        )

    def repair_capability(self):
        return "tools"

    def agent_arguments(self, executable, prompt, usage_path):
        if self.llm_mode == "copilot":
            return copilot_arguments(
                executable,
                prompt,
                usage_path,
                self.agency_model,
                self.reasoning_effort,
                self.repair_capability(),
            )
        return self.agency_arguments(executable, prompt, usage_path)

    def artifact_directory(self):
        return self.output.parent / (
            "copilot" if self.llm_mode == "copilot" else "agency"
        )

    def repair_metadata(self, result, usage):
        return {}

    def escalate(self):
        self.require_budget()
        self.require_budget("llm")
        if self.budget.max_actions - len(self.record.events) < 2:
            raise BudgetReached("actions")
        current = self.snapshot()
        if current in self.sent_evidence:
            raise RuntimeError(
                "Repeated normalized evidence must not be sent to the coding backend"
            )
        prompt = self.packet()
        input_delta = evidence_delta(self.previous_repair, current)
        self.sent_evidence.add(current)
        self.previous_repair = current
        before, started = revision(self.repo), utc_now()
        self.record.agent_attempts += 1
        self.record.agent_sessions += 1
        if self.llm_mode == "agency":
            self.record.agency_attempts += 1
            self.record.agency_sessions += 1
        elif self.llm_mode == "copilot":
            self.record.copilot_sessions += 1
        self.record.llm_calls = self.record.agent_sessions
        usage = {}
        agency_exe = None
        if self.llm_mode == "mock":
            from .execution import CommandResult

            result = CommandResult([], 0, "unknown", 0, "mock: no patch", "")
        else:
            agency_exe = self.resolve_agent()
            directory = self.artifact_directory()
            directory.mkdir(parents=True, exist_ok=True)
            usage_path = (
                directory
                / f"{self.record.run_id}-call-{self.record.agent_sessions:03d}-usage.json"
            )
            argv = self.agent_arguments(agency_exe, prompt, usage_path)
            if self.llm_mode == "copilot":
                result = run_command(
                    argv,
                    self.agency_directory(),
                    self.remaining(),
                    env={
                        "COPILOT_PLUGIN_DIR_ONLY": "true",
                        "COPILOT_ALLOW_ALL": None,
                        "COPILOT_ASSISTED_APPROVAL": None,
                    },
                )
            else:
                result = run_command(argv, self.agency_directory(), self.remaining())
            usage = self._read_usage(usage_path)
            if result.outcome == "pass":
                self.record.agent_successful_calls += 1
                if self.llm_mode == "agency":
                    self.record.agency_successful_calls += 1
        self._accumulate_usage(usage)
        repair_metadata = self.repair_metadata(result, usage)
        event = Event(
            len(self.record.events),
            "llm",
            "copilot-repair" if self.llm_mode == "copilot" else "agency-repair",
            started,
            result.duration_ms,
            result.outcome,
            result.exit_code,
            before,
            revision(self.repo),
            result.stdout[-TAIL:],
            result.stderr[-TAIL:],
            {
                "backend": self.llm_mode,
                "agent_executable": agency_exe,
                "agency_executable": agency_exe if self.llm_mode == "agency" else None,
                "native_usage": usage,
                "agency_usage": usage,
                "input_evidence_delta": input_delta.compact(),
                **repair_metadata,
            },
        )
        self.record.events.append(event)
        self.record.critical_path_seconds += result.duration_ms / 1000
        self.pending_repair = event
        self.persist()
        return event

    def candidates(self):
        features = task_features(self.record.task_text, changed_files(self.repo))
        return [
            Candidate(
                "smt-z3",
                f'python -m verify_and_repair.z3verify "{self.smt_spec}"',
                20,
                0.85 if features["constraint"] or features["boundary"] else 0.35,
                (self.repo / self.smt_spec).is_file(),
            ),
            Candidate(
                "symbolic-crosshair",
                f'python -m crosshair check "{self.crosshair_target}"',
                45,
                0.65 if features["boundary"] else 0.25,
            ),
            Candidate(
                "property-tests",
                f'{self.working_tests} -k "property or hypothesis"',
                30,
                0.65 if features["parser"] or features["boundary"] else 0.30,
            ),
        ]

    def verify(self):
        strong_names = set(self.latest) - {
            "ruff",
            "targeted-tests",
            "mypy",
            "visible-tests",
        }
        complete = False
        try:
            if self.record.condition == "baseline":
                self.command("visible-tests", self.working_tests, 180)
            else:
                self.tier0()
                for candidate in self.candidates():
                    if candidate.name in strong_names:
                        self.command(
                            candidate.name, candidate.command, candidate.timeout
                        )
            complete = True
        finally:
            self.finish_repair(complete)

    def finish_repair(self, complete):
        if self.pending_repair is not None:
            current = self.snapshot()
            delta = evidence_delta(self.previous_repair, current)
            self.pending_repair.metadata["evidence_delta"] = delta.compact()
            self.pending_repair.metadata["verification_complete"] = complete
            useful = (
                complete
                and bool(delta.resolved)
                and len(current) < len(self.previous_repair)
            )
            self.pending_repair.metadata["useful"] = useful
            if complete:
                self.policy.update(
                    self.pending_repair.name,
                    self.pending_repair.duration_ms / 1000,
                    useful,
                )
            self.pending_repair = None
            self.persist()

    def select(self):
        current = self.snapshot()
        if self.record.condition == "baseline":
            if not current:
                return "done", None
            if self.budget_reason("llm"):
                raise BudgetReached(self.budget_reason("llm"))
            return (
                ("stalled", None) if current in self.sent_evidence else ("repair", None)
            )
        required = {"ruff", "targeted-tests", "mypy"}
        if not required <= self.latest.keys():
            raise BudgetReached("actions")
        candidates = [
            candidate
            for candidate in self.candidates()
            if candidate.applicable and candidate.name not in self.latest
        ]
        if self.record.condition == "fixed":
            if not current and candidates:
                return "verify", candidates[0]
            if not current:
                return "done", None
            if self.budget_reason("llm"):
                raise BudgetReached(self.budget_reason("llm"))
            return (
                ("stalled", None) if current in self.sent_evidence else ("repair", None)
            )
        elif not current:
            confirmed = any(
                event.outcome == "pass"
                for name, event in self.latest.items()
                if name not in required
            )
            if confirmed:
                return "done", None
            if not candidates:
                return "stalled", None
        self.require_budget()
        defaults = {"smt-z3": 10.0, "symbolic-crosshair": 45.0, "property-tests": 20.0}
        limit = min(self.remaining(), self.verifier_remaining())
        choice = self.policy.choose(candidates, defaults, limit)
        repair_ok = (
            bool(current)
            and current not in self.sent_evidence
            and not self.budget_reason("llm")
        )
        repair_name = (
            "copilot-repair" if self.llm_mode == "copilot" else "agency-repair"
        )
        if choice and (
            not repair_ok or choice[0] > self.policy.llm_score(name=repair_name)
        ):
            return "verify", choice[2]
        if repair_ok:
            return "repair", None
        if choice:
            return "verify", choice[2]
        if current and self.budget_reason("llm"):
            raise BudgetReached(self.budget_reason("llm"))
        return "stalled", None

    def run(self):
        preflight = self.preflight()
        self.record.config["preflight"] = preflight
        if not preflight["ok"]:
            self.record.status = "infrastructure_failure"
            self.record.events.append(
                Event(
                    0,
                    "preflight",
                    "preflight",
                    utc_now(),
                    0,
                    "error",
                    stderr_tail="; ".join(preflight["problems"]),
                    metadata=preflight,
                )
            )
            self.record.finished_at = utc_now()
            self.persist()
            return self.record
        self.start = time.perf_counter()
        self.record.started_at = utc_now()
        state = "repair" if self.record.condition == "baseline" else "verify"
        try:
            try:
                while True:
                    if state == "repair":
                        self.escalate()
                        state = "verify"
                    elif state == "verify":
                        self.verify()
                        state = "select"
                    else:
                        action, candidate = self.select()
                        if action in {"done", "stalled"}:
                            self.record.declared_verified = action == "done"
                            self.record.termination_reason = (
                                "visible_obligations_passed"
                                if action == "done"
                                else "no_useful_action"
                            )
                            break
                        if action == "verify":
                            before = self.snapshot()
                            event = self.command(
                                candidate.name, candidate.command, candidate.timeout
                            )
                            useful = (
                                bool(self.snapshot() - before)
                                or event.outcome == "pass"
                            )
                            self.policy.update(
                                candidate.name, event.duration_ms / 1000, useful
                            )
                        else:
                            state = "repair"
            except BudgetReached as error:
                self.record.termination_reason = str(error)
            exhausted = self.record.termination_reason in {
                "wall_time",
                "actions",
                "verifier_time",
                "agency_sessions",
                "agent_sessions",
                "model_requests",
            }
            self.record.status = (
                "verified"
                if self.record.declared_verified
                else ("budget_exhausted" if exhausted else "unresolved")
            )
            # The evaluator runs only after the visible state machine terminates; it never feeds it.
            if not self.defer_hidden_evaluation and not self.budget_reason("evaluator"):
                hidden = self.command(
                    "hidden-evaluation", self.hidden_tests, 240, "evaluator"
                )
                self.record.hidden_correct = hidden.outcome == "pass"
                self.record.false_verification = bool(
                    self.record.declared_verified and not self.record.hidden_correct
                )
                if self.record.hidden_correct:
                    self.record.status = (
                        "solved"
                        if self.record.declared_verified
                        else "correct_but_unverified"
                    )
                elif self.record.declared_verified:
                    self.record.status = "false_verification"
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            self.record.status = "infrastructure_failure"
            self.record.events.append(
                Event(
                    len(self.record.events),
                    "controller",
                    "exception",
                    utc_now(),
                    0,
                    "error",
                    stderr_tail=str(error),
                )
            )
        self.record.finished_at = utc_now()
        self.persist()
        return self.record
