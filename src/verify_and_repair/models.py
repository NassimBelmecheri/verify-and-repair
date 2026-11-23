from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Budget:
    max_wall_seconds: float = 600
    max_llm_calls: int = 3
    max_verifier_wall_seconds: float = 240
    max_actions: int = 24
    target_latency_seconds: float = 120
    max_model_requests: int = 30
    max_agency_sessions: int | None = None
    max_agent_sessions: int | None = None

    @property
    def session_limit(self) -> int:
        if self.max_agent_sessions is not None:
            return self.max_agent_sessions
        return (
            self.max_llm_calls
            if self.max_agency_sessions is None
            else self.max_agency_sessions
        )


@dataclass
class Event:
    index: int
    kind: str
    name: str
    started_at: str
    duration_ms: int
    outcome: str
    exit_code: int | None = None
    revision_before: str | None = None
    revision_after: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    schema_version: str
    run_id: str
    task_id: str
    condition: str
    repo_path: str
    task_text: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    declared_verified: bool = False
    hidden_correct: bool | None = None
    false_verification: bool | None = None
    agency_attempts: int = 0
    agency_successful_calls: int = 0
    agent_attempts: int = 0
    agent_successful_calls: int = 0
    agent_sessions: int = 0
    copilot_sessions: int = 0
    llm_calls: int = 0
    agency_sessions: int = 0
    model_requests: int = 0
    model_request_overrun: int = 0
    model_usage_complete: bool = True
    termination_reason: str | None = None
    premium_request_cost: int = 0
    nano_aiu: int = 0
    agency_api_duration_ms: int = 0
    model_input_tokens: int = 0
    prompt_input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    models_used: dict[str, int] = field(default_factory=dict)
    verifier_wall_seconds: float = 0.0
    critical_path_seconds: float = 0.0
    wall_seconds: float = 0.0
    events: list[Event] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
