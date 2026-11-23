# Verify and Repair

A repair controller for **Agency** and **GitHub Copilot CLI**: source-first
code generation, deterministic public checks, and repository tools when needed.
It coordinates existing coding CLIs; it is not a separate coding agent.

Use it from PowerShell or [from inside a Copilot CLI or Agency
session](#use-from-inside-copilot-cli-or-agency).

**Evaluation-only, source-available; not open-source.** Viewing and running
unmodified copies for personal, non-commercial evaluation are permitted.
Commercial/organizational use, production use, code modification and
redistribution require prior written permission. See [LICENSE](LICENSE),
including its configuration exception and GitHub-platform rights.

## Results on MAI-CODE-2

**Lean used 70.0% less native AIU and 42.7% less summed coding time than
tools-first**, with **164 vs. 641 model requests**, on the same 164 HumanEval
tasks using **MAI-CODE-2, medium reasoning, through Agency**.

| Approach | HumanEval | HumanEval+ | Model requests | Native AIU | Coding seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Tools-first reference | 163/164 | **156/164** | 641 | 182.67248 | 5456.04 |
| **Lean (this implementation)** | **164/164** | 155/164 | **164** | **54.74155** | **3128.27** |

Lean used less model quota and coding time, but solved one fewer HumanEval+ task (155 vs. 156).

## Install on Windows

### 1. Install prerequisites

You need **Python 3.11+**, **Git**, and access to either coding CLI. Check:

```powershell
python --version
git --version
```

Only one backend is required. Install and authenticate it using the
[Copilot CLI setup](#github-copilot-cli-setup) or
[Agency setup](#agency-setup) below. You need access to the selected model
and available usage quota.

### 2. Clone and set up

```powershell
git clone https://github.com/NassimBelmecheri/verify-and-repair.git
Set-Location .\verify-and-repair
.\setup.ps1
.\verify-and-repair.ps1 --help
```

Setup creates `.venv` and installs the package, pytest, Ruff, mypy, CrossHair
and Z3. It does not install or authenticate the coding CLI. No environment
activation is needed. To select a specific Python executable, use
`.\setup.ps1 -Python C:\path\to\Python311\python.exe`.

If PowerShell blocks scripts, enable them for **this process only** and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
```

The setup and launcher workflow is validated on Windows, not other platforms.

## Connect Copilot CLI or Agency

**This is a controller around the CLI, not a Copilot/Agency plugin.** There is
no extension to install, MCP server to register, or system prompt to paste into
an interactive session. Start repairs with `verify-and-repair.ps1`, not with a direct coding
prompt to `copilot` or `agency copilot`:

```text
verify-and-repair.ps1 solve -> lean controller -> Copilot CLI OR Agency
                  |                    |
                  +-- public checks <--+-- proposed repair
```

The controller supplies the lean prompt, restricts the initial request to
source generation, applies validated source, runs public checks, and decides
whether to request another repair or enable tools. An ordinary interactive
CLI conversation does not automatically use this loop; invoke the controller
from that session as shown below.

Run the following commands from the cloned `verify-and-repair`
directory, after `setup.ps1`. Replace `C:\work\project` with a **disposable
evaluation Git repository** containing `src\logic.py` and
`tests\test_logic.py`; adjust both paths and the public test command as needed.
Install that task's dependencies in the Python environment used by the test
command. `configure` creates a JSON file without invoking a model;
**`solve` uses paid model quota and can change allowlisted implementation files.**

### GitHub Copilot CLI setup

Install the native Windows CLI, then reopen PowerShell if it is not on PATH:

```powershell
winget install GitHub.Copilot
```

Locate the executable and sign in with an account that has Copilot access:

```powershell
$CopilotExe = (Get-Command copilot.exe -CommandType Application -ErrorAction Stop).Source
& $CopilotExe --version
& $CopilotExe login
```

If a browser cannot open, use `& $CopilotExe login --device-code`.
The integration needs the native `.exe`, not an npm `.cmd` or `.ps1` wrapper.
If discovery fails, set `$CopilotExe` to the full path of the installed
`copilot.exe`. Keep credentials in the CLI's authentication store, not in
the task configuration.

Save a Copilot-backed task configuration, then run the lean loop:

```powershell
$Repo = "C:\work\project"
.\verify-and-repair.ps1 configure --backend copilot --copilot-exe $CopilotExe `
  --repo $Repo --config "$Repo\verify-and-repair-copilot.json" `
  --implementation-file src\logic.py --context-file tests\test_logic.py `
  --working-tests "python -m pytest -q tests" --model gpt-6.1-sol

.\verify-and-repair.ps1 solve --config "$Repo\verify-and-repair-copilot.json" `
  --task "Fix the failing boundary case"
```

The saved `backend` is `copilot`; repairs invoke that executable directly.
`--model` selects the model for this controller, independently of your
interactive CLI default. Replace `gpt-6.1-sol` with an accessible model if
necessary. The generated configuration uses medium reasoning.

### Agency setup

Obtain Agency using the installation instructions available to your account
at [the Agency site](https://aka.ms/agency). Agency is distributed separately;
this repository does not install it or grant access. Follow any required
organization-specific authentication steps.

If `agency.exe` is on PATH, locate it with:

```powershell
$AgencyExe = (Get-Command agency.exe -CommandType Application -ErrorAction Stop).Source
```

Otherwise, use its full installed path. For a standard per-user installation:

```powershell
$AgencyExe = Join-Path $env:APPDATA "agency\CurrentVersion\agency.exe"
```

Check the executable and authenticate its Copilot command:

```powershell
& $AgencyExe --version
& $AgencyExe copilot login
# If needed instead: & $AgencyExe copilot login --device-code
```

Save an Agency-backed configuration and start the same lean loop:

```powershell
$Repo = "C:\work\project"
.\verify-and-repair.ps1 configure --backend agency --agency-exe $AgencyExe `
  --repo $Repo --config "$Repo\verify-and-repair-agency.json" `
  --implementation-file src\logic.py --context-file tests\test_logic.py `
  --working-tests "python -m pytest -q tests" --model gpt-6.1-sol

.\verify-and-repair.ps1 solve --config "$Repo\verify-and-repair-agency.json" `
  --task "Fix the failing boundary case"
```

The saved `backend` is `agency`; repairs invoke `agency.exe copilot`.
Use a model available through your Agency account. No separate Copilot
installation is required by this controller when using Agency; Agency manages
its own Copilot invocation.

### Reuse or switch a backend

Configure once, then rerun `solve` with new task text. `configure` refuses to
overwrite an existing file: edit its documented JSON fields, or choose another
`--config` path. To switch a saved Copilot configuration to Agency for one run:

```powershell
.\verify-and-repair.ps1 solve --config "$Repo\verify-and-repair-copilot.json" `
  --backend agency --agency-exe $AgencyExe --task "Fix the failing boundary case"
```

Use `--backend copilot --copilot-exe $CopilotExe` for the reverse switch.
Overrides do not rewrite the saved configuration. Normal managed policies
and hooks still apply; the controller does not bypass account restrictions.
Results report the selected backend, sessions, native model requests and
public-check outcome. Public success is not a hidden-correctness guarantee.
The default JSON output goes into `verify-and-repair-results` beside the task
repository, not inside it. If you supply `--output`, choose a path outside the
task repository; preflight rejects in-repository artifacts.

### Use from inside Copilot CLI or Agency

After setup and configuration above, you can invoke the controller without
leaving your interactive coding CLI.

**From Copilot CLI:** start it in the cloned controller directory:

```powershell
Set-Location C:\path\to\verify-and-repair
copilot
```

At the **Copilot prompt**, enter this single line. The leading `!` runs a
PowerShell command rather than asking the outer agent to implement the fix:

```text
!.\verify-and-repair.ps1 solve --config C:\work\project\verify-and-repair-copilot.json --task "Fix the failing boundary case"
```

**From Agency:** start its Copilot interface in the same directory:

```powershell
Set-Location C:\path\to\verify-and-repair
agency copilot
```

At the **Agency Copilot prompt**, run:

```text
!.\verify-and-repair.ps1 solve --config C:\work\project\verify-and-repair-agency.json --task "Fix the failing boundary case"
```

If either executable is not on PATH, launch it with the `$CopilotExe` or
`$AgencyExe` path from setup. Use actual paths, not `C:\path\to` placeholders.
The config chooses the repair backend; the interactive session is only the
launcher. Both examples run the same lean controller and its public checks.
`!` avoids an extra outer-agent reasoning step, but the controller's repair
sessions still consume model quota. Command output is added to the outer
conversation; use public evaluation tasks only.

You can instead ask the interactive agent:

```text
Run .\verify-and-repair.ps1 solve --config C:\work\project\verify-and-repair-copilot.json
with --task "Fix the failing boundary case". Use the controller to make the
repair; do not edit the implementation or tests yourself. Report its result.
```

For Agency, substitute `verify-and-repair-agency.json`. This natural-language
route can add outer-agent requests, which are not included in the child
controller's usage record. Normal folder trust, command approval and host
policy still apply. If the host blocks child CLI execution, run the same
`verify-and-repair.ps1` command in a separate PowerShell terminal. No global instruction file
or plugin is required, and unrelated CLI conversations are unchanged.

### Install the integration with a skill

In Copilot CLI, install the [setup skill](.github/skills/install-verify-and-repair/SKILL.md):

```powershell
copilot skill add https://raw.githubusercontent.com/NassimBelmecheri/verify-and-repair/main/.github/skills/install-verify-and-repair/SKILL.md
```

Then invoke it in chat:

```text
/install-verify-and-repair
```

The skill asks for your installation directory, task repository, editable
Python files, public test command and backend. With your approval, it installs
the controller, saves the task config and copies the optional VS Code agent
into your workspace with the local paths filled in. It preserves existing
files and **does not run a paid repair**. CLI installation/authentication stays
under your control. The outer setup conversation can still consume model quota.

The skill is manually invoked; it does not install global always-run rules.
After setup, select **Verify and Repair** in VS Code. Questions stay normal;
explicit code-change requests use the configured controller with quota approval.
You can also follow the manual steps below.

### Use in VS Code: questions stay normal, code requests use the controller

The optional [Verify and Repair custom agent](.github/agents/verify-and-repair.agent.md)
routes **explicit requests to create or change code** to the controller.
Questions, explanations, reviews, plans and example snippets in chat do not
start a repair. Keep this agent selected to use that split; other agents are
unchanged.

1. Complete setup and create a task configuration as above.
2. Copy `.github\agents\verify-and-repair.agent.md` from this repository into
   your task workspace's `.github\agents` directory. Do this before starting a
   repair so the new file is part of the controller's initial workspace state.
3. In VS Code Copilot Chat, select **Verify and Repair** from the agent selector.
   If it is not listed, use **Chat: Open Customizations** to inspect workspace
   agents. Follow the [VS Code custom-agent guide](https://code.visualstudio.com/docs/agent-customization/custom-agents)
   for your installed version and selected harness.
4. Tell it the absolute paths to the installed `verify-and-repair.ps1` and your
   task JSON. Authorize the model-quota use when you request a repair.

For example:

```text
Controller: C:\tools\verify-and-repair\verify-and-repair.ps1
Config: C:\work\project\verify-and-repair.json

When I ask a question, answer it without running repairs.
When I explicitly ask for an implementation change, use this controller.
Ask before using paid model quota.
```

Then “Explain why this assertion fails” is a normal question, while “Fix this
boundary case using the controller” starts the configured repair workflow.
There is **no need to activate it for every question**.

This is instruction-based routing, **not an enforced code-generation or
file-save hook**. A custom agent does not guarantee that every generated edit
passes through the controller. It applies only when selected, and normal
folder trust, terminal permissions and managed policies still apply.
The controller currently changes existing Python files only; new projects,
new files and other languages require a different workflow.

VS Code remains the outer conversation. The selected CLI performs the inner
repair; outer chat requests are not included in the controller's usage totals.
Do not install an always-run global instruction or post-edit hook: it can
trigger unnecessary paid calls or recursively invoke repairs.

### Try the bundled evaluation example

**This uses paid model quota.** Choose one backend:

```powershell
.\examples\verify-and-repair-demo\run.ps1 -Backend copilot
# Or:
.\examples\verify-and-repair-demo\run.ps1 -Backend agency
```

The script repairs a fresh temporary copy, displays the change and rechecks
three public examples. Expected output: `Three public examples passed`.
It prints the workspace and JSON evidence paths. It allows one model session.
The configured example model is `mai-code-2`; if unavailable on your account,
select a supported model in a local copy of its documented JSON configuration.

For evaluation, use your own disposable task repository: the allowlisted
implementation files can be changed. Do not supply sensitive context or private
evaluators. Use beyond personal evaluation requires prior written permission.

## How lean works

1. Run existing public checks and collect normalized failures.
2. Supply only the allowlisted implementation, read-only context and evidence
   delta to a zero-tool model request.
3. Validate complete-source JSON, paths, syntax and workspace fingerprints;
   apply the edit deterministically and rerun checks.
4. Finish if public obligations pass. Otherwise send resolved/remaining/new
   evidence when it changes. Escalate once to repository tools if context is too
   large, source is rejected, exploration is requested or evidence is unchanged.
5. Stop on success, repeated evidence for the same capability, infrastructure
   errors or exhausted budgets. Never feed private grading into repair.

Example: `return x - 1` fails `assert inc(0) == 1`. The model returns
`{"files":[{"path":"src/logic.py","content":"def inc(x):\n    return x + 1\n"}]}`.
The controller checks and applies this data, then runs the actual public checks.
The model does not need to choose file-reading, editing or test-running tools.

For two independent defects, a first repair may resolve one test while leaving
another: the next request contains that distinction instead of another full
diagnostic transcript. If the same failures persist, the controller does not
repeat the same source request: it can switch to tools once, within the same budgets.

## Configuration and Python API

```json
{
  "repo": "C:\\work\\project",
  "implementation_files": ["src/logic.py"],
  "context_files": ["tests/test_logic.py"],
  "working_tests": "python -m pytest -q tests",
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

```python
from pathlib import Path
from verify_and_repair.api import TaskConfig, solve

record = solve(
    TaskConfig.load(Path("verify-and-repair.json")),
    "Fix the failing boundary case",
    Path(r"C:\tmp\repair.json"),
)
```

Optional fields: `agency_exe`, `copilot_exe`, `lint_command`,
`typecheck_command`, `lean_context_bytes` (default 16000 encoded context bytes),
`contract_manifest` and `symbolic_seconds`. Optional contracts are checked after
cheap public checks pass; unknown is not a proof.

Defaults: Agency, `gpt-6.1-sol`, medium reasoning, 600s wall, three sessions,
30 session-boundary requests and 180s verifier time.

## Accounting and boundaries

`agent_sessions` counts launches; `agency_sessions` and `copilot_sessions` count
their actual backends. `model_requests` sums native
`modelMetrics[*].requests.count`; one session can contain many requests.
Set `budget.max_agent_sessions` to limit launches.
Missing/malformed usage blocks further inference.

Request limits apply **between sessions**, not inside them: a session may
overshoot, recorded as `model_request_overrun`. Preflight is outside experimental
timing. Wall time bounds subprocess work but is not a hard real-time guarantee
for process cleanup and controller/filesystem overhead.

`solve` runs no private evaluator. Passing public checks does **not** establish
hidden correctness. Supply only public commands/context; arbitrary commands
cannot be classified automatically. Source responses require native evidence
of zero exposed tools. Tool edits to protected eligible files are detected
after execution and reported, not silently reverted. These are controller
integrity checks, not an OS sandbox; managed policy and hooks still apply.

## Paper

Read [the paper (PDF)](docs/verify-and-repair-paper.pdf). It includes simple
notation, algorithm2e algorithms, diagrams, worked examples, the concrete execution
and limitations.

## Development

Install development dependencies and run the runtime tests without invoking
paid models:

```powershell
.\setup.ps1 -Dev
.\.venv\Scripts\python.exe -m pytest -q
```
