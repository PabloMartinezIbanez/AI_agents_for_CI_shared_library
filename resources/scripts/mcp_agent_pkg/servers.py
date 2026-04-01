import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from mcp import StdioServerParameters

from .env_config import resolve_reports_dir
from .logging_utils import log


def _normalize_url_for_docker(raw_url):
    """Map localhost-style URLs to a host reachable from inside Docker."""
    try:
        parsed = urlparse(raw_url)
        host = parsed.hostname or ""
        if host in ("localhost", "127.0.0.1", "sonarqube"):
            netloc = parsed.netloc.replace(host, "host.docker.internal", 1)
            mapped = urlunparse(
                (
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
            log(f"ℹ️  Rewriting SONARQUBE_URL for Docker: {raw_url} -> {mapped}")
            return mapped
    except Exception:
        # Keep original URL if parsing fails.
        return raw_url
    return raw_url


def build_server_configs(
    workspace,
    sonarqube_url,
    sonarqube_token,
    sonarqube_project_key,
    github_token,
):
    """Define the MCP servers to connect to (same ones as in VS Code mcp.json)."""
    servers = {}
    test_runner_script = Path(workspace) / ".ai_fixer" / "mcp_servers" / "test_runner_server.py"
    reports_dir = resolve_reports_dir(workspace)

    # --- SonarQube MCP Server (Docker) ---
    sonarqube_container = f"mcp-sonarqube-{os.getpid()}"
    if sonarqube_url and sonarqube_token:
        docker_sonarqube_url = _normalize_url_for_docker(sonarqube_url)
        servers["sonarqube"] = StdioServerParameters(
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "--init",
                "--pull=always",
                "--name",
                sonarqube_container,
                "--add-host",
                "host.docker.internal:host-gateway",
                "-e",
                "SONARQUBE_TOKEN",
                "-e",
                "SONARQUBE_URL",
                "mcp/sonarqube",
            ],
            env={
                **os.environ,
                "SONARQUBE_TOKEN": sonarqube_token,
                "SONARQUBE_URL": docker_sonarqube_url,
            },
        )
    else:
        sonarqube_container = None
        log("⚠️  SonarQube credentials not provided, skipping SonarQube MCP server")

    # --- Filesystem MCP Server (npx) ---
    servers["filesystem"] = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", workspace],
        env={**os.environ},
    )

    # --- Test Runner MCP Server (local Python) ---
    if test_runner_script.is_file():
        test_runner_env = {
            **os.environ,
            "WORKSPACE_ROOT": workspace,
            "AGENT_REPORTS_DIR": reports_dir,
        }
        # Forward custom config path if set by pipeline
        ai_test_config = os.environ.get("AI_TEST_CONFIG_FILE", "")
        if ai_test_config:
            test_runner_env["AI_TEST_CONFIG_FILE"] = ai_test_config
        servers["test_runner"] = StdioServerParameters(
            command=sys.executable,
            args=[str(test_runner_script)],
            env=test_runner_env,
        )
    else:
        log(
            f"⚠️  Test Runner MCP server not found at {test_runner_script}, "
            "skipping Test Runner MCP server"
        )

    # --- GitHub MCP Server (npx) ---
    if github_token:
        servers["github"] = StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={
                **os.environ,
                "GITHUB_PERSONAL_ACCESS_TOKEN": github_token,
            },
        )
    else:
        log("⚠️  GitHub token not provided, skipping GitHub MCP server")

    return servers, sonarqube_container
