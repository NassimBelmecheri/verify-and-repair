# Token-efficient repair and task accounting

These features extend the **same-agent harness integration**. They do not
change historical benchmark runs or prove a token-saving percentage.
See [harness setup](harnesses.md) to connect Codex, Claude Code, Cursor or another
host. The existing Copilot/Agency child-controller remains unchanged.

## 1. Compile a bounded repair packet

When verification fails, `verify_code` now includes `repair_context`:

- Relevant implementation excerpts with file SHA-256 and source line ranges.
- For Python, AST-selected functions, directly referenced imports/constants/types
  and same-file callers when identifiable from failure names or locations.
- Read-only public context explicitly supplied in `context_files`.
- An omission count when the encoded context exceeds its budget.

Optional harness configuration fields:

```json
{
  "context_files": ["tests/test_logic.py", "src/money_types.py"],
  "max_context_bytes": 12000
}
```

Merge these fields into the harness config, not the child-controller config.
Context files are read-only and fingerprinted for the repair session. Only
configured public files are read: there is no repository-wide scan or private
evaluator discovery.

The compiler returns source excerpts rather than model-written summaries.
It uses Python heuristics, not complete interprocedural analysis. Other
languages and syntax errors receive bounded excerpts around a reported location,
or the file's start if no location is available. If a necessary symbol is
missing or too large, `needs_more_context` is true when snippets were omitted;
the host should read only the missing public definition rather than guess.
The byte cap bounds the `repair_context` object, **not all conversation tokens**.
Source hashes detect stale edits; they are not semantic correctness proofs.

## 2. Apply a focused edit, not a rewritten file

The MCP tool `apply_focused_edit` accepts:

```json
{
  "session_id": "the existing feedback session ID",
  "file": "src/logic.py",
  "sha256": "the file hash from repair_context",
  "old": "return x - 1",
  "new": "return x + 1"
}
```

The tool requires a pending repair from an actual failed check. It rejects
out-of-scope paths, changed protected files, expired budgets, stale source
hashes, ambiguous/missing anchors and invalid replacement Python syntax.
It replaces **one unique exact occurrence**, preserves unrelated bytes and
atomically replaces that file. An individual old/new edit is limited to 100 KB.

For another edit to the same file, use its returned updated hash. Reverify with
the same session ID after completing the repair. Applying an edit never claims
that tests pass. Use the existing host tools to read specific missing context
or handle unsupported encodings; do not broaden repair scope silently.

These checks do not sandbox a host that has its own file-editing tools.
Non-Python edits are validated for scope, hash and anchor, then by the real
configured checks; no language-specific parser is claimed for them.

## 3. Account for the whole task, including outer-agent work

The server cannot see every model request inside arbitrary hosts. Export the
host/provider's actual per-request usage and normalize it into a local JSON
array. Include **initial generation, retries, repair and reviews**, not only
requests after the feedback session started. Keep the export private.

Example format (illustrative values, not measurements):

```json
[
  {
    "request_id": "provider-account-request-001",
    "model": "your-actual-model",
    "phase": "generation",
    "input_tokens": 1000,
    "output_tokens": 100,
    "cache_read_tokens": 700,
    "cache_write_tokens": 100,
    "reasoning_tokens": 30,
    "cost": 0.02,
    "currency": "USD"
  }
]
```

Use globally namespaced request IDs, stable across overlapping exports.
Allowed phases: `generation`, `repair`, `review`, `other`. **Input totals include
cache reads/writes; output totals include reasoning.** Normalize exports using
different conventions before import. Do not add these subcounts twice or
guess missing token values. If the host does not expose a required count,
accounting remains incomplete; the integration cannot manufacture it.
`cost`/`currency` are optional; copy actual billing values when available.
Native units such as AIU must use their own label, not be called dollars.
Do not include credentials, prompts or raw conversation content.

Import receipts from PowerShell after completing the task:

```powershell
.\verify-and-repair.ps1 account `
  --config C:\work\project\verify-and-repair-harness.json `
  --session-id "the-existing-session-uuid" `
  --receipts C:\private\host-usage.json `
  --task-complete --accepted yes
```

Omit `--task-complete` for a partial export. Use `--accepted no` for rejected
tasks. `--accepted yes` requires passing configured checks and a completeness
attestation; it is a user decision, not a hidden-correctness assertion.
Do not finalize until any final model review/report request has been included.
Import after host execution, not by asking the model to invent its usage.
The CLI records export hashes and idempotently deduplicates request IDs;
conflicting values are rejected. Finalized receipts cannot silently grow.

Project-level summary:

```powershell
.\verify-and-repair.ps1 account-report `
  --config C:\work\project\verify-and-repair-harness.json
```

The report includes observed request/token totals, cache subcounts, phase
breakdown, billing units, accepted-task count and **all imported costs divided
by accepted tasks**, including costs from rejected tasks. Sessions from other
projects sharing the artifacts directory are excluded. Compare separate
artifacts directories for baseline and treatment, supplying `--state-dir`
consistently to the server and accounting commands.

Missing exports are unknown, not zero. Missing costs, incomplete coverage,
unknown acceptance or overlapping requests across tasks suppress cost per
accepted task. Billing units are never merged. Completeness is explicitly
**attested from imported receipts**, not independently verified by the server.
The server neither intercepts native host usage nor caps host token spending.

For a company comparison, hold tasks, acceptance criteria, host/model and budgets
constant. Count initial generation and failures in both conditions. Savings
must come from those measurements, not smaller packets alone.
