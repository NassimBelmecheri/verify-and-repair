from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int | None
    outcome: str
    duration_ms: int
    stdout: str
    stderr: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def split_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    return [
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'"
        else part
        for part in parts
    ]


def run_command(
    command: str | list[str],
    cwd: Path,
    timeout: float,
    *,
    env: dict[str, str | None] | None = None,
    use_current_python: bool = True,
    non_interactive: bool = False,
) -> CommandResult:
    argv = split_command(command) if isinstance(command, str) else command
    if use_current_python and argv and argv[0].lower() in {"python", "python.exe"}:
        argv = [sys.executable, *argv[1:]]
    start = time.perf_counter()
    environment = dict(os.environ)
    if use_current_python:
        environment["PATH"] = (
            str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        )
    for key, value in (env or {}).items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    try:
        p = subprocess.Popen(
            argv,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL if non_interactive else None,
            env=environment,
            shell=False,
        )
        try:
            stdout, stderr = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                p.kill()
            stdout, stderr = p.communicate()
            return CommandResult(
                argv,
                None,
                "timeout",
                int((time.perf_counter() - start) * 1000),
                stdout,
                stderr,
            )
        return CommandResult(
            argv,
            p.returncode,
            "pass" if p.returncode == 0 else "fail",
            int((time.perf_counter() - start) * 1000),
            stdout,
            stderr,
        )
    except OSError as e:
        return CommandResult(
            argv, None, "error", int((time.perf_counter() - start) * 1000), "", str(e)
        )


def git_text(repo: Path, *args: str) -> str:
    try:
        p = subprocess.run(
            ["git", *args],
            check=False,
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            shell=False,
        )
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def revision(repo: Path) -> str:
    head = git_text(repo, "rev-parse", "HEAD") or "no-git-head"
    diff = git_text(repo, "diff", "--binary", "HEAD")
    return (
        f"{head}+diff:{hashlib.sha256(diff.encode(errors='replace')).hexdigest()[:16]}"
    )


def changed_files(repo: Path) -> list[str]:
    return [x for x in git_text(repo, "diff", "--name-only", "HEAD").splitlines() if x]


def diff_summary(repo: Path) -> str:
    return git_text(repo, "diff", "--stat", "HEAD")
