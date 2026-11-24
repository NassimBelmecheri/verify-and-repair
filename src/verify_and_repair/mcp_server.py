from __future__ import annotations

from pathlib import Path

from .harness import HarnessConfig, HarnessSessions


def serve(config_path: Path, state_dir: Path | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError(
            'Install MCP support with: python -m pip install -e ".[mcp]"'
        ) from error

    sessions = HarnessSessions(HarnessConfig.load(config_path), state_dir)
    server = FastMCP(
        "Verify and Repair",
        instructions=(
            "Generate code with your own model. For an authorized implementation task, "
            "call begin_repair_task once, then verify_code. On next_action=repair, "
            "use its bounded repair_context and apply_focused_edit for a minimal anchored replacement, "
            "then verify again with the same ID. Read specific missing context only when necessary. "
            "On done or stop, end the loop. Questions and tests-only requests do not authorize repairs. "
            "Do not reset an exhausted session without renewed task authorization. "
            "No child coding CLI is launched; your host owns model accounting and permissions."
        ),
    )

    @server.tool()
    def begin_repair_task(task: str) -> dict:
        """Start feedback for generated code under the configured, user-approved task scope and budget."""
        return sessions.begin(task)

    @server.tool()
    def verify_code(session_id: str) -> dict:
        """Run configured public checks; return counterexamples, evidence delta and repair/done/stop."""
        return sessions.verify(session_id)

    @server.tool()
    def apply_focused_edit(
        session_id: str, file: str, sha256: str, old: str, new: str
    ) -> dict:
        """Apply one unique exact-anchor replacement to an allowlisted file at its expected hash; Python edits must parse."""
        return sessions.apply_edit(session_id, file, sha256, old, new)

    server.run(transport="stdio")
