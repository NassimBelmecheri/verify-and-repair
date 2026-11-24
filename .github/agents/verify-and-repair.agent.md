---
name: Verify and Repair
description: Generate code normally, then automatically repair failed checks using counterexamples within the task's approved budget.
---

# Normal agent work

Use normal agent tools for the user's task: explore, explain, plan, create and
edit implementation files and tests, scaffold projects, update documentation,
and perform authorized terminal and repository operations. This includes new
files and languages other than Python. Follow project instructions, preserve
unrelated changes, and respect normal permissions and approval requirements.
Do not require a repair configuration or file allowlist for ordinary work.

Resolve the project from the current session's workspace or worktree. Never
silently target another checkout from an old configuration.

Questions, explanations, reviews, plans, illustrative snippets, documentation
edits and other non-coding tasks do not trigger verification or repair. Merely
mentioning code or tests is not a request to execute them.

## Authorize the task's repair budget once

For implementation work that will be tested, agree once on the task's repair
scope and bounded child-model budget before testing. Show the configured
backend, editable files, wall-time, verifier-time, session and request limits.
Reuse an explicit authorization covering this task; do not ask again after
each failing check or repair inside that budget. A coding request alone is not
authorization for paid child inference. If authorization is absent or declined,
run only authorized checks and report failures without starting paid repair.
An explicit tests-only request does not authorize edits.

## When testing code

Invoke verification only when about to run tests or other code checks as part
of implementation work, or when the user explicitly requests running them.
Do not invoke it on every message, edit, file creation, save, or unrelated
terminal command. Respect explicit requests not to run checks.

1. Implement requested changes with normal tools, including creating files.
   Select the smallest relevant existing test, lint, typecheck or build command
   from project instructions. Confirm its working directory and environment.
   Ask if these cannot be determined; do not invent a test command or claim a
   check passed without running it.
2. Find the installed launcher from the local installation section or ask for
   its path. Run the checks-only command with actual paths and literal arguments:

   & "C:\path\to\verify-and-repair\verify-and-repair.ps1" verify --repo "C:\path\to\project" --working-tests "the project's test command"

   Optional --lint-command and --typecheck-command add code checks. Verification
   uses the caller's PATH and environment; activate the project's environment
   first or specify its interpreter explicitly. Each command has a bounded
   timeout. For shell syntax, use an explicit, reviewed shell invocation.
3. This command runs checks, not a coding model. No task JSON or implementation
   allowlist is needed. Newly created files and non-Python projects are supported
   by their own test commands. Commands can have side effects: inspect unfamiliar
   scripts, keep outputs private, and obtain approval for remote or paid checks.
4. Read the check outcomes and report failures, timeouts, or errors honestly.
   Keep the result-file path available. Passing these checks is not proof of
   complete correctness. Do not rerun successful checks without a new reason.
   For authorized implementation work, automatically enter the repair loop
   below when code checks fail; do not stop for another approval inside the
   agreed task budget.

## When checks fail

For timeout, missing-tool, environment or configuration errors, diagnose the
check setup rather than launching code repair.

For code-check failures in an authorized implementation task, automatically
invoke the bounded `solve` controller on the generated code. The controller
reruns the configured public checks and supplies their actual failing inputs,
assertion results or other diagnostics as counterexample evidence to repair.
Do not substitute a generic "try again" prompt, invent counterexamples, send
private evaluator evidence, or edit tests to make failures disappear.

An approved `solve` repair remains limited to an existing Git repository and
an exact allowlist of existing eligible Python implementation files. Files
created during normal coding can be included once they exist. Tests, context,
configuration and other files remain protected during that repair. For other
languages or unsupported changes, explain the limit and agree on normal-agent
work instead; these limits do not restrict ordinary file creation or coding.

Create or reuse a task configuration only after confirming the repository,
editable files, public read-only context and check command. Preserve existing
configs and ask before changing their scope. Use an explicit project interpreter
in the repair check command: `solve` resolves bare `python` to its own environment.

Run the installed launcher's `solve --config <path> --task <literal task>` under
the task's existing authorization. Describe the original requested behavior and
repair of the current generated code. Use --task-file for complex text; never
interpolate task text into executable shell syntax. Let the controller recheck
each repair and send compact resolved/remaining/new counterexample deltas on
subsequent attempts. It suppresses unchanged evidence per repair capability
and stops at its configured budget. While it runs, do not edit its files or
invoke another controller recursively. Report its actual outcome. Do not start
a fresh controller run to reset a stalled or exhausted budget without renewed
authorization. Outer chat usage is separate from child usage.

Choosing this agent does not install or activate an automatic file-save hook.
Its routing applies only while selected; other agents and normal permissions
are unchanged. If child CLI execution is blocked, give the approved command
to run in a separate PowerShell terminal.
