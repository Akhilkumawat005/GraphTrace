from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from graphtrace.git_analyzer import GitAnalyzer
from graphtrace.graph_engine import DependencyGraph
from graphtrace.storage import GraphStorage

MCP_TOOLS = [
    {
        "name": "get_blast_radius",
        "description": "Compute downstream symbols, files, and risk scores impacted by modifying a target symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or full ID (e.g. 'verify_token' or 'auth.py::verify_token')",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum reverse dependency hop depth (default 5)",
                    "default": 5,
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_impacted_tests",
        "description": "Analyze current Git working-tree or commit diff and return impacted symbols and minimal test command.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff_spec": {
                    "type": "string",
                    "description": "Optional git diff target (e.g. 'HEAD~1'). Omit for uncommitted working tree changes.",
                }
            },
        },
    },
]


class GraphTraceMCPServer:
    """Zero-dependency JSON-RPC 2.0 stdio MCP server exposing GraphTrace tools to AI agents."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.db_path = self.repo_root / ".graphtrace" / "index.db"

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.db_path.is_file():
            raise ValueError("GraphTrace index not found. Run `graphtrace index` in the repository first.")

        storage = GraphStorage(self.db_path)
        try:
            if name == "get_blast_radius":
                symbol = str(arguments.get("symbol", "")).strip()
                max_depth = int(arguments.get("max_depth", 5))
                matches = storage.find_symbols_by_query(symbol)
                if not matches:
                    return {"seed_symbols": [], "impacted": []}
                seed_ids = [r["id"] for r in matches]
                dep_graph = DependencyGraph(storage)
                impacts = dep_graph.compute_blast_radius(seed_ids, max_depth=max_depth)
                return {
                    "seed_symbols": seed_ids,
                    "impacted": [item.model_dump() for item in impacts],
                }

            if name == "get_impacted_tests":
                diff_spec = arguments.get("diff_spec")
                git_analyzer = GitAnalyzer(self.repo_root, storage)
                modified_files, mutated_symbols = git_analyzer.map_diff_to_symbols(diff_spec=diff_spec)
                dep_graph = DependencyGraph(storage)
                report = dep_graph.build_diff_report(
                    diff_target=diff_spec or "WORKING_TREE",
                    modified_files=modified_files,
                    mutated_symbols=mutated_symbols,
                )
                return report.model_dump()

            raise ValueError(f"Unknown tool: {name}")
        finally:
            storage.close()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}

        # Notifications have no 'id' and require no response
        if req_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "graphtrace-mcp", "version": "0.1.0"},
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": MCP_TOOLS},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                payload = self._execute_tool(tool_name, tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def serve_stdio(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle_request(req)
            if resp is not None:
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()