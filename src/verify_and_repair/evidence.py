from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .models import Event


def normalize(text: str, repo: str = "") -> str:
    if repo:
        text = re.sub(re.escape(repo), "<repo>", text, flags=re.IGNORECASE)
        text = text.replace(repo.replace("\\", "/"), "<repo>")
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|seconds?|s)\b", "<time>", text)
    text = re.sub(r"0x[0-9a-fA-F]+", "<address>", text)
    text = re.sub(r"(?<=\.py):\d+(?::\d+)?", ":<line>", text)
    return " ".join(text.split())


@dataclass(frozen=True, order=True)
class Failure:
    verifier: str
    outcome: str
    detail: str

    def compact(self) -> dict[str, str]:
        return {
            "verifier": self.verifier,
            "outcome": self.outcome,
            "detail": self.detail[:1600],
        }


Snapshot = frozenset[Failure]


def failures(events: list[Event], repo: str = "") -> Snapshot:
    result: set[Failure] = set()
    for event in events:
        if (
            event.kind != "verifier"
            or event.outcome == "pass"
            or event.metadata.get("not_applicable")
        ):
            continue
        structured = event.metadata.get("structured_evidence")
        if structured is not None:
            result.add(
                Failure(
                    event.name,
                    event.outcome,
                    json.dumps(structured, sort_keys=True, separators=(",", ":")),
                )
            )
            continue
        text = event.stdout_tail + "\n" + event.stderr_tail
        pytest_failures = re.findall(r"^(?:FAILED|ERROR) .+$", text, re.MULTILINE)
        type_errors = re.findall(r"^.+:\d+(?::\d+)?: error: .+$", text, re.MULTILINE)
        lint_errors = re.findall(
            r"^(?:.+:\d+:\d+: [A-Z]+\d+ .+|[A-Z]+\d+ (?:\[\*\] )?.+(?:\n\s*--> .+)?)$",
            text,
            re.MULTILINE,
        )
        diagnostics = pytest_failures or type_errors or lint_errors
        if diagnostics:
            result.update(
                Failure(event.name, event.outcome, normalize(detail, repo))
                for detail in diagnostics
            )
            continue
        lines = []
        for line in text.splitlines():
            line = normalize(line, repo)
            if not line or re.fullmatch(r"[.FE sxX]+(?: \[\d+%\])?", line):
                continue
            if line.startswith(
                ("===", "collected ", "platform ", "rootdir:", "cachedir:", "plugins:")
            ):
                continue
            lines.append(line)
        detail = "\n".join(sorted(set(lines))) or f"exit_code={event.exit_code}"
        result.add(Failure(event.name, event.outcome, detail))
    return frozenset(result)


@dataclass(frozen=True)
class EvidenceDelta:
    resolved: Snapshot
    remaining: Snapshot
    new: Snapshot

    def compact(self) -> dict[str, list[dict[str, str]]]:
        return {
            name: [failure.compact() for failure in sorted(getattr(self, name))]
            for name in ("resolved", "remaining", "new")
        }


def evidence_delta(before: Snapshot, after: Snapshot) -> EvidenceDelta:
    return EvidenceDelta(before - after, before & after, after - before)
