#!/usr/bin/env python3
"""
MCP Server — Test Runner

Expone tools para ejecutar tests (pytest, Node.js) vía MCP.
Permite al agente verificar que sus fixes no rompen tests.

Transporte: stdio (el agente lo lanza como subproceso).

Uso standalone:
    python test_runner_server.py            # arranca vía stdio
    WORKSPACE_ROOT=/path python test_runner_server.py
"""

import json
import os
import subprocess
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("test-runner")

WORKSPACE = os.environ.get("WORKSPACE_ROOT", ".")


def _run_command(cmd, cwd=None, timeout=120):
    """Run a command and return stdout+stderr."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or WORKSPACE,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n--- stderr ---\n" + result.stderr
        return {
            "returncode": result.returncode,
            "output": output.strip(),
            "passed": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "output": f"Command timed out after {timeout}s",
            "passed": False,
        }
    except FileNotFoundError as e:
        return {
            "returncode": -1,
            "output": f"Command not found: {e}",
            "passed": False,
        }


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="run_pytest",
            description="Run pytest on the workspace or a specific test file. Returns test results including pass/fail counts and failure details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "test_path": {
                        "type": "string",
                        "description": "Optional path to a specific test file or directory, relative to workspace root. If omitted, runs all tests.",
                    },
                    "extra_args": {
                        "type": "string",
                        "description": "Optional extra arguments to pass to pytest (e.g. '-x -v').",
                    },
                },
            },
        ),
        Tool(
            name="run_node_tests",
            description="Run Node.js tests using 'npm test' or 'node --test' on the workspace or a specific file. Returns test results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "test_path": {
                        "type": "string",
                        "description": "Optional path to a specific test file, relative to workspace root. If omitted, runs 'npm test'.",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    workspace = os.path.abspath(WORKSPACE)

    if name == "run_pytest":
        cmd = [sys.executable, "-m", "pytest", "--tb=short", "-q"]
        test_path = arguments.get("test_path")
        if test_path:
            # Validate path stays within workspace
            abs_test = os.path.normpath(os.path.join(workspace, test_path))
            if not abs_test.startswith(workspace):
                return [TextContent(type="text", text="Error: test_path escapes workspace")]
            cmd.append(abs_test)
        extra = arguments.get("extra_args", "")
        if extra:
            cmd.extend(extra.split())
        result = _run_command(cmd, cwd=workspace)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "run_node_tests":
        test_path = arguments.get("test_path")
        if test_path:
            abs_test = os.path.normpath(os.path.join(workspace, test_path))
            if not abs_test.startswith(workspace):
                return [TextContent(type="text", text="Error: test_path escapes workspace")]
            cmd = ["node", "--test", abs_test]
        else:
            cmd = ["npm", "test"]
        result = _run_command(cmd, cwd=workspace)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
