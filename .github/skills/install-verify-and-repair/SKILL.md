---
name: install-verify-and-repair
description: Install Verify and Repair and its optional VS Code agent for a public Python task. Use only when the user explicitly requests installation or setup.
disable-model-invocation: true
---

# Install Verify and Repair

Install the controller and a workspace-scoped custom agent. Do not repair code
as part of installation. Questions must continue to be answered normally.

This skill contains its entire workflow; it needs no adjacent installer script.
It can be installed from its single SKILL.md URL.

## 1. Agree on the installation

Collect the following from the user; inspect the workspace to propose values,
but do not invent a test command or silently expand editable paths:

- An installation directory for the controller, outside the target repository.
- An existing target Git repository with existing Python implementation files.
- The exact implementation-file allowlist and any public read-only context.
- The public test command and its Python environment.
- Backend: Copilot CLI or Agency; optionally an explicit native executable path.
- A model accessible to that account, or the default gpt-6.1-sol.

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
Do not install or sign in to a coding CLI on the user's behalf. If the selected
CLI is missing or unauthenticated, explain the README's backend setup steps and
wait for the user to complete them. Use only help/version commands to probe it.

Create a new `verify-and-repair.json` in the task repository with the launcher's
`configure` command. For example, after replacing all values with agreed paths:

```powershell
& "$InstallDir\verify-and-repair.ps1" configure `
  --repo $TaskRepo --backend copilot `
  --implementation-file src\logic.py `
  --context-file tests\test_logic.py `
  --working-tests "python -m pytest -q tests"
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

Read any applicable target instructions before making changes. Create
`.github\agents` inside the target repository if needed, then copy
`.github\agents\verify-and-repair.agent.md` from the controller checkout.
Do not overwrite an existing agent file. If it is already present, preserve
it and ask before updating.

Append an `## Local installation` section to the newly copied agent containing:

- The absolute controller launcher path.
- The absolute task-configuration path.
- The selected backend.
- A reminder to ask before spending paid model quota.

Render the two paths as JSON string values in a fenced JSON block, using a JSON
serializer to escape backslashes and quotes correctly. Do not include credentials.
These local paths belong only in the target workspace, not a commit to the
upstream controller repository. Do not commit any changes unless requested.

Do not add global instructions, change the default agent, install a post-edit
hook, or modify implementation files or tests. Install these workspace files
before any future repair takes its protected-file snapshot.

## 5. Verify installation and hand off

Without invoking `solve` or the paid demo:

1. Run `verify-and-repair.ps1 --help`.
2. Load the task config through `verify_and_repair.api.TaskConfig.load`, using
   the installed controller's `.venv\Scripts\python.exe`.
3. Check that the copied custom agent retains its question-versus-code routing
   instructions and contains the correct launcher and config paths.
4. Report exactly which local files were created or reused.

Tell the user to open the target workspace in VS Code and select
**Verify and Repair** in Copilot Chat. This selection is manual. Questions,
reviews, plans and illustrative snippets are answered normally; explicit
supported implementation-change requests use the controller after quota
authorization. This is instruction-based routing, not an enforced automatic
code-generation or file-save hook. Other agents are unaffected.

If agent discovery differs in their VS Code version, direct them to
**Chat: Open Customizations** and the official custom-agent guide:
https://code.visualstudio.com/docs/agent-customization/custom-agents

In Copilot CLI, they may also invoke the launcher directly. Outer conversation
usage is separate from the controller's recorded child sessions.
