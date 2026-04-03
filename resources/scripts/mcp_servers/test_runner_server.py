#!/usr/bin/env python3
"""
MCP Server ---- Test Runner (config-driven)

Expone 3 tools MCP:
  - discover_tests   : lee .ai-tests.json y devuelve la configuracion de suites
  - run_tests        : ejecuta las suites definidas en .ai-tests.json
  - analyze_failures : parsea el output de tests fallidos y clasifica la culpa

El proyecto debe contener un archivo ``.ai-tests.json`` en la raiz del workspace
(o en la ruta indicada por AI_TEST_CONFIG_FILE) con la configuracion de test suites.

Formato de ``.ai-tests.json``::

    {
      "test_suites": [
        {
          "name": "unit-tests",
          "command": "python -m pytest tests/ --tb=long -v",
          "framework": "pytest",
          "setup": "pip install -r requirements-test.txt",
          "test_dir": "tests/",
          "timeout": 120
        }
      ]
    }

Transporte: stdio (el agente lo lanza como subproceso).

Uso:
    WORKSPACE_ROOT=/path python test_runner_server.py
"""

import json
import os
import re
import shlex
import subprocess
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# --------------------------------------------------------------------------
# 1. Constants & Config
# --------------------------------------------------------------------------

server = Server("test-runner")

WORKSPACE = os.environ.get("WORKSPACE_ROOT", ".")

DEFAULT_CONFIG_FILENAME = ".ai-tests.json"
DEFAULT_TIMEOUT = 120
SUPPORTED_FRAMEWORKS = ("pytest", "node", "generic")
PYTEST_EXECUTABLES = {"pytest", "python", "python3", "py"}
NODE_EXECUTABLES = {"node", "nodejs", "npm", "npx"}
SETUP_EXECUTABLES = PYTEST_EXECUTABLES | NODE_EXECUTABLES | {"pip", "pip3"}
UNSAFE_EXTRA_ARG_MARKERS = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r")

# Suffixes used by _is_test_file heuristic (kept for analyze_failures)
_NODE_TEST_SUFFIXES = (
    ".test.js", ".spec.js", ".test.mjs", ".spec.mjs",
    ".test.cjs", ".spec.cjs", ".test.ts", ".spec.ts",
)

# --------------------------------------------------------------------------
# 2. Helpers
# --------------------------------------------------------------------------


def _strip_wrapping_quotes(token):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _split_command(command):
    posix = os.name != "nt"
    return [_strip_wrapping_quotes(token) for token in shlex.split(command, posix=posix)]


def _prepare_command(command, extra_args=""):
    tokens = _split_command(command)
    if not tokens:
        raise ValueError("Command cannot be empty")

    env_updates = {}
    argv = []
    assignment_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

    for token in tokens:
        if not argv and assignment_pattern.match(token):
            key, value = token.split("=", 1)
            env_updates[key] = value
            continue
        argv.append(token)

    if not argv:
        raise ValueError("Command must include an executable")

    if extra_args:
        _validate_extra_args(extra_args)
        argv.extend(_split_command(extra_args))

    return argv, env_updates


def _validate_extra_args(extra_args):
    for marker in UNSAFE_EXTRA_ARG_MARKERS:
        if marker in extra_args:
            raise ValueError("extra_args contains unsafe shell-like tokens")


def _validate_path_inside_workspace(workspace, raw_path, *, label):
    normalized_path = os.path.abspath(os.path.join(workspace, raw_path))
    workspace_root = os.path.abspath(workspace)
    if os.path.commonpath([workspace_root, normalized_path]) != workspace_root:
        raise ValueError(f"{label} must stay inside the workspace")


def _validate_suite_security(workspace, suite, path):
    name = suite["name"]
    framework = suite.get("framework", "generic")
    command_argv, _ = _prepare_command(suite["command"])
    executable = os.path.basename(command_argv[0]).lower()

    if framework == "pytest" and executable not in PYTEST_EXECUTABLES:
        raise ValueError(
            f"{path}: suite '{name}' must use an approved executable for framework 'pytest': {sorted(PYTEST_EXECUTABLES)}"
        )
    if framework == "node" and executable not in NODE_EXECUTABLES:
        raise ValueError(
            f"{path}: suite '{name}' must use an approved executable for framework 'node': {sorted(NODE_EXECUTABLES)}"
        )

    setup_cmd = suite.get("setup")
    if setup_cmd:
        setup_argv, _ = _prepare_command(setup_cmd)
        setup_executable = os.path.basename(setup_argv[0]).lower()
        if setup_executable not in SETUP_EXECUTABLES:
            raise ValueError(
                f"{path}: suite '{name}' setup must use an approved executable: {sorted(SETUP_EXECUTABLES)}"
            )

    test_dir = suite.get("test_dir")
    if isinstance(test_dir, str) and test_dir.strip():
        _validate_path_inside_workspace(workspace, test_dir, label=f"{path}: suite '{name}' test_dir")


def _run_command(cmd, cwd=None, timeout=120, env=None):
    """Run a command and return a result dict."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or WORKSPACE,
            timeout=timeout,
            shell=False,
            env=env,
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


def _skip(message):
    return {"returncode": 0, "output": message, "passed": False,
            "status": "skipped", "skipped": True}


# --------------------------------------------------------------------------
# 3. Config loading & validation
# --------------------------------------------------------------------------


def _config_path(workspace):
    """Return the absolute path to the test config file."""
    custom = os.environ.get("AI_TEST_CONFIG_FILE", "").strip()
    if custom:
        if os.path.isabs(custom):
            return custom
        return os.path.join(workspace, custom)
    return os.path.join(workspace, DEFAULT_CONFIG_FILENAME)


def _load_config(workspace):
    """Load and validate .ai-tests.json.  Returns parsed dict or None."""
    path = _config_path(workspace)
    if not os.path.isfile(path):
        return None

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # --- schema validation ---
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")

    suites = data.get("test_suites")
    if not isinstance(suites, list) or len(suites) == 0:
        raise ValueError(f"{path}: 'test_suites' must be a non-empty array")

    seen_names = set()
    for idx, suite in enumerate(suites):
        if not isinstance(suite, dict):
            raise ValueError(f"{path}: test_suites[{idx}] must be an object")

        name = suite.get("name")
        if not name or not isinstance(name, str):
            raise ValueError(f"{path}: test_suites[{idx}] must have a string 'name'")
        if name in seen_names:
            raise ValueError(f"{path}: duplicate suite name '{name}'")
        seen_names.add(name)

        command = suite.get("command")
        if not command or not isinstance(command, str):
            raise ValueError(f"{path}: suite '{name}' must have a string 'command'")

        fw = suite.get("framework", "generic")
        if fw not in SUPPORTED_FRAMEWORKS:
            raise ValueError(
                f"{path}: suite '{name}' has unsupported framework '{fw}'. "
                f"Supported: {SUPPORTED_FRAMEWORKS}"
            )

        timeout = suite.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError(f"{path}: suite '{name}' timeout must be a positive number")

        _validate_suite_security(workspace, suite, path)

    return data


# --------------------------------------------------------------------------
# 4. Discovery (config-driven)
# --------------------------------------------------------------------------


def discover_tests(workspace):
    """Read .ai-tests.json and return its contents."""
    try:
        config = _load_config(workspace)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"configured": False, "error": str(exc)}

    if config is None:
        return {
            "configured": False,
            "message": (
                f"No test configuration found. Create a '{DEFAULT_CONFIG_FILENAME}' "
                "file in the project root to define how tests should be run. "
                "See the tool description for the expected format."
            ),
        }

    suites_summary = []
    for suite in config["test_suites"]:
        suites_summary.append({
            "name": suite["name"],
            "framework": suite.get("framework", "generic"),
            "command": suite["command"],
            "setup": suite.get("setup"),
            "test_dir": suite.get("test_dir"),
            "timeout": suite.get("timeout", DEFAULT_TIMEOUT),
        })

    return {
        "configured": True,
        "config_file": _config_path(workspace),
        "test_suites": suites_summary,
    }


# --------------------------------------------------------------------------
# 5. Execution (config-driven)
# --------------------------------------------------------------------------


def run_suite(workspace, suite, extra_args=""):
    """Run a single test suite: setup (if any) then command."""
    name = suite["name"]
    framework = suite.get("framework", "generic")
    timeout = suite.get("timeout", DEFAULT_TIMEOUT)
    result_base = {"name": name, "framework": framework}

    # --- setup step ---
    setup_cmd = suite.get("setup")
    if setup_cmd:
        try:
            setup_argv, setup_env_updates = _prepare_command(setup_cmd)
        except ValueError as exc:
            return {
                **result_base,
                "returncode": -1,
                "output": f"[setup invalid]\n{exc}",
                "passed": False,
                "status": "error",
            }

        setup_result = _run_command(
            setup_argv,
            cwd=workspace,
            timeout=timeout,
            env={**os.environ, **setup_env_updates},
        )
        if setup_result["returncode"] != 0:
            return {
                **result_base,
                "returncode": setup_result["returncode"],
                "output": f"[setup failed]\n{setup_result['output']}",
                "passed": False,
                "status": "setup_failed",
            }

    # --- test command ---
    try:
        command_argv, command_env_updates = _prepare_command(suite["command"], extra_args)
    except ValueError as exc:
        return {
            **result_base,
            "returncode": -1,
            "output": str(exc),
            "passed": False,
            "status": "error",
        }

    test_result = _run_command(
        command_argv,
        cwd=workspace,
        timeout=timeout,
        env={**os.environ, **command_env_updates},
    )

    return {
        **result_base,
        "returncode": test_result["returncode"],
        "output": test_result["output"],
        "passed": test_result["passed"],
        "status": test_result["status"],
    }


def run_tests(workspace, suite_name=None, extra_args=""):
    """Run test suites from .ai-tests.json."""
    try:
        config = _load_config(workspace)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": str(exc)}

    if config is None:
        return _skip(
            f"No test configuration found ({DEFAULT_CONFIG_FILENAME}). "
            "Tests cannot be run without explicit configuration."
        )

    suites = config["test_suites"]

    if suite_name:
        matched = [s for s in suites if s["name"] == suite_name]
        if not matched:
            available = [s["name"] for s in suites]
            return {
                "error": f"Suite '{suite_name}' not found. Available: {available}"
            }
        suites = matched

    results = []
    for suite in suites:
        results.append(run_suite(workspace, suite, extra_args))

    return {"results": results}


# --------------------------------------------------------------------------
# 6. Analysis --- parse test output into structured failures
# --------------------------------------------------------------------------

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


def _is_test_file(filepath):
    """Heuristic: does the path look like a test file?"""
    base = os.path.basename(filepath)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    for suffix in _NODE_TEST_SUFFIXES:
        if base.endswith(suffix):
            return True
    parts = filepath.replace("\\", "/").split("/")
    for part in parts:
        if part in ("tests", "test", "__tests__"):
            return True
    return False


def classify_fault(failure):
    """Heuristic to decide if the failure is likely caused by source or test.

    Returns "source", "test", or "unknown".
    """
    source_frames = failure.get("source_frames", [])
    error_type = failure.get("error_type", "")

    if source_frames:
        return "source"

    if error_type in ("AssertionError", "AssertionError", "AssertError"):
        return "test"

    return "unknown"


def parse_pytest_failures(output):
    """Parse verbose pytest output into a list of structured failure dicts."""
    failures = []
    lines = output.splitlines()

    i = 0
    while i < len(lines):
        header_match = _RE_PYTEST_FAILURE_HEADER.match(lines[i])
        if not header_match:
            i += 1
            continue

        test_id = header_match.group(1).strip()
        i += 1

        tb_lines = []
        while i < len(lines):
            if _RE_PYTEST_FAILURE_HEADER.match(lines[i]):
                break
            if lines[i].startswith("="):
                break
            tb_lines.append(lines[i])
            i += 1

        traceback_text = "\n".join(tb_lines)

        all_frames = []
        for line in tb_lines:
            fm = _RE_PYTEST_FILE_LINE.match(line)
            if fm:
                all_frames.append({
                    "file": fm.group(1),
                    "line": int(fm.group(2)),
                    "function": fm.group(3),
                })

        error_type = ""
        error_message = ""
        for line in tb_lines:
            em = _RE_PYTEST_ERROR_LINE.match(line)
            if em:
                error_type = em.group(1)
                error_message = em.group(2).strip()
                break

        source_frames = [f for f in all_frames if not _is_test_file(f["file"])]
        test_frames = [f for f in all_frames if _is_test_file(f["file"])]

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


def parse_node_failures(output):
    """Parse Node.js test runner (TAP) or npm test output into structured failures."""
    failures = []
    lines = output.splitlines()

    for idx, line in enumerate(lines):
        m = _RE_NODE_NOT_OK.match(line.strip())
        if not m:
            continue

        test_name = m.group(1).strip()

        ctx_lines = []
        j = idx + 1
        while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip() == ""):
            ctx_lines.append(lines[j])
            j += 1
        context_text = "\n".join(ctx_lines)

        error_message = ""
        em = _RE_NODE_ERROR_LINE.search(context_text)
        if em:
            error_message = em.group(1).strip()

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


def parse_generic_failures(output):
    """Fallback parser: return the raw output as a single failure block."""
    if not output.strip():
        return []
    return [{
        "test_name": "(unknown)",
        "test_file": "",
        "test_line": 0,
        "error_type": "",
        "error_message": "",
        "traceback": output.strip(),
        "source_frames": [],
        "likely_fault": "unknown",
    }]


def analyze_test_output(framework, test_output):
    """Dispatch to the right parser and return structured failure info."""
    framework = framework.lower().strip()
    if framework in ("pytest", "python"):
        parsed = parse_pytest_failures(test_output)
    elif framework in ("node", "nodejs", "npm", "node.js"):
        parsed = parse_node_failures(test_output)
    elif framework == "generic":
        parsed = parse_generic_failures(test_output)
    else:
        return {"error": f"Unknown framework: {framework}",
                "supported": ["pytest", "node", "generic"]}

    return {
        "framework": framework,
        "total_failures": len(parsed),
        "failures": parsed,
    }


# --------------------------------------------------------------------------
# 7. MCP Tool definitions
# --------------------------------------------------------------------------

CONFIG_FORMAT_HELP = """
Expected format of .ai-tests.json:
{
  "test_suites": [
    {
      "name": "unit-tests",
      "command": "python -m pytest tests/ --tb=long -v",
      "framework": "pytest",
      "setup": "pip install -r requirements-test.txt",
      "test_dir": "tests/",
      "timeout": 120
    }
  ]
}
Fields:
  - name (required): unique identifier for the suite
  - command (required): shell command to execute the tests
  - framework (optional, default "generic"): output parser -- "pytest", "node", or "generic"
  - setup (optional): shell command to install dependencies before running tests
  - test_dir (optional): directory where test files live (informational)
  - timeout (optional, default 120): max seconds before killing the test process
""".strip()


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="discover_tests",
            description=(
                "Read the project's .ai-tests.json configuration file and return the list "
                "of defined test suites with their commands, frameworks, setup steps, and "
                "timeouts. Returns configured=false if no config file exists.\n\n"
                + CONFIG_FORMAT_HELP
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="run_tests",
            description=(
                "Execute test suites as defined in .ai-tests.json. For each suite, runs "
                "the optional 'setup' command first, then the 'command'. Returns exit code, "
                "pass/fail status, and full output. Use 'analyze_failures' on the output "
                "to get structured failure data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "suite": {
                        "type": "string",
                        "description": (
                            "Name of a specific test suite to run (must match a 'name' "
                            "in .ai-tests.json). If omitted, runs all suites."
                        ),
                    },
                    "extra_args": {
                        "type": "string",
                        "description": (
                            "Optional extra arguments appended to the test command "
                            "(e.g. '-x --no-header')."
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
                "test itself is wrong, or 'unknown'). Use framework='generic' if the "
                "test runner is not pytest or node."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "framework": {
                        "type": "string",
                        "description": "The test framework that produced the output: 'pytest', 'node', or 'generic'.",
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
async def call_tool(name, arguments):
    workspace = os.path.abspath(WORKSPACE)

    # -- discover_tests --
    if name == "discover_tests":
        result = discover_tests(workspace)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # -- run_tests --
    if name == "run_tests":
        suite_name = arguments.get("suite")
        extra_args = arguments.get("extra_args", "")
        result = run_tests(workspace, suite_name, extra_args)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # -- analyze_failures --
    if name == "analyze_failures":
        framework = arguments.get("framework", "")
        test_output = arguments.get("test_output", "")
        if not framework or not test_output:
            return [TextContent(type="text", text=json.dumps(
                {"error": "Both 'framework' and 'test_output' are required"}, indent=2))]
        result = analyze_test_output(framework, test_output)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# --------------------------------------------------------------------------
# 8. Server entry point
# --------------------------------------------------------------------------


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
