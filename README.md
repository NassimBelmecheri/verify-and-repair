# Verify and Repair

**Generate code normally. Verify it. Repair from counterexamples.**

Use your existing agent in **Codex, Claude Code, Cursor, or any local stdio MCP
harness**. It receives failing assertions and compact evidence deltas, then
repairs with **its own model**. No Copilot or Agency account is needed for this
mode. The existing **Copilot CLI and Agency** repair controller remains available.

Questions stay normal. For an authorized implementation task, failed checks
trigger repair within its agreed budget, not another approval for every attempt.

## Install

Windows, Python 3.11+ and Git:

```powershell
git clone https://github.com/NassimBelmecheri/verify-and-repair.git
Set-Location .\verify-and-repair
.\setup.ps1
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
```

The last command adds optional MCP support. Authenticate your chosen agent
through its own setup; this project does not install or sign in to it.

## Connect your agent

[Follow the harness guide](docs/harnesses.md) for Codex, Claude Code, Cursor
and generic MCP configuration.

```text
Your agent generates code
        -> verify_code returns counterexamples
        -> the same agent repairs
        -> verify again until pass, unchanged evidence, or budget exhaustion
```

The server exposes `begin_repair_task` and `verify_code`. It runs configured
public tests, lint or typechecks, protects declared test/config files and
persists the loop's budget across restarts. It does not call a model or edit
source. Your harness retains its normal permissions and model accounting.

For the source-first child-controller workflow, see
[Copilot CLI / Agency setup](docs/setup.md). Its `solve` loop changes only
allowlisted existing Python files; MCP feedback can check any language with
its own commands and does not require Git.

## Checks only

```powershell
.\verify-and-repair.ps1 verify --repo C:\work\project `
  --working-tests '"C:\work\project\.venv\Scripts\python.exe" -m pytest -q tests'
```

`verify` never starts repair by itself. Exit codes: **0** pass, **1** failed
checks, **2** invalid input, execution error or timeout.

## Historical results

Agency, MAI-CODE-2, medium reasoning; 164 HumanEval tasks:

| Approach | HumanEval+ | Model requests | Native AIU |
| --- | ---: | ---: | ---: |
| Tools-first | 156/164 | 641 | 182.67 |
| Source-first repair | 155/164 | 164 | 54.74 |

Source-first used less quota but solved one fewer task. These measurements
do **not** evaluate the new same-agent MCP integration.

## Links and limits

[Paper](docs/verify-and-repair-paper.pdf) |
[Installation skill](.github/skills/install-verify-and-repair/SKILL.md) |
[VS Code agent](.github/agents/verify-and-repair.agent.md)

Only public evidence goes to repair. Passing checks is not proof of complete
correctness. Supplied commands can have side effects; review them. MCP routing
is agent guidance, not a forced file-save hook or an OS sandbox. Host model
usage is not measured or capped by this server.

**Evaluation-only, source-available; not open-source.** Personal non-commercial
evaluation is permitted; other use requires written permission. See [LICENSE](LICENSE).

Development: `.\setup.ps1 -Dev`, then `.\.venv\Scripts\python.exe -m pytest -q`.
Add `.[mcp]` to include the real MCP integration check.
