from __future__ import annotations

import shutil
from pathlib import Path

BACKENDS = ("agency", "copilot")
COPILOT_REPAIR_TOOLS = (
    "view",
    "rg",
    "glob",
    "apply_patch",
    "powershell",
    "bash",
    "read_powershell",
    "stop_powershell",
)


def resolve_copilot(override: str | None = None) -> str:
    found: str | None
    if override:
        path = Path(override)
        if path.is_file():
            found = str(path)
        else:
            raise RuntimeError(f"--copilot-exe does not exist: {path}")
    else:
        found = shutil.which("copilot.exe") or shutil.which("copilot")
    if not found:
        raise RuntimeError("Copilot CLI could not be located. Use --copilot-exe.")
    if Path(found).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise RuntimeError(
            "Use the native Copilot executable, not a shell wrapper "
            "(on Windows install GitHub.Copilot with winget)."
        )
    return found


def copilot_arguments(
    executable: str,
    prompt: str,
    usage_path: Path,
    model: str,
    reasoning_effort: str,
    capability: str,
) -> list[str]:
    args = [
        executable,
        "--no-auto-update",
        "--no-custom-instructions",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-bash-env",
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
        model,
        "--reasoning-effort",
        reasoning_effort,
    ]
    if capability == "source":
        # A nonempty allowlist avoids empty-list interpretation differences.
        args += [
            "--available-tools",
            "task_complete",
            "--excluded-tools",
            "task_complete",
        ]
    else:
        args += [
            "--available-tools",
            *COPILOT_REPAIR_TOOLS,
            "--allow-tool",
            "write",
            "--allow-tool",
            "shell(python:*)",
        ]
    return args
