import argparse
import asyncio
import os
import sys
from contextlib import AsyncExitStack

from .agent_loop import SYSTEM_PROMPT, run_agent_loop
from .artifacts import persist_agent_artifacts
from .env_config import resolve_env_value, resolve_reports_dir
from .logging_utils import log
from .mcp_client import connect_servers, mcp_tools_to_openai_format
from .servers import build_server_configs


def _build_system_prompt(workspace, sonarqube_project_key, repo_slug, source_branch):
    owner, repo = repo_slug.split("/", 1)
    return SYSTEM_PROMPT + f"""

## Environment details
- Workspace path (for filesystem tools): {workspace}
- SonarQube project key: {sonarqube_project_key}
- GitHub owner: {owner}
- GitHub repo: {repo}
- Source branch: {source_branch}
"""


async def async_main(
    args,
    *,
    build_server_configs=build_server_configs,
    connect_servers=connect_servers,
    run_agent_loop=run_agent_loop,
    mcp_tools_to_openai_format=mcp_tools_to_openai_format,
    resolve_env_value=resolve_env_value,
    resolve_reports_dir=resolve_reports_dir,
    persist_agent_artifacts=persist_agent_artifacts,
    log=log,
):
    workspace = os.path.abspath(args.workspace)
    reports_dir = resolve_reports_dir(workspace)
    model = args.model or resolve_env_value("LLM_MODEL") or "gemini/gemini-2.0-flash"
    github_token = resolve_env_value("GITHUB_PERSONAL_ACCESS_TOKEN", "Github_AI_Auth")
    sonarqube_url = resolve_env_value("SONARQUBE_URL")
    sonarqube_token = resolve_env_value("SONARQUBE_TOKEN")
    sonarqube_project_key = resolve_env_value(
        "SONARQUBE_EFFECTIVE_PROJECT_KEY",
        "SONARQUBE_PROJECT_KEY",
    )

    log("=" * 60)
    log("🤖  MCP AI Agent")
    log(f"   Model:     {model}")
    log(f"   Repo:      {args.repo}")
    log(f"   Branch:    {args.source_branch}")
    log(f"   Workspace: {workspace}")
    log(f"   SonarQube: {sonarqube_url} (project: {sonarqube_project_key})")
    log(f"   Dry run:   {args.dry_run}")
    log(f"   Max iter:  {args.max_iterations}")
    log("=" * 60)

    if not sonarqube_project_key:
        log("❌ SONARQUBE_EFFECTIVE_PROJECT_KEY or SONARQUBE_PROJECT_KEY not set.")
        sys.exit(1)

    server_configs, sonarqube_container = build_server_configs(
        workspace=workspace,
        sonarqube_url=sonarqube_url,
        sonarqube_token=sonarqube_token,
        sonarqube_project_key=sonarqube_project_key,
        github_token=github_token,
    )

    if not server_configs:
        log("❌ No MCP servers configured. Cannot proceed.")
        sys.exit(1)

    log("\n📡 Connecting to MCP servers...")
    messages = []
    status = "failed"
    error_message = ""
    async with AsyncExitStack() as exit_stack:
        sessions, all_tools, tool_to_session = await connect_servers(
            server_configs, exit_stack
        )

        if "sonarqube" not in sessions:
            log(
                "❌ SonarQube MCP server did not connect. Check SONARQUBE_URL reachability from Docker."
            )
            log(
                "   Tip: if SonarQube runs on Jenkins host, use SONARQUBE_URL=http://host.docker.internal:9000"
            )
            sys.exit(1)

        if not all_tools:
            log("❌ No tools discovered from any MCP server. Cannot proceed.")
            sys.exit(1)

        allowed_tools = {
            "search_sonar_issues_in_projects",
            "show_rule",
            "read_text_file",
            "edit_file",
            "write_file",
            "read_multiple_files",
            "discover_tests",
            "run_tests",
            "analyze_failures",
            "create_branch",
            "push_files",
            "create_pull_request",
        }
        filtered_tools = [tool for tool in all_tools if tool.name in allowed_tools]
        log(f"   ✅ MCP tools loaded: {len(filtered_tools)}")

        openai_tools = mcp_tools_to_openai_format(filtered_tools)
        system_prompt = _build_system_prompt(
            workspace,
            sonarqube_project_key,
            args.repo,
            args.source_branch,
        )

        log("\n🚀 Starting agent loop...")
        try:
            messages = await run_agent_loop(
                tool_to_session=tool_to_session,
                openai_tools=openai_tools,
                model=model,
                system_prompt=system_prompt,
                repo_slug=args.repo,
                source_branch=args.source_branch,
                sonarqube_project_key=sonarqube_project_key,
                max_iterations=args.max_iterations,
                dry_run=args.dry_run,
            )
            status = "completed"
        except Exception as exc:
            error_message = str(exc)
            raise
        finally:
            persist_agent_artifacts(
                reports_dir,
                repo_slug=args.repo,
                source_branch=args.source_branch,
                workspace=workspace,
                model=model,
                dry_run=args.dry_run,
                max_iterations=args.max_iterations,
                messages=messages,
                status=status,
                error_message=error_message,
            )

        log(f"\n✅ Agent completed. Total messages exchanged: {len(messages)}")

        if sonarqube_container:
            log(f"\n🛑 Stopping SonarQube Docker container: {sonarqube_container}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "stop",
                    "--time",
                    "5",
                    sonarqube_container,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                log("   ✅ SonarQube container stopped gracefully")
            except Exception as e:
                log(f"   ⚠️  Could not stop SonarQube container: {e}")


def main(async_main_callable=async_main):
    parser = argparse.ArgumentParser(description="MCP AI Agent — Fix code issues using MCP tools")
    parser.add_argument("--repo", required=True, help="GitHub repo slug (owner/repo)")
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name for litellm (e.g. gemini/gemini-2.0-flash). Overrides LLM_MODEL env var.",
    )
    parser.add_argument("--source-branch", required=True, help="Current branch name")
    parser.add_argument("--workspace", default=".", help="Path to the workspace root")
    parser.add_argument("--max-iterations", type=int, default=25, help="Max agent loop iterations")
    parser.add_argument("--dry-run", action="store_true", help="Discover issues but skip git/PR operations")
    args = parser.parse_args()

    asyncio.run(async_main_callable(args))
