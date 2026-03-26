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
import shlex
import shutil
import subprocess
import sys
from importlib.util import find_spec

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("test-runner")

WORKSPACE = os.environ.get("WORKSPACE_ROOT", ".")

PYTHON_TEST_CONFIGS = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
PYTHON_TEST_SUFFIXES = (
    os.path.join("tests", ""),
    os.path.join("test", ""),
)
NODE_TEST_SUFFIXES = (".test.js", ".spec.js", ".test.mjs", ".spec.mjs", ".test.cjs", ".spec.cjs")
NODE_TEST_DIRS = {
    "test",
    "tests",
    "__tests__",
}
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


def _result(returncode, output, passed, status, skipped=False):
    return {
        "returncode": returncode,
        "output": output,
        "passed": passed,
        "status": status,
        "skipped": skipped,
    }


def _skip_result(message):
    return _result(0, message, False, "skipped", skipped=True)


def _resolve_workspace_path(workspace, relative_path):
    abs_path = os.path.normpath(os.path.join(workspace, relative_path))
    if os.path.commonpath([workspace, abs_path]) != workspace:
        return None
    return abs_path


def _iter_workspace_files(workspace):
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [directory for directory in dirs if directory not in IGNORED_DIRS]
        for filename in files:
            yield root, filename


def _workspace_has_python_tests(workspace):
    for root, filename in _iter_workspace_files(workspace):
        if filename in PYTHON_TEST_CONFIGS:
            return True
        if not filename.endswith(".py"):
            continue
        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True
        relative_root = os.path.relpath(root, workspace)
        if relative_root == ".":
            continue
        normalized_root = os.path.join(relative_root, "")
        if normalized_root.startswith(PYTHON_TEST_SUFFIXES):
            return True
    return False


def _workspace_has_node_tests(workspace):
    for root, filename in _iter_workspace_files(workspace):
        if filename == "package.json":
            return True
        if filename.endswith(NODE_TEST_SUFFIXES):
            return True
        relative_root = os.path.relpath(root, workspace)
        if relative_root == ".":
            continue
        root_parts = set(relative_root.split(os.sep))
        if root_parts & NODE_TEST_DIRS:
            if filename.endswith((".js", ".mjs", ".cjs")):
                return True
    return False


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
        return _result(
            result.returncode,
            output.strip(),
            result.returncode == 0,
            "passed" if result.returncode == 0 else "failed",
        )
    except subprocess.TimeoutExpired:
        return _result(-1, f"Command timed out after {timeout}s", False, "failed")
    except FileNotFoundError as e:
        return _result(-1, f"Command not found: {e}", False, "failed")


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
        if find_spec("pytest") is None:
            return [TextContent(type="text", text=json.dumps(_skip_result("pytest is not installed in the active Python environment"), indent=2))]

        cmd = [sys.executable, "-m", "pytest", "--tb=short", "-q"]
        test_path = arguments.get("test_path")
        if test_path:
            abs_test = _resolve_workspace_path(workspace, test_path)
            if abs_test is None:
                return [TextContent(type="text", text="Error: test_path escapes workspace")]
            if not os.path.exists(abs_test):
                return [TextContent(type="text", text=json.dumps(_result(1, f"Test path not found: {test_path}", False, "failed"), indent=2))]
            cmd.append(abs_test)
        elif not _workspace_has_python_tests(workspace):
            return [TextContent(type="text", text=json.dumps(_skip_result("No Python test configuration or test files were found in the workspace"), indent=2))]

        extra = arguments.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))
        result = _run_command(cmd, cwd=workspace)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "run_node_tests":
        test_path = arguments.get("test_path")
        if test_path:
            if shutil.which("node") is None:
                return [TextContent(type="text", text=json.dumps(_skip_result("node is not installed or not available on PATH"), indent=2))]

            abs_test = _resolve_workspace_path(workspace, test_path)
            if abs_test is None:
                return [TextContent(type="text", text="Error: test_path escapes workspace")]
            if not os.path.exists(abs_test):
                return [TextContent(type="text", text=json.dumps(_result(1, f"Test path not found: {test_path}", False, "failed"), indent=2))]
            cmd = ["node", "--test", abs_test]
        else:
            has_package_json = os.path.isfile(os.path.join(workspace, "package.json"))
            has_node_tests = _workspace_has_node_tests(workspace)
            if not has_package_json and not has_node_tests:
                return [TextContent(type="text", text=json.dumps(_skip_result("No Node.js test configuration or test files were found in the workspace"), indent=2))]

            if has_package_json:
                if shutil.which("npm") is None:
                    return [TextContent(type="text", text=json.dumps(_skip_result("npm is not installed or not available on PATH"), indent=2))]
                cmd = ["npm", "test"]
            else:
                if shutil.which("node") is None:
                    return [TextContent(type="text", text=json.dumps(_skip_result("node is not installed or not available on PATH"), indent=2))]
                cmd = ["node", "--test"]
        result = _run_command(cmd, cwd=workspace)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
