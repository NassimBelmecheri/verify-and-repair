# Copilot CLI and Agency child-controller setup

This mode launches a bounded source-first repair controller. It supports
**existing Python implementation files in a Git repository**. For same-agent
Codex, Claude Code, Cursor or generic MCP repair, use [the harness guide](harnesses.md).

Run [Windows setup](../README.md#install) first. No environment activation is
needed for the controller. Install the target project's dependencies separately
and use its explicit interpreter in the check command.

## Copilot CLI

```powershell
winget install GitHub.Copilot
$CopilotExe = (Get-Command copilot.exe -CommandType Application).Source
& $CopilotExe login
```

Reopen your terminal after installation if discovery fails. Use the native
executable, not an npm `.cmd` or `.ps1` wrapper. For headless sign-in use
`login --device-code`. Keep credentials in the CLI's authentication store.

From the controller directory:

```powershell
$Repo = "C:\work\project"
.\verify-and-repair.ps1 configure --backend copilot --copilot-exe $CopilotExe `
  --repo $Repo --implementation-file src\logic.py `
  --context-file tests\test_logic.py `
  --working-tests '"C:\work\project\.venv\Scripts\python.exe" -m pytest -q tests'

.\verify-and-repair.ps1 solve --config "$Repo\verify-and-repair.json" `
  --task "Repair the generated code using the failing public examples"
```

`configure` does not call a model. **`solve` spends model quota and edits
allowlisted implementation files.** It reruns public checks and sends compact
counterexample deltas on subsequent repairs.

## Agency

Install Agency through [your account's Agency distribution](https://aka.ms/agency)
and follow its authentication instructions. A standard per-user executable is:

```powershell
$AgencyExe = Join-Path $env:APPDATA "agency\CurrentVersion\agency.exe"
& $AgencyExe --version
& $AgencyExe copilot login
```

Use your actual installation path if different. If Agency's login wrapper
fails, authenticate its managed native Copilot executable according to your
installation's instructions rather than editing credential files.

Configure the same task with `--backend agency --agency-exe $AgencyExe`
instead of the Copilot options above. Agency manages its Copilot invocation;
this repository does not grant backend or model access.

## Reuse, configuration and budgets

`configure` refuses existing files. Reuse the saved config for new task text,
or select another `--config` path. `solve --backend agency --agency-exe <path>`
and `solve --backend copilot --copilot-exe <path>` override a backend for one
run without changing the saved config.

Example task JSON:

```json
{
  "repo": "C:\\work\\project",
  "implementation_files": ["src/logic.py"],
  "context_files": ["tests/test_logic.py"],
  "working_tests": "\"C:\\work\\project\\.venv\\Scripts\\python.exe\" -m pytest -q tests",
  "backend": "copilot",
  "model": "gpt-6.1-sol",
  "reasoning_effort": "medium",
  "budget": {
    "max_agent_sessions": 3,
    "max_model_requests": 30,
    "max_wall_seconds": 600,
    "max_verifier_wall_seconds": 180
  }
}
```

Optional fields include executable paths, `lint_command`, `typecheck_command`,
`lean_context_bytes`, `contract_manifest` and `symbolic_seconds`.
Scoped contracts are optional; unknown is not a proof.

Defaults are Agency, `gpt-6.1-sol`, medium reasoning, 600s wall, three sessions,
30 session-boundary requests and 180s verifier time. Native
`modelMetrics[*].requests.count` counts requests separately from launches.
Request limits are checked between sessions, not within one; overshoot is
reported. Missing native usage stops further inference.

JSON results are saved in `verify-and-repair-results` beside the task.
Explicit output paths must be outside it. Public checks passing is not hidden
correctness, and no private evaluator runs. Source responses must have native
zero-tool evidence; protected-file tool edits are detected, not silently reverted.
These controls are not an OS sandbox or a hard real-time guarantee.

## Interactive use and VS Code

An ordinary conversation does not automatically invoke the controller. Run its
launcher through your terminal, or at the Copilot/Agency prompt:

```text
!C:\tools\verify-and-repair\verify-and-repair.ps1 solve --config C:\work\project\verify-and-repair.json --task "Fix the failing example"
```

For VS Code, copy [.github\agents\verify-and-repair.agent.md](../.github/agents/verify-and-repair.agent.md)
into the target workspace and select **Verify and Repair**. Bind its actual
launcher/config paths. The agent generates normally, then repairs failures
using counterexamples within a task's once-approved scope and budget.
This is selected-agent routing, not a global hook. Outer chat usage is separate
from child-controller accounting.

The [installation skill](../.github/skills/install-verify-and-repair/SKILL.md)
can perform the setup with approval and without launching a paid repair.
The bundled `examples\verify-and-repair-demo\run.ps1` is a **paid** disposable
demonstration; select `-Backend copilot` or `-Backend agency`.
