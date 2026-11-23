from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .contracts import contained_file
from .execution import run_command


def workspace_state(repo: Path) -> dict[str, str]:
    result = run_command(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        repo,
        10,
    )
    if result.outcome != "pass":
        raise ValueError(f"Cannot fingerprint workspace: {result.stderr}")
    state = {}
    for name in sorted(set(filter(None, result.stdout.split("\0")))):
        path = contained_file(repo, name)
        state[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def response_message(
    stdout: str, *, expected_requests: int | None = None
) -> tuple[str, dict[str, Any]]:
    messages = []
    records = []
    session_ids = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError as error:
            raise ValueError("Expected native coding-backend JSONL output") from error
        if not isinstance(event, dict) or not isinstance(event.get("data", {}), dict):
            raise TypeError("Malformed native coding-backend event")
        kind = event.get("type")
        if kind in {"tool.execution_start", "subagent.started"}:
            raise ValueError("Patch-only generation attempted tool use or delegation")
        if kind == "session.start":
            session_ids.add(str(uuid.UUID(event["data"]["sessionId"])))
        if kind == "session.usage_record":
            usage = event.get("data", {}).get("usage")
            if not isinstance(usage, dict):
                raise TypeError("Malformed native usage record")
            records.append(usage)
        if kind == "assistant.message" and not event.get("agentId"):
            content = event.get("data", {}).get("content")
            if isinstance(content, str) and content.strip():
                messages.append(content.strip())
    evidence_file = None
    if not records and len(session_ids) == 1:
        # Streams may omit usage; the durable native session log retains it.
        evidence_file = (
            Path(os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot")))
            / "session-state"
            / session_ids.pop()
            / "events.jsonl"
        )
        with evidence_file.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("type") in {"tool.execution_start", "subagent.started"}:
                    raise ValueError(
                        "Patch-only durable session attempted tool use or delegation"
                    )
                if event.get("type") == "session.usage_record":
                    records.append(event["data"]["usage"])
    distinct = {}
    for index, usage in enumerate(records):
        key = (
            usage.get("accounting", {}).get("usageId")
            or usage.get("apiCallId")
            or index
        )
        distinct[key] = usage
    records = list(distinct.values())
    if not records or any(record.get("availableToolCount") != 0 for record in records):
        raise ValueError("Native usage did not confirm zero exposed tools")
    if expected_requests is not None and len(records) != expected_requests:
        raise ValueError("Native tool evidence does not cover every model request")
    if not messages:
        raise ValueError("No final source response")
    content = messages[-1]
    if len(content.encode("utf-8")) > 100_000:
        raise ValueError("Response exceeds 100 KB")
    return content, {
        "available_tool_count": 0,
        "generation_messages": len(messages),
        "native_tool_evidence_file": str(evidence_file) if evidence_file else None,
        "native_tool_evidence": [
            {
                "model": record.get("model"),
                "available_tool_count": record["availableToolCount"],
                "tool_calls": record.get("numToolCalls"),
            }
            for record in records
        ],
    }


def response_patch(
    stdout: str, *, expected_requests: int | None = None
) -> tuple[str, dict[str, Any]]:
    patch, metadata = response_message(stdout, expected_requests=expected_requests)
    if patch.startswith("```diff\n") and patch.endswith("\n```"):
        patch = patch[len("```diff\n") : -len("\n```")]
    return patch + "\n", metadata


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def apply_sources(
    repo: Path,
    response: str,
    editable: tuple[str, ...],
    expected: dict[str, str],
    scratch: Path,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    start = time.perf_counter()
    deadline = start + timeout
    if timeout <= 0:
        raise ValueError("Patch validation time budget exhausted")
    if len(response.encode("utf-8")) > 100_000:
        raise ValueError("Response exceeds 100 KB")
    if response.startswith("```json\n") and response.endswith("\n```"):
        response = response[len("```json\n") : -len("\n```")]
    payload = json.loads(response, object_pairs_hook=_unique_json_object)
    if not isinstance(payload, dict) or set(payload) != {"files"}:
        raise ValueError("Source response must contain only a files array")
    entries = payload["files"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Source response files must be a nonempty array")
    replacements: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "content"}:
            raise ValueError("Each source replacement needs only path and content")
        if not isinstance(entry["path"], str) or not isinstance(entry["content"], str):
            raise TypeError("Replacement path and content must be strings")
        name = entry["path"].replace("\\", "/")
        if name not in editable or name in replacements:
            raise ValueError("Replacement path must be unique and explicitly editable")
        replacements[name] = entry["content"]
    if workspace_state(repo) != expected:
        raise ValueError("Workspace changed while the source was being generated")
    scratch.mkdir(parents=True, exist_ok=True)
    patches = []
    with tempfile.TemporaryDirectory(prefix="validate-", dir=scratch) as directory:
        staging = Path(directory)
        for name, content in replacements.items():
            original = contained_file(repo, name)
            before = original.read_bytes().decode("utf-8")
            after = content.replace("\r\n", "\n").replace("\r", "\n")
            newline = "\r\n" if b"\r\n" in original.read_bytes() else "\n"
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            after = after.replace("\n", newline)
            destination.write_bytes(after.encode("utf-8"))
            for line in difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            ):
                patches.append(
                    line
                    if line.endswith("\n")
                    else line + "\n\\ No newline at end of file\n"
                )
        _commit_staged(repo, set(replacements), staging, expected, deadline)
    patch = "".join(patches)
    return patch, {
        "patch_applied": True,
        "files": sorted(replacements),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "patch_apply_ms": int((time.perf_counter() - start) * 1000),
        "response_format": "source-files-v1",
    }


def _commit_staged(
    repo: Path,
    paths: set[str],
    staging: Path,
    expected: dict[str, str],
    deadline: float,
) -> None:
    contents = {}
    for name in sorted(paths):
        source = staging / name
        compile(source.read_text(encoding="utf-8"), name, "exec")
        contents[name] = source.read_bytes()
    if workspace_state(repo) != expected:
        raise ValueError("Workspace changed during patch validation")
    if all(
        hashlib.sha256(data).hexdigest() == expected[name]
        for name, data in contents.items()
    ):
        raise ValueError("Patch makes no implementation change")
    if time.perf_counter() >= deadline:
        raise ValueError("Patch validation time budget exhausted")
    for name, data in contents.items():
        contained_file(repo, name).write_bytes(data)


def patch_paths(patch: str, editable: tuple[str, ...]) -> set[str]:
    paths = set()
    old = None
    for line in patch.splitlines():
        if line.startswith(
            (
                "new file mode",
                "deleted file mode",
                "old mode",
                "new mode",
                "rename ",
                "copy ",
                "GIT binary patch",
                "Binary files",
            )
        ):
            raise ValueError(
                "File creation, deletion, mode changes and renames are forbidden"
            )
        if line.startswith("--- "):
            if old is not None:
                raise ValueError("Unpaired patch file header")
            old = line[4:].split("\t")[0]
            if not old.startswith("a/"):
                raise ValueError("Patch old paths must use the a/ prefix")
        elif line.startswith("+++ "):
            new = line[4:].split("\t")[0]
            if old is None or new != "b/" + old[2:] or new[2:] not in editable:
                raise ValueError(
                    "Patch must update the same explicitly editable existing file"
                )
            paths.add(new[2:])
            old = None
        elif line.startswith("diff --git ") and not any(
            line == f"diff --git a/{name} b/{name}" for name in editable
        ):
            raise ValueError("Patch metadata references a forbidden path")
    if old is not None or not paths:
        raise ValueError("No valid unified diff file headers")
    return paths


def apply_patch(
    repo: Path,
    patch: str,
    editable: tuple[str, ...],
    expected: dict[str, str],
    scratch: Path,
    timeout: float,
) -> dict[str, Any]:
    start = time.perf_counter()
    paths = patch_paths(patch, editable)
    if workspace_state(repo) != expected:
        raise ValueError("Workspace changed while the patch was being generated")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="validate-", dir=scratch) as directory:
        staging = Path(directory)
        for name in paths:
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(contained_file(repo, name), destination)
        candidate = staging / "candidate.patch"
        for name in sorted(paths):
            newline = "\r\n" if b"\r\n" in (staging / name).read_bytes() else "\n"
            candidate.write_bytes(
                patch.replace("\r\n", "\n").replace("\n", newline).encode()
            )
            for options in (["--check"], []):
                left = timeout - (time.perf_counter() - start)
                if left <= 0:
                    raise ValueError("Patch validation time budget exhausted")
                result = run_command(
                    [
                        "git",
                        "-c",
                        "core.autocrlf=false",
                        "apply",
                        "--recount",
                        *options,
                        f"--include={name}",
                        str(candidate),
                    ],
                    staging,
                    left,
                )
                if result.outcome != "pass":
                    raise ValueError(f"Patch rejected: {result.stderr.strip()}")
        _commit_staged(repo, paths, staging, expected, start + timeout)
    return {
        "patch_applied": True,
        "files": sorted(paths),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "patch_apply_ms": int((time.perf_counter() - start) * 1000),
    }
