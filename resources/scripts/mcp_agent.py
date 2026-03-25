#!/usr/bin/env python3
"""
MCP AI Agent — Agente autónomo que usa MCP servers (SonarQube, Filesystem,
GitHub) para descubrir issues, leer/corregir código, y crear PRs.

Uso:
    python mcp_agent.py --repo owner/repo --source-branch main --workspace /path

    python mcp_agent.py --repo owner/repo --source-branch main --dry-run
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

# ---------------------------------------------------------------------------
# MCP SDK imports
# ---------------------------------------------------------------------------
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ---------------------------------------------------------------------------
# 1. MCP Server definitions
# ---------------------------------------------------------------------------

def build_server_configs(workspace, sonarqube_url, sonarqube_token, sonarqube_project_key,
                         github_token):
    """Define the MCP servers to connect to (same ones as in VS Code mcp.json)."""
    servers = {}

    def _normalize_url_for_docker(raw_url):
        """Map localhost-style URLs to a host reachable from inside Docker."""
        try:
            parsed = urlparse(raw_url)
            host = parsed.hostname or ""
            if host in ("localhost", "127.0.0.1", "sonarqube"):
                netloc = parsed.netloc.replace(host, "host.docker.internal", 1)
                mapped = urlunparse((
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                ))
                log(f"ℹ️  Rewriting SONARQUBE_URL for Docker: {raw_url} -> {mapped}")
                return mapped
        except Exception:
            # Keep original URL if parsing fails.
            return raw_url
        return raw_url

    # --- SonarQube MCP Server (Docker) ---
    if sonarqube_url and sonarqube_token:
        docker_sonarqube_url = _normalize_url_for_docker(sonarqube_url)
        servers["sonarqube"] = StdioServerParameters(
            command="docker",
            args=[
                "run", "-i", "--rm", "--init",
                "--pull=always",
                "--add-host", "host.docker.internal:host-gateway",
                "-e", "SONARQUBE_TOKEN",
                "-e", "SONARQUBE_URL",
                "mcp/sonarqube",
            ],
            env={
                **os.environ,
                "SONARQUBE_TOKEN": sonarqube_token,
                "SONARQUBE_URL": docker_sonarqube_url,
            },
        )
    else:
        log("⚠️  SonarQube credentials not provided, skipping SonarQube MCP server")

    # --- Filesystem MCP Server (npx) ---
    servers["filesystem"] = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", workspace],
        env={**os.environ},
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

    return servers


# ---------------------------------------------------------------------------
# 2. MCP Client — connect to servers and discover tools
# ---------------------------------------------------------------------------

async def connect_servers(server_configs, exit_stack):
    """Connect to all MCP servers and return {name: session} + merged tool list."""
    sessions = {}
    all_tools_by_name = {}
    tool_owner = {}
    tool_to_session = {}

    # Higher number means higher priority when two servers expose same tool name.
    server_priority = {
        "github": 30,
        "sonarqube": 20,
        "filesystem": 10,
    }

    for name, params in server_configs.items():
        log(f"🔌 Connecting to MCP server: {name}...")
        try:
            stdio_transport = await exit_stack.enter_async_context(
                stdio_client(params)
            )
            read_stream, write_stream = stdio_transport
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            sessions[name] = session

            # Discover tools from this server
            response = await session.list_tools()
            server_tools = response.tools
            log(f"   ✅ {name}: {len(server_tools)} tools discovered")
            for tool in server_tools:
                existing_owner = tool_owner.get(tool.name)
                if existing_owner is None:
                    log(f"      - {tool.name}")
                    all_tools_by_name[tool.name] = tool
                    tool_owner[tool.name] = name
                    tool_to_session[tool.name] = session
                    continue

                current_prio = server_priority.get(existing_owner, 0)
                new_prio = server_priority.get(name, 0)

                if new_prio > current_prio:
                    log(
                        f"      - {tool.name} (replacing {existing_owner} with {name} due to higher priority)"
                    )
                    all_tools_by_name[tool.name] = tool
                    tool_owner[tool.name] = name
                    tool_to_session[tool.name] = session
                else:
                    log(
                        f"      - {tool.name} (skipping duplicate from {name}; keeping {existing_owner})"
                    )
        except Exception as e:
            log(f"   ❌ Failed to connect to {name}: {e}")

    all_tools = list(all_tools_by_name.values())
    return sessions, all_tools, tool_to_session


# ---------------------------------------------------------------------------
# 3. Convert MCP tools to litellm/OpenAI function-calling format
# ---------------------------------------------------------------------------

def mcp_tools_to_openai_format(mcp_tools):
    """Convert MCP tool definitions to OpenAI function-calling schema."""
    openai_tools = []
    for tool in mcp_tools:
        schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": schema,
            },
        })
    return openai_tools


# ---------------------------------------------------------------------------
# 4. Agent loop — LLM reasons and calls tools iteratively
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an autonomous AI agent running inside a CI/CD pipeline.
Your goal is to fix code quality issues and create a Pull Request with the fixes.

You have access to MCP tools connected to:
- **SonarQube**: Query code issues, security hotspots, and quality metrics
- **Filesystem**: Read and write files in the project workspace
- **GitHub**: Create branches, push files, and create pull requests

## Workflow
1. **Discover issues**: Use SonarQube tools to find issues in the project.
   - Use `search_sonar_issues_in_projects` with the project key to get open issues.
   - Focus on OPEN issues with HIGH or BLOCKER severity first, then MEDIUM.
2. **Read affected files**: Use filesystem tools to read the source files that have issues.
   - Use `read_text_file` to read each affected file.
3. **Fix the code**: Analyze each issue and determine the fix.
   - Use `edit_file` to apply precise fixes (preferred), or `write_file` for full rewrites.
   - Fix ALL issues in a file before moving to the next file.
   - Preserve coding style, indentation, and comments.
   - Do NOT change logic unless required to fix an issue.
4. **Create a PR**: Once all fixes are applied:
   - Use `create_branch` to create a new branch named `ai-fix/{source_branch}-{timestamp}`.
   - Use `push_files` to push ALL modified files in a single commit.
   - Use `create_pull_request` to open a PR with a descriptive body listing all fixed issues.

## Rules
- Fix only the issues reported by SonarQube. Do not refactor or improve unrelated code.
- If a file has no fixable issues, skip it.
- If you're unsure about a fix, skip that issue and note it in the PR body.
- The PR body should list every issue you fixed, grouped by file.
- Include a warning that changes should be reviewed before merging.
- Always use the exact file paths as reported by SonarQube (relative to project root).
"""


async def run_agent_loop(tool_to_session, openai_tools, model, system_prompt,
                         repo_slug, source_branch, sonarqube_project_key,
                         max_iterations=25, dry_run=False):
    """Run the agent reasoning loop: LLM decides → call tool → observe → repeat."""
    import litellm

    owner, repo = repo_slug.split("/", 1)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    # Initial user message with context
    user_message = f"""Fix all code quality issues in the project and create a Pull Request.

Context:
- SonarQube project key: `{sonarqube_project_key}`
- GitHub repository: `{owner}/{repo}`
- Source branch: `{source_branch}`
- Branch for fixes: `ai-fix/{source_branch}-{timestamp}`
- Timestamp: {timestamp}
- Dry run: {dry_run}

{"NOTE: This is a DRY RUN. Do NOT create branches, push files, or create pull requests. Only discover issues and propose fixes." if dry_run else "Proceed with the full workflow: discover issues, fix files, and create a PR."}

Start by querying SonarQube for open issues in the project."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for iteration in range(1, max_iterations + 1):
        log(f"\n{'─' * 50}")
        log(f"🔄 Iteration {iteration}/{max_iterations}")
        log(f"{'─' * 50}")

        # Call LLM with tools
        try:
            response = litellm.completion(
                model=model,
                messages=messages,
                tools=openai_tools if openai_tools else None,
                temperature=0.1,
            )
        except Exception as e:
            log(f"❌ LLM call failed: {e}")
            break

        choice = response.choices[0]
        message = choice.message

        # Add assistant message to history
        messages.append(message.model_dump())

        # If no tool calls, the agent is done
        if not message.tool_calls:
            log(f"\n🏁 Agent finished reasoning.")
            if message.content:
                log(f"📝 Final message:\n{message.content}")
            break

        # Process each tool call
        for tool_call in message.tool_calls:
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            log(f"\n🔧 Tool call: {func_name}")
            log(f"   Args: {json.dumps(func_args, indent=2)[:500]}")

            if dry_run and func_name in (
                "create_branch", "push_files", "create_pull_request",
                "create_or_update_file", "write_file",
            ):
                result_text = f"[DRY RUN] Skipped {func_name} — would have been called with: {json.dumps(func_args)[:300]}"
                log(f"   🏜️  {result_text}")
            else:
                # Dispatch to the correct MCP server session
                session = tool_to_session.get(func_name)
                if not session:
                    result_text = f"Error: Unknown tool '{func_name}'. Available tools: {list(tool_to_session.keys())}"
                    log(f"   ❌ {result_text}")
                else:
                    try:
                        result = await session.call_tool(func_name, func_args)
                        # Extract text content from MCP result
                        if result.content:
                            result_text = "\n".join(
                                item.text for item in result.content
                                if hasattr(item, "text")
                            )
                        else:
                            result_text = "(empty result)"

                        # Truncate very long results for logging
                        log_preview = result_text[:800] + ("..." if len(result_text) > 800 else "")
                        log(f"   ✅ Result ({len(result_text)} chars): {log_preview}")
                    except Exception as e:
                        result_text = f"Error calling {func_name}: {e}"
                        log(f"   ❌ {result_text}")

            # Add tool result to message history
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text,
            })

    else:
        log(f"\n⚠️  Max iterations ({max_iterations}) reached. Agent stopped.")

    return messages


# ---------------------------------------------------------------------------
# 5. Utility
# ---------------------------------------------------------------------------

def log(msg):
    """Print to stderr (Jenkins captures stdout for artifacts)."""
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------

async def async_main(args):
    workspace = os.path.abspath(args.workspace)
    model = os.environ.get("LLM_MODEL", "gemini-3.1-pro-preview")
    github_token = os.environ.get("Github_AI_Auth", "")
    sonarqube_url = os.environ.get("SONARQUBE_URL", "")
    sonarqube_token = os.environ.get("SONARQUBE_TOKEN", "")
    sonarqube_project_key = os.environ.get("SONARQUBE_EFFECTIVE_PROJECT_KEY","")

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

    # --- Build server configs ---
    server_configs = build_server_configs(
        workspace=workspace,
        sonarqube_url=sonarqube_url,
        sonarqube_token=sonarqube_token,
        sonarqube_project_key=sonarqube_project_key,
        github_token=github_token,
    )

    if not server_configs:
        log("❌ No MCP servers configured. Cannot proceed.")
        sys.exit(1)

    # --- Connect to MCP servers ---
    log("\n📡 Connecting to MCP servers...")
    async with AsyncExitStack() as exit_stack:
        sessions, all_tools, tool_to_session = await connect_servers(
            server_configs, exit_stack
        )

        if "sonarqube" not in sessions:
            log("❌ SonarQube MCP server did not connect. Check SONARQUBE_URL reachability from Docker.")
            log("   Tip: if SonarQube runs on Jenkins host, use SONARQUBE_URL=http://host.docker.internal:9000")
            sys.exit(1)

        if not all_tools:
            log("❌ No tools discovered from any MCP server. Cannot proceed.")
            sys.exit(1)

        log(f"\n📋 Total tools available: {len(all_tools)}")

        # Convert to OpenAI format for litellm
        openai_tools = mcp_tools_to_openai_format(all_tools)

        # --- Build system prompt with context ---
        system_prompt = SYSTEM_PROMPT + f"""

## Environment details
- Workspace path (for filesystem tools): {workspace}
- SonarQube project key: {sonarqube_project_key}
- GitHub owner: {args.repo.split('/')[0]}
- GitHub repo: {args.repo.split('/')[1]}
- Source branch: {args.source_branch}
"""

        # --- Run agent loop ---
        log("\n🚀 Starting agent loop...")
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

        log(f"\n✅ Agent completed. Total messages exchanged: {len(messages)}")


def main():
    parser = argparse.ArgumentParser(description="MCP AI Agent — Fix code issues using MCP tools")
    parser.add_argument("--repo", required=True, help="GitHub repo slug (owner/repo)")
    parser.add_argument("--source-branch", required=True, help="Current branch name")
    parser.add_argument("--workspace", default=".", help="Path to the workspace root")
    parser.add_argument("--max-iterations", type=int, default=25, help="Max agent loop iterations")
    parser.add_argument("--dry-run", action="store_true", help="Discover issues but skip git/PR operations")
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
