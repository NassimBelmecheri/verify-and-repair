from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .contracts import contained_file


def compile_context(
    repo: Path,
    implementation_files: tuple[str, ...],
    context_files: tuple[str, ...],
    diagnostics: str,
    max_bytes: int,
) -> dict:
    """Slice only explicitly supplied public files; never search the repository."""
    chunks: list[tuple[int, str, int, int, str | None, str]] = []
    documents = {}
    wanted = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", diagnostics))
    for name in (*implementation_files, *context_files):
        data = contained_file(repo, name).read_bytes()
        text = data.decode("utf-8-sig")
        lines = text.splitlines(keepends=True)
        digest = hashlib.sha256(data).hexdigest()
        locations = [
            int(match[1])
            for match in re.finditer(
                re.escape(name.replace("\\", "/")) + r'(?::|", line )(\d+)',
                diagnostics.replace("\\", "/"),
            )
        ]
        documents[name] = (lines, digest)
        try:
            tree = ast.parse(text) if name.endswith(".py") else None
        except SyntaxError:
            tree = None
        symbols = (
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if tree
            else []
        )
        selected = [
            node
            for node in symbols
            if node.name in wanted
            or any(
                node.lineno <= line <= (node.end_lineno or node.lineno)
                for line in locations
            )
        ]
        for node in selected:
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            chunks.append(
                (
                    0,
                    name,
                    start,
                    node.end_lineno or node.lineno,
                    node.name,
                    "failure_target",
                )
            )
        if tree and selected:
            referenced = {
                item.id
                for node in selected
                for item in ast.walk(node)
                if isinstance(item, ast.Name)
            }
            for definition in tree.body:
                if definition in selected:
                    continue
                defined = set()
                if isinstance(definition, (ast.Import, ast.ImportFrom)):
                    defined = {
                        item.asname or item.name.split(".")[0]
                        for item in definition.names
                    }
                elif isinstance(
                    definition, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    defined = {definition.name}
                elif isinstance(definition, (ast.Assign, ast.AnnAssign)):
                    defined = {
                        item.id
                        for item in ast.walk(definition)
                        if isinstance(item, ast.Name)
                        and isinstance(item.ctx, ast.Store)
                    }
                if referenced & defined:
                    chunks.append(
                        (
                            1,
                            name,
                            definition.lineno,
                            definition.end_lineno or definition.lineno,
                            getattr(definition, "name", None),
                            "dependency",
                        )
                    )
            target_names = {node.name for node in selected}
            for node in symbols:
                if node not in selected and any(
                    isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id in target_names
                    for item in ast.walk(node)
                ):
                    chunks.append(
                        (
                            2,
                            name,
                            node.lineno,
                            node.end_lineno or node.lineno,
                            node.name,
                            "caller",
                        )
                    )
        if not selected:
            line = locations[-1] if locations else 1
            chunks.append(
                (
                    3,
                    name,
                    max(1, line - 5),
                    min(len(lines), line + 34),
                    None,
                    "public_excerpt",
                )
            )
    packet: dict[str, Any] = {
        "snippets": [],
        "omitted_snippets": 0,
        "needs_more_context": False,
        "scope": "Configured public files only; Python AST heuristics are not a complete dependency graph.",
    }
    seen = set()
    for _, name, start, end, symbol, reason in sorted(chunks, key=lambda item: item[0]):
        key = (name, start, end)
        if key in seen:
            continue
        seen.add(key)
        lines, digest = documents[name]
        snippet = {
            "path": name,
            "sha256": digest,
            "editable": name in implementation_files,
            "symbol": symbol,
            "start_line": start,
            "end_line": end,
            "reason": reason,
            "code": "".join(lines[start - 1 : end]),
        }
        packet["snippets"].append(snippet)
        if len(json.dumps(packet, ensure_ascii=True).encode("utf-8")) > max_bytes:
            packet["snippets"].pop()
            packet["omitted_snippets"] += 1
    packet["needs_more_context"] = packet["omitted_snippets"] > 0
    # Reserve room for the omission count changing while assembling the packet.
    while (
        packet["snippets"]
        and len(json.dumps(packet, ensure_ascii=True).encode("utf-8")) > max_bytes
    ):
        packet["snippets"].pop()
        packet["omitted_snippets"] += 1
        packet["needs_more_context"] = True
    return packet
