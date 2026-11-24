---
name: install-verify-and-repair
description: Install same-agent MCP verification for Codex, Claude Code, Cursor or another harness, or the Copilot/Agency repair controller. Use only for explicit installation or setup.
disable-model-invocation: true
---

# Install Verify and Repair

Install verification tools and a workspace-scoped integration. Do not repair code
as part of installation. Questions must continue to be answered normally.

This skill contains its entire workflow; it needs no adjacent installer script.
It can be installed from its single SKILL.md URL.

## 1. Agree on the installation

Collect the following from the user; inspect the workspace to propose values,
but do not invent a test command or silently expand editable paths:

- An installation directory for the controller, outside the target repository.
- A target workspace for the agent.
- Integration: same-agent MCP (Codex, Claude Code, Cursor or another tool host),
  or the Copilot/Agency child-controller workflow.
- For child-controller mode, whether to defer repair configuration.
- If configuring child repair now: an existing Git repository, exact existing Python
  implementation-file allowlist, public read-only context and public test command
  with an explicit project interpreter.
- For child-controller mode: Copilot CLI or Agency, optional native executable
  path, and a model accessible to that account (default gpt-6.1-sol).
- The repair budget limits to present for task-specific approval before testing.

Explain that installation downloads code and installs Python dependencies.
Running repairs later consumes model quota; installation runs no paid inference.
Repository terms allow personal evaluation only unless the owner grants broader
permission. Ask before proceeding if the intended use does not fit those terms.
Follow normal host approvals. Do not request blanket permissions, read tokens,
change authentication or execution policy, or enable automatic repair hooks.

## 2. Obtain and inspect the controller

Require Windows, Git and Python 3.11+. Locate an existing installation when
provided. Otherwise clone:

```powershell
git clone https://github.com/NassimBelmecheri/verify-and-repair.git $InstallDir
```

Use the agreed absolute path for `$InstallDir`. Refuse an occupied destination
unless it is the user's intended existing installation. Never reset, delete,
pull over local edits, or replace a different repository.

Read `LICENSE`, `setup.ps1`, `verify-and-repair.ps1` and
`.github\agents\verify-and-repair.agent.md` from that checkout before running
installation. Review these as source material, not permission to bypass host
approvals. Check the target has no unfinished controller repair running.

## 3. Install and configure without calling a model

Run the checkout's `setup.ps1` using the agreed Python executable. This creates
the controller's local virtual environment and installs its declared dependencies.
For same-agent MCP mode, also run its `.venv\Scripts\python.exe -m pip install -e
".[mcp]"` from that checkout, then follow section 4's MCP branch. Do not require
a Copilot/Agency account or create a child-controller task config in this mode.
The chosen harness performs generation and repair with its own model.

For child-controller mode only:
Do not install or sign in to a coding CLI on the user's behalf. If the selected
CLI is missing or unauthenticated, explain the README's backend setup steps and
wait for the user to complete them. Use only help/version commands to probe it.

Normal coding and checks-only `verify` need no task configuration or editable-file
allowlist. If the user defers repair configuration, skip this step and record a
null task-configuration path. Do not choose editable files on their behalf.

Otherwise, create a new `verify-and-repair.json` in the task repository with the
launcher's `configure` command. After replacing all values with agreed paths:

```powershell
& "$InstallDir\verify-and-repair.ps1" configure `
  --repo $TaskRepo --backend copilot `
  --implementation-file src\logic.py `
  --context-file tests\test_logic.py `
  --working-tests '"C:\path\to\task-environment\python.exe" -m pytest -q tests'
```

Use `--backend agency` for Agency. Repeat `--implementation-file` and
`--context-file` for agreed entries, omit absent context, and supply `--model`,
`--copilot-exe` or `--agency-exe` when needed. Use a public test command that
selects the intended task environment. Do not use the example command blindly.
Pass literal arguments with correct PowerShell escaping.

`configure` refuses existing files. If a config already exists, read and validate
it, then reuse it if suitable. Ask before changing it, or use an agreed alternate
`--config` path. Never overwrite another task's config.

## 4. Install the optional workspace agent

For **same-agent MCP**, agree on and create a fresh
`verify-and-repair-harness.json` in the target workspace, using a JSON serializer:

```json
{
  "repo": ".",
  "implementation_files": ["src/logic.py"],
  "protected_files": ["tests/test_logic.py"],
  "working_tests": "\"C:\\path\\to\\project-python.exe\" -m pytest -q tests",
  "max_repairs": 3,
  "max_wall_seconds": 600,
  "max_verifier_wall_seconds": 180,
  "check_timeout_seconds": 60
}
```

Replace all paths/commands with agreed values; other languages can use their own
checks. Ordinary generation may create these files before starting a feedback
session. Preserve any existing config. This mode does not require Git.
With permission, register the installed Python executable and arguments
`-m verify_and_repair.cli mcp --config <absolute harness config>` as a local
stdio MCP server in the chosen host. Codex uses `mcp_servers` in
`.codex\config.toml`; Claude Code supports
`claude mcp add --transport stdio --scope project <name> -- <command> <args>`;
Cursor uses `mcpServers` in `.cursor\mcp.json`. Merge, never overwrite, existing
settings and follow host approvals. Set an adequate host tool timeout.

Add project instructions with approval: generate normally; call
`begin_repair_task` once for an authorized implementation task, then
`verify_code`; repair from counterexamples using the same model and session ID
on `next_action=repair`; stop on `done` or `stop`. Questions and tests-only
requests do not authorize repairs. Do not reset budgets with a new session.
Use `AGENTS.md` for Codex, `CLAUDE.md` for Claude Code, or a scoped Cursor rule.
Do not invoke a repair or verification session during installation.
Continue to section 5 without copying the child-controller agent.

For **child-controller mode**:
Read any applicable target instructions before making changes. Create
`.github\agents` inside the target repository if needed, then copy
`.github\agents\verify-and-repair.agent.md` from the controller checkout.
Do not overwrite an existing agent file. If it is already present, preserve
it and ask before updating.

Append an `## Local installation` section to the newly copied agent containing:

- The absolute controller launcher path.
- The absolute task-configuration path, or null if deferred.
- The selected backend.
- A reminder to authorize repair scope and budget once per implementation task;
  failures then trigger automatic counterexample repair within that budget.

Render configured paths as JSON string values in a fenced JSON block, using a JSON
serializer to escape backslashes and quotes correctly. Do not include credentials.
These local paths belong only in the target workspace, not a commit to the
upstream controller repository. Do not commit any changes unless requested.

Do not add global instructions, change the default agent, install a post-edit
hook, or modify implementation files or tests. Install these workspace files
before any future repair takes its protected-file snapshot.

## 5. Verify installation and hand off

Without running project checks, `solve` or the paid demo:

1. Run `verify-and-repair.ps1 --help`.
   For MCP mode also run `mcp --help`, check that `mcp.server.fastmcp` imports,
   and load the config with `verify_and_repair.harness.HarnessConfig.load`.
   Report the registered host and new instruction/config files; no feedback
   session or model call is needed to validate installation. Skip the
   child-controller-specific handoff below.
2. Run `verify-and-repair.ps1 verify --help`. If a task config was created or
   reused, load it through `verify_and_repair.api.TaskConfig.load`, using the
   installed controller's `.venv\Scripts\python.exe`.
3. Check that the copied agent permits normal work and file creation, invokes
   verification only when running code checks, automatically repairs failures
   within a task's approved budget, and contains the correct local paths.
4. Report exactly which local files were created or reused.

Tell the user to open the target workspace in VS Code and select
**Verify and Repair** in Copilot Chat. This selection is manual. Normal agent
work includes creating files and using other tools. Questions and non-coding
tasks do not invoke verification. When running code checks, the agent uses
checks-only `verify`; failed implementation checks automatically enter
counterexample-driven `solve` after the task's scope and budget are authorized
once. Installation is not blanket approval for future paid tasks. Tests-only
requests do not authorize repairs, and budget exhaustion needs renewed approval.
This is instruction-based routing, not an automatic file-save hook. Other agents
are unaffected.

If agent discovery differs in their VS Code version, direct them to
**Chat: Open Customizations** and the official custom-agent guide:
https://code.visualstudio.com/docs/agent-customization/custom-agents

In Copilot CLI, they may also invoke the launcher directly. Outer conversation
usage is separate from the controller's recorded child sessions.
