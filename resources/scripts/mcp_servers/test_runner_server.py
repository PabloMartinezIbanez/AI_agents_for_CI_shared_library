#!/usr/bin/env python3
"""
MCP Server — Test Runner (modular)

Expone 3 tools MCP:
  - discover_tests   : detecta frameworks y archivos de test en el workspace
  - run_tests        : ejecuta tests (pytest y/o Node.js)
  - analyze_failures : parsea el output de tests fallidos y clasifica la culpa

Transporte: stdio (el agente lo lanza como subproceso).

Uso:
    WORKSPACE_ROOT=/path python test_runner_server.py
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from importlib.util import find_spec

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ─────────────────────────────────────────────────────────────────────
# 1. Constants & Config
# ─────────────────────────────────────────────────────────────────────

server = Server("test-runner")

WORKSPACE = os.environ.get("WORKSPACE_ROOT", ".")

PYTHON_TEST_CONFIGS = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
PYTHON_TEST_DIR_NAMES = {"tests", "test"}

NODE_TEST_FILE_SUFFIXES = (
    ".test.js", ".spec.js",
    ".test.mjs", ".spec.mjs",
    ".test.cjs", ".spec.cjs",
    ".test.ts", ".spec.ts",
)
NODE_TEST_DIR_NAMES = {"test", "tests", "__tests__"}

IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env",
    "node_modules", "__pycache__",
    ".ai_fixer", ".tox", "dist", "build",
}

# ─────────────────────────────────────────────────────────────────────
# 2. Helpers
# ─────────────────────────────────────────────────────────────────────


def _resolve_workspace_path(workspace: str, relative_path: str):
    """Resolve *relative_path* inside *workspace*; return None if it escapes."""
    abs_path = os.path.normpath(os.path.join(workspace, relative_path))
    if os.path.commonpath([workspace, abs_path]) != workspace:
        return None
    return abs_path


def _iter_workspace_files(workspace: str):
    """Walk workspace yielding (root, filename), skipping ignored dirs."""
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for filename in files:
            yield root, filename


def _run_command(cmd, cwd=None, timeout=120):
    """Run a shell command and return a result dict."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or WORKSPACE,
            timeout=timeout,
        )
        output = ""
        if proc.stdout:
            output += proc.stdout
        if proc.stderr:
            output += "\n--- stderr ---\n" + proc.stderr
        return {
            "returncode": proc.returncode,
            "output": output.strip(),
            "passed": proc.returncode == 0,
            "status": "passed" if proc.returncode == 0 else "failed",
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "output": f"Command timed out after {timeout}s",
                "passed": False, "status": "timeout"}
    except FileNotFoundError as exc:
        return {"returncode": -1, "output": f"Command not found: {exc}",
                "passed": False, "status": "error"}


def _skip(message: str):
    return {"returncode": 0, "output": message, "passed": False,
            "status": "skipped", "skipped": True}


def _normalize_node_zero_tests_result(result: dict) -> dict:
    """Treat Node.js runs with 0 executed tests as skipped instead of passed."""
    output = result.get("output", "")
    if "tests 0" in output and "fail 0" in output:
        normalized = dict(result)
        normalized["passed"] = False
        normalized["status"] = "skipped"
        normalized["skipped"] = True
        return normalized
    return result


# ─────────────────────────────────────────────────────────────────────
# 3. Discovery
# ─────────────────────────────────────────────────────────────────────


def discover_python_tests(workspace: str) -> dict:
    """Scan workspace for Python/pytest test artefacts."""
    config_files = []
    test_files = []
    test_dirs = set()

    for root, filename in _iter_workspace_files(workspace):
        rel_root = os.path.relpath(root, workspace)

        # Config files
        if filename in PYTHON_TEST_CONFIGS:
            config_files.append(
                os.path.join(rel_root, filename) if rel_root != "." else filename
            )

        if not filename.endswith(".py"):
            continue

        # Test files by naming convention
        if filename.startswith("test_") or filename.endswith("_test.py"):
            rel_file = os.path.join(rel_root, filename) if rel_root != "." else filename
            test_files.append(rel_file)
            test_dirs.add(rel_root if rel_root != "." else ".")
            continue

        # Files inside known test directories
        if rel_root != ".":
            top_dir = rel_root.split(os.sep)[0]
            if top_dir in PYTHON_TEST_DIR_NAMES:
                rel_file = os.path.join(rel_root, filename)
                test_files.append(rel_file)
                test_dirs.add(rel_root)

    detected = bool(test_files or config_files)
    return {
        "framework": "pytest",
        "detected": detected,
        "config_files": sorted(config_files),
        "test_dirs": sorted(test_dirs),
        "test_files": sorted(test_files),
    }


def discover_node_tests(workspace: str) -> dict:
    """Scan workspace for Node.js test artefacts."""
    has_package_json = False
    test_script = None
    test_files = []
    test_dirs = set()

    pkg_path = os.path.join(workspace, "package.json")
    if os.path.isfile(pkg_path):
        has_package_json = True
        try:
            with open(pkg_path, "r", encoding="utf-8") as fh:
                pkg = json.load(fh)
            test_script = pkg.get("scripts", {}).get("test")
        except (json.JSONDecodeError, OSError):
            pass

    for root, filename in _iter_workspace_files(workspace):
        rel_root = os.path.relpath(root, workspace)

        if filename.endswith(NODE_TEST_FILE_SUFFIXES):
            rel_file = os.path.join(rel_root, filename) if rel_root != "." else filename
            test_files.append(rel_file)
            test_dirs.add(rel_root if rel_root != "." else ".")
            continue

        # JS files inside known test directories
        if rel_root != "." and filename.endswith((".js", ".mjs", ".cjs", ".ts")):
            top_dir = rel_root.split(os.sep)[0]
            if top_dir in NODE_TEST_DIR_NAMES:
                rel_file = os.path.join(rel_root, filename)
                test_files.append(rel_file)
                test_dirs.add(rel_root)

    detected = bool(test_files) or bool(test_script)
    return {
        "framework": "node",
        "detected": detected,
        "has_package_json": has_package_json,
        "test_script": test_script,
        "test_dirs": sorted(test_dirs),
        "test_files": sorted(test_files),
    }


def discover_all(workspace: str) -> dict:
    return {
        "frameworks": [
            discover_python_tests(workspace),
            discover_node_tests(workspace),
        ]
    }


# ─────────────────────────────────────────────────────────────────────
# 4. Execution
# ─────────────────────────────────────────────────────────────────────


def run_python_tests(workspace: str, test_path: str | None = None,
                     extra_args: str = "") -> dict:
    """Execute pytest and return results."""
    bootstrap_output = ""

    if find_spec("pytest") is not None:
        cmd = [sys.executable, "-m", "pytest", "--tb=long", "-v"]
    else:
        install_result = _run_command(
            [sys.executable, "-m", "pip", "install", "pytest"],
            cwd=workspace,
            timeout=240,
        )
        if install_result["returncode"] == 0 and find_spec("pytest") is not None:
            cmd = [sys.executable, "-m", "pytest", "--tb=long", "-v"]
            bootstrap_output = (
                "[bootstrap] pytest was missing and has been installed in the active environment.\n"
                + (install_result.get("output") or "")
            ).strip()
        else:
            pytest_bin = shutil.which("pytest")
            if not pytest_bin:
                install_log = install_result.get("output") or ""
                return _skip(
                    "pytest is not installed in the active Python environment, automatic installation failed, "
                    "and no global pytest executable was found on PATH.\n"
                    + install_log
                )
            cmd = [pytest_bin, "--tb=long", "-v"]
            bootstrap_output = (
                "[bootstrap] pytest installation in active environment failed; using global pytest from PATH.\n"
                + (install_result.get("output") or "")
            ).strip()

    if test_path:
        abs_test = _resolve_workspace_path(workspace, test_path)
        if abs_test is None:
            return {"returncode": 1, "output": "Error: test_path escapes workspace",
                    "passed": False, "status": "error"}
        if not os.path.exists(abs_test):
            return {"returncode": 1, "output": f"Test path not found: {test_path}",
                    "passed": False, "status": "error"}
        cmd.append(abs_test)
    else:
        info = discover_python_tests(workspace)
        if not info["detected"]:
            return _skip("No Python test files found in the workspace")

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    result = _run_command(cmd, cwd=workspace)
    if bootstrap_output:
        output = result.get("output") or ""
        result["output"] = (bootstrap_output + "\n\n" + output).strip() if output else bootstrap_output
    return result


def run_node_tests(workspace: str, test_path: str | None = None) -> dict:
    """Execute Node.js tests and return results."""
    if test_path:
        if shutil.which("node") is None:
            return _skip("node is not installed or not available on PATH")
        abs_test = _resolve_workspace_path(workspace, test_path)
        if abs_test is None:
            return {"returncode": 1, "output": "Error: test_path escapes workspace",
                    "passed": False, "status": "error"}
        if not os.path.exists(abs_test):
            return {"returncode": 1, "output": f"Test path not found: {test_path}",
                    "passed": False, "status": "error"}
        cmd = ["node", "--test", abs_test]
    else:
        info = discover_node_tests(workspace)
        if not info["detected"]:
            return _skip("No Node.js test files found in the workspace")

        if info["has_package_json"] and info.get("test_script"):
            if shutil.which("npm") is None:
                return _skip("npm is not installed or not available on PATH")
            cmd = ["npm", "test"]
        else:
            if shutil.which("node") is None:
                return _skip("node is not installed or not available on PATH")
            cmd = ["node", "--test"]

    result = _run_command(cmd, cwd=workspace)
    return _normalize_node_zero_tests_result(result)


# ─────────────────────────────────────────────────────────────────────
# 5. Analysis — parse test output into structured failures
# ─────────────────────────────────────────────────────────────────────

# Regex patterns for pytest traceback parsing
_RE_PYTEST_FAILURE_HEADER = re.compile(
    r"^_{3,}\s+(.+?)\s+_{3,}$"
)
_RE_PYTEST_FILE_LINE = re.compile(
    r"^([^\s].*?):(\d+):\s+in\s+(.+)$"
)
_RE_PYTEST_ERROR_LINE = re.compile(
    r"^E\s+(\w[\w.]*(?:Error|Exception|Warning|Failure)?):?\s*(.*)"
)
_RE_PYTEST_SHORT_SUMMARY = re.compile(
    r"^(?:FAILED|ERROR)\s+(.*?)::(.+?)(?:\s+-\s+(.+))?$"
)

# Regex patterns for Node.js TAP / node --test output
_RE_NODE_NOT_OK = re.compile(r"^not ok\s+\d+\s+-\s+(.+)$", re.MULTILINE)
_RE_NODE_ERROR_LINE = re.compile(r"^\s+error:\s*'?(.+?)'?\s*$", re.MULTILINE)
_RE_NODE_STACK_FRAME = re.compile(
    r"at\s+(?:(.+?)\s+)?\(?(.+?):(\d+):\d+\)?"
)


def _is_test_file(filepath: str) -> bool:
    """Heuristic: does the path look like a test file?"""
    base = os.path.basename(filepath)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    for suffix in NODE_TEST_FILE_SUFFIXES:
        if base.endswith(suffix):
            return True
    parts = filepath.replace("\\", "/").split("/")
    for part in parts:
        if part in ("tests", "test", "__tests__"):
            return True
    return False


def classify_fault(failure: dict) -> str:
    """Heuristic to decide if the failure is likely caused by source or test.

    Returns "source", "test", or "unknown".
    """
    source_frames = failure.get("source_frames", [])
    error_type = failure.get("error_type", "")

    # If there are frames in source (non-test) code, the fault is likely there
    if source_frames:
        return "source"

    # Pure assertion errors with no source frames -> the test itself is probably wrong
    if error_type in ("AssertionError", "AssertionError", "AssertError"):
        return "test"

    return "unknown"


def parse_pytest_failures(output: str) -> list[dict]:
    """Parse verbose pytest output into a list of structured failure dicts."""
    failures = []
    lines = output.splitlines()

    # Strategy 1: parse full tracebacks between ___ HEADER ___ markers
    i = 0
    while i < len(lines):
        header_match = _RE_PYTEST_FAILURE_HEADER.match(lines[i])
        if not header_match:
            i += 1
            continue

        test_id = header_match.group(1).strip()
        i += 1

        # Collect all lines until next header or short-test-summary / ====
        tb_lines = []
        while i < len(lines):
            if _RE_PYTEST_FAILURE_HEADER.match(lines[i]):
                break
            if lines[i].startswith("="):
                break
            tb_lines.append(lines[i])
            i += 1

        traceback_text = "\n".join(tb_lines)

        # Extract frames from traceback
        all_frames = []
        for line in tb_lines:
            fm = _RE_PYTEST_FILE_LINE.match(line)
            if fm:
                all_frames.append({
                    "file": fm.group(1),
                    "line": int(fm.group(2)),
                    "function": fm.group(3),
                })

        # Extract error type + message from E lines
        error_type = ""
        error_message = ""
        for line in tb_lines:
            em = _RE_PYTEST_ERROR_LINE.match(line)
            if em:
                error_type = em.group(1)
                error_message = em.group(2).strip()
                break

        # Separate test frames from source frames
        source_frames = [f for f in all_frames if not _is_test_file(f["file"])]
        test_frames = [f for f in all_frames if _is_test_file(f["file"])]

        # Determine test file / line from the test_id or the last test frame
        test_file = ""
        test_line = 0
        if "::" in test_id:
            test_file = test_id.split("::")[0]
        if test_frames:
            test_file = test_file or test_frames[-1]["file"]
            test_line = test_frames[-1]["line"]

        failure = {
            "test_name": test_id,
            "test_file": test_file,
            "test_line": test_line,
            "error_type": error_type,
            "error_message": error_message,
            "traceback": traceback_text.strip(),
            "source_frames": source_frames,
            "likely_fault": "",
        }
        failure["likely_fault"] = classify_fault(failure)
        failures.append(failure)

    # Strategy 2 (fallback): parse short test summary lines
    if not failures:
        for line in lines:
            sm = _RE_PYTEST_SHORT_SUMMARY.match(line.strip())
            if sm:
                test_file = sm.group(1)
                test_name = sm.group(2)
                error_msg = sm.group(3) or ""
                failures.append({
                    "test_name": f"{test_file}::{test_name}",
                    "test_file": test_file,
                    "test_line": 0,
                    "error_type": "",
                    "error_message": error_msg,
                    "traceback": "",
                    "source_frames": [],
                    "likely_fault": "unknown",
                })

    return failures


def parse_node_failures(output: str) -> list[dict]:
    """Parse Node.js test runner (TAP) or npm test output into structured failures."""
    failures = []
    lines = output.splitlines()

    for idx, line in enumerate(lines):
        m = _RE_NODE_NOT_OK.match(line.strip())
        if not m:
            continue

        test_name = m.group(1).strip()

        # Gather indented context lines after the "not ok" line
        ctx_lines = []
        j = idx + 1
        while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip() == ""):
            ctx_lines.append(lines[j])
            j += 1
        context_text = "\n".join(ctx_lines)

        # Extract error message
        error_message = ""
        em = _RE_NODE_ERROR_LINE.search(context_text)
        if em:
            error_message = em.group(1).strip()

        # Extract stack frames
        all_frames = []
        for cl in ctx_lines:
            fm = _RE_NODE_STACK_FRAME.search(cl)
            if fm:
                filepath = fm.group(2)
                if filepath.startswith("node:") or "node_modules" in filepath:
                    continue
                all_frames.append({
                    "file": filepath,
                    "line": int(fm.group(3)),
                    "function": fm.group(1) or "(anonymous)",
                })

        source_frames = [f for f in all_frames if not _is_test_file(f["file"])]
        test_frames = [f for f in all_frames if _is_test_file(f["file"])]

        test_file = test_frames[0]["file"] if test_frames else ""
        test_line = test_frames[0]["line"] if test_frames else 0

        error_type = ""
        for cl in ctx_lines:
            type_match = re.search(r"(\w+(?:Error|Exception)):", cl)
            if type_match:
                error_type = type_match.group(1)
                break

        failure = {
            "test_name": test_name,
            "test_file": test_file,
            "test_line": test_line,
            "error_type": error_type,
            "error_message": error_message,
            "traceback": context_text.strip(),
            "source_frames": source_frames,
            "likely_fault": "",
        }
        failure["likely_fault"] = classify_fault(failure)
        failures.append(failure)

    return failures


def analyze_test_output(framework: str, test_output: str) -> dict:
    """Dispatch to the right parser and return structured failure info."""
    framework = framework.lower().strip()
    if framework in ("pytest", "python"):
        parsed = parse_pytest_failures(test_output)
    elif framework in ("node", "nodejs", "npm", "node.js"):
        parsed = parse_node_failures(test_output)
    else:
        return {"error": f"Unknown framework: {framework}",
                "supported": ["pytest", "node"]}

    return {
        "framework": framework,
        "total_failures": len(parsed),
        "failures": parsed,
    }


# ─────────────────────────────────────────────────────────────────────
# 6. MCP Tool definitions
# ─────────────────────────────────────────────────────────────────────


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="discover_tests",
            description=(
                "Scan the workspace to detect available test frameworks and test files. "
                "Returns which frameworks are present (pytest, Node.js), their config files, "
                "test directories, and individual test files found."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="run_tests",
            description=(
                "Execute tests in the workspace. Can run a specific framework (pytest or node) "
                "or all detected frameworks. Returns exit code, pass/fail status, and full output "
                "including tracebacks for failures. Use 'analyze_failures' on the output to get "
                "structured failure data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "framework": {
                        "type": "string",
                        "description": (
                            "Which framework to run: 'pytest' or 'node'. "
                            "If omitted, runs all detected frameworks."
                        ),
                    },
                    "test_path": {
                        "type": "string",
                        "description": (
                            "Optional path to a specific test file or directory, "
                            "relative to workspace root."
                        ),
                    },
                    "extra_args": {
                        "type": "string",
                        "description": (
                            "Optional extra arguments for the test runner "
                            "(e.g. '-x --no-header' for pytest). Only used with pytest."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="analyze_failures",
            description=(
                "Parse raw test output and extract structured failure information. "
                "For each failure returns: test name, test file, line number, error type, "
                "error message, full traceback, source files involved, and a 'likely_fault' "
                "hint ('source' if the bug is probably in production code, 'test' if the "
                "test itself is wrong, or 'unknown')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "framework": {
                        "type": "string",
                        "description": "The test framework that produced the output: 'pytest' or 'node'.",
                    },
                    "test_output": {
                        "type": "string",
                        "description": "The raw test output to analyze (copy the full output from run_tests).",
                    },
                },
                "required": ["framework", "test_output"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    workspace = os.path.abspath(WORKSPACE)

    # ── discover_tests ──
    if name == "discover_tests":
        result = discover_all(workspace)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── run_tests ──
    if name == "run_tests":
        framework = arguments.get("framework")
        test_path = arguments.get("test_path")
        extra_args = arguments.get("extra_args", "")

        results = []

        if framework:
            fw = framework.lower().strip()
            if fw in ("pytest", "python"):
                results.append({
                    "framework": "pytest",
                    **run_python_tests(workspace, test_path, extra_args),
                })
            elif fw in ("node", "nodejs", "npm", "node.js"):
                results.append({
                    "framework": "node",
                    **run_node_tests(workspace, test_path),
                })
            else:
                return [TextContent(type="text", text=json.dumps(
                    {"error": f"Unknown framework: {framework}",
                     "supported": ["pytest", "node"]}, indent=2))]
        else:
            # Run all detected frameworks
            info = discover_all(workspace)
            for fw_info in info["frameworks"]:
                if not fw_info["detected"]:
                    continue
                fw_name = fw_info["framework"]
                if fw_name == "pytest":
                    results.append({
                        "framework": "pytest",
                        **run_python_tests(workspace, test_path, extra_args),
                    })
                elif fw_name == "node":
                    results.append({
                        "framework": "node",
                        **run_node_tests(workspace, test_path),
                    })

            if not results:
                return [TextContent(type="text", text=json.dumps(
                    _skip("No test frameworks detected in the workspace"), indent=2))]

        return [TextContent(type="text", text=json.dumps(
            {"results": results}, indent=2))]

    # ── analyze_failures ──
    if name == "analyze_failures":
        framework = arguments.get("framework", "")
        test_output = arguments.get("test_output", "")
        if not framework or not test_output:
            return [TextContent(type="text", text=json.dumps(
                {"error": "Both 'framework' and 'test_output' are required"}, indent=2))]
        result = analyze_test_output(framework, test_output)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ─────────────────────────────────────────────────────────────────────
# 7. Server entry point
# ─────────────────────────────────────────────────────────────────────


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
