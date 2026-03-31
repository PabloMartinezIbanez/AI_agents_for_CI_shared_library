#!/usr/bin/env python3
"""
MCP AI Agent — Agente autónomo que usa MCP servers (SonarQube, Filesystem,
GitHub, Test Runner) para descubrir issues, leer/corregir código, validar
tests y crear PRs.

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
    test_runner_script = os.path.join(workspace, ".ai_fixer", "mcp_servers", "test_runner_server.py")

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
    sonarqube_container = f"mcp-sonarqube-{os.getpid()}"
    if sonarqube_url and sonarqube_token:
        docker_sonarqube_url = _normalize_url_for_docker(sonarqube_url)
        servers["sonarqube"] = StdioServerParameters(
            command="docker",
            args=[
                "run", "-i", "--rm", "--init",
                "--pull=always",
                "--name", sonarqube_container,
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
        sonarqube_container = None
        log("⚠️  SonarQube credentials not provided, skipping SonarQube MCP server")

    # --- Filesystem MCP Server (npx) ---
    servers["filesystem"] = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", workspace],
        env={**os.environ},
    )

    # --- Test Runner MCP Server (local Python) ---
    if os.path.isfile(test_runner_script):
        test_runner_env = {
            **os.environ,
            "WORKSPACE_ROOT": workspace,
        }
        # Forward custom config path if set by pipeline
        ai_test_config = os.environ.get("AI_TEST_CONFIG_FILE", "")
        if ai_test_config:
            test_runner_env["AI_TEST_CONFIG_FILE"] = ai_test_config
        servers["test_runner"] = StdioServerParameters(
            command=sys.executable,
            args=[test_runner_script],
            env=test_runner_env,
        )
    else:
        log(f"⚠️  Test Runner MCP server not found at {test_runner_script}, skipping Test Runner MCP server")

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


# ---------------------------------------------------------------------------
# 2. MCP Client — connect to servers and discover tools
# ---------------------------------------------------------------------------

async def connect_servers(server_configs, exit_stack):
    """Connect to all MCP servers **in parallel** and return {name: session} + merged tool list."""

    # Higher number means higher priority when two servers expose same tool name.
    server_priority = {
        "github": 30,
        "sonarqube": 20,
        "test_runner": 15,
        "filesystem": 10,
    }

    async def _connect_one(name, params):
        """Connect to a single MCP server, returning its session and tools."""
        log(f"🔌 Connecting to MCP server: {name}...")
        per_stack = AsyncExitStack()
        try:
            errlog = open(os.devnull, "w") if name == "sonarqube" else sys.stderr
            stdio_transport = await per_stack.enter_async_context(
                stdio_client(params, errlog=errlog)
            )
            read_stream, write_stream = stdio_transport
            session = await per_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            response = await session.list_tools()
            log(f"   ✅ {name}: {len(response.tools)} tools discovered")
            return (name, per_stack, session, response.tools)
        except Exception as e:
            log(f"   ❌ Failed to connect to {name}: {e}")
            await per_stack.aclose()
            return (name, None, None, None)

    # Launch all server connections in parallel
    results = await asyncio.gather(
        *[_connect_one(name, params) for name, params in server_configs.items()]
    )

    # Merge results sequentially (fast — just dict operations)
    sessions = {}
    all_tools_by_name = {}
    tool_owner = {}
    tool_to_session = {}

    for name, per_stack, session, server_tools in results:
        if session is None:
            continue
        # Transfer ownership of per-server stack to parent for cleanup
        exit_stack.push_async_callback(per_stack.aclose)
        sessions[name] = session

        for tool in server_tools:
            existing_owner = tool_owner.get(tool.name)
            if existing_owner is None:
                all_tools_by_name[tool.name] = tool
                tool_owner[tool.name] = name
                tool_to_session[tool.name] = session
                continue

            current_prio = server_priority.get(existing_owner, 0)
            new_prio = server_priority.get(name, 0)

            if new_prio > current_prio:
                all_tools_by_name[tool.name] = tool
                tool_owner[tool.name] = name
                tool_to_session[tool.name] = session

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
- **Test Runner**: Run tests defined in the project's `.ai-tests.json` config and analyze failures
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
4. **Validate the changes**: Before creating a PR, run available tests.
    - Call `discover_tests` to load the project's `.ai-tests.json` test configuration.
      This file defines test suites with their commands, setup steps, and frameworks.
    - If `configured` is true, call `run_tests` to execute all configured suites
      (or a specific one by name using the `suite` parameter).
    - If `configured` is false, note that no tests are configured and skip validation.
    - If any tests fail, call `analyze_failures` with the raw output and the suite's
      `framework` value to get structured failure data including test name, file, line,
      error type, and a `likely_fault` hint.
    - If `likely_fault` is `"source"`, the bug is in the production code you changed — fix it.
    - If `likely_fault` is `"test"`, the test itself may be outdated or wrong — fix the test.
    - Re-run tests after each fix until all pass (or you're confident remaining failures
      are pre-existing and unrelated to your changes).
    - Include the validation outcome in your final summary, even if no tests are configured.
5. **Create a PR**: Once all fixes are applied:
    - Use `create_branch` to create a new branch named `ai-fix/{source_branch}-{date}` (date = YYYYMMDD), explicitly setting `from_branch` to `{source_branch}`.
   - Use `push_files` to push ALL modified files in a single commit.
    - Use `create_pull_request` to open a PR. The title MUST follow this exact format:
      `[AI Fix][{source_branch}] {N} issue(s) fixed — {date}`
      Where: `{N}` = total number of issues fixed, `{date}` = date tag (YYYY-MM-DD). Example:
      `[AI Fix][main] 5 issue(s) fixed — 2026-03-26`

## Rules
- Fix only the issues reported by SonarQube. Do not refactor or improve unrelated code.
- If a file has no fixable issues, skip it.
- If you're unsure about a fix, skip that issue and note it in the PR body.
- The PR body should list every issue you fixed, grouped by file.
- If tests can be executed, run them before creating the PR and summarize the result.
- Include a warning that changes should be reviewed before merging.
- Always use the exact file paths as reported by SonarQube (relative to project root).
"""


async def run_agent_loop(tool_to_session, openai_tools, model, system_prompt,
                         repo_slug, source_branch, sonarqube_project_key,
                         max_iterations=25, dry_run=False):
    """Run the agent reasoning loop: LLM decides → call tool → observe → repeat."""
    import litellm

    # Ensure timeout is honoured by all providers (some ignore the parameter)
    os.environ.setdefault("LITELLM_REQUEST_TIMEOUT", "300")

    owner, repo = repo_slug.split("/", 1)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")

    # Initial user message with context
    user_message = f"""Fix all code quality issues in the project and create a Pull Request.

Context:
- SonarQube project key: `{sonarqube_project_key}`
- GitHub repository: `{owner}/{repo}`
- Source branch: `{source_branch}`
- Branch for fixes: `ai-fix/{source_branch}-{date_tag}`
- Date tag: {date_tag}
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

        # Call LLM with tools (5 min timeout, 2 automatic retries on transient errors)
        try:
            response = litellm.completion(
                model=model,
                messages=messages,
                tools=openai_tools if openai_tools else None,
                temperature=0.1,
                timeout=300,
                num_retries=2,
                user="jenkins-pipeline-agent",
            )
        except Exception as e:
            log(f"❌ LLM call failed after retries: {e}")
            raise RuntimeError(f"LLM call failed: {e}") from e

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

        # ── Phase 1: Preprocess all tool calls sequentially (arg injection) ──
        BOT_NAME = "Jenkins AI Bot"
        BOT_EMAIL = "jenkinai@noreply.github.com"
        bot_identity = {"name": BOT_NAME, "email": BOT_EMAIL}

        preprocessed = []
        for tool_call in message.tool_calls:
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            if func_name == "push_files":
                func_args["committer"] = bot_identity
                func_args["author"] = bot_identity
            elif func_name == "create_branch":
                func_args["from_branch"] = source_branch
            elif func_name == "create_pull_request":
                existing_body = func_args.get("body", "")
                func_args["body"] = (
                    f"> 🤖 This branch and PR were created automatically by **{BOT_NAME}** "
                    f"(`{BOT_EMAIL}`) as part of the CI/CD pipeline.\n\n"
                    f"{existing_body}"
                )

            preprocessed.append((tool_call, func_name, func_args))

        # ── Phase 2: Execute all tool calls in parallel ──
        DRY_RUN_TOOLS = {
            "create_branch", "push_files", "create_pull_request",
            "create_or_update_file", "write_file",
        }

        async def _execute_one(tool_call, func_name, func_args):
            """Execute a single tool call. Returns (tool_call, func_name, func_args, result_text, status)."""
            if dry_run and func_name in DRY_RUN_TOOLS:
                result_text = f"[DRY RUN] Skipped {func_name} — would have been called with: {json.dumps(func_args)[:300]}"
                return (tool_call, func_name, func_args, result_text, "dry_run")

            session = tool_to_session.get(func_name)
            if not session:
                result_text = f"Error: Unknown tool '{func_name}'. Available tools: {list(tool_to_session.keys())}"
                return (tool_call, func_name, func_args, result_text, "error")

            try:
                result = await session.call_tool(func_name, func_args)
                if result.content:
                    result_text = "\n".join(
                        item.text for item in result.content
                        if hasattr(item, "text")
                    )
                else:
                    result_text = "(empty result)"
                return (tool_call, func_name, func_args, result_text, "ok")
            except Exception as e:
                result_text = f"Error calling {func_name}: {e}"
                return (tool_call, func_name, func_args, result_text, "error")

        results = await asyncio.gather(
            *[_execute_one(tc, fn, fa) for tc, fn, fa in preprocessed]
        )

        # ── Phase 3: Log results and add to messages (in original order) ──
        for tool_call, func_name, func_args, result_text, status in results:
            log(f"\n🔧 Tool call: {func_name}")
            if func_name == "edit_file":
                edit_file_args_for_log = {
                    "path": func_args.get("path"),
                    "edits": func_args.get("edits"),
                }
                for key, value in func_args.items():
                    if key not in ("path", "edits"):
                        edit_file_args_for_log[key] = value
                log(f"   Args: {json.dumps(edit_file_args_for_log, indent=2)[:500]}")
            else:
                log(f"   Args: {json.dumps(func_args, indent=2)[:500]}")

            if status == "dry_run":
                log(f"   🏜️  {result_text}")
            elif status == "error":
                log(f"   ❌ {result_text}")
            else:
                log_preview = result_text[:600] + ("..." if len(result_text) > 600 else "")
                log(f"   ✅ Result ({len(result_text)} chars): {log_preview}")

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
    model = args.model or os.environ.get("LLM_MODEL") or "gemini/gemini-2.0-flash"
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

        # Filter to only the tools the agent actually needs (reduces token usage)
        ALLOWED_TOOLS = {
            # SonarQube
            "search_sonar_issues_in_projects",
            "show_rule",
            # Filesystem
            "read_text_file",
            "edit_file",
            "write_file",
            "read_multiple_files",
            # Test Runner
            "discover_tests",
            "run_tests",
            "analyze_failures",
            # GitHub
            "create_branch",
            "push_files",
            "create_pull_request",
        }
        filtered_tools = [t for t in all_tools if t.name in ALLOWED_TOOLS]
        log(f"   ✅ MCP tools loaded: {len(filtered_tools)}")

        # Convert to OpenAI format for litellm
        openai_tools = mcp_tools_to_openai_format(filtered_tools)

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

        # --- Graceful SonarQube Docker shutdown ---
        if sonarqube_container:
            log(f"\n🛑 Stopping SonarQube Docker container: {sonarqube_container}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "stop", "--time", "5", sonarqube_container,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                log("   ✅ SonarQube container stopped gracefully")
            except Exception as e:
                log(f"   ⚠️  Could not stop SonarQube container: {e}")


def main():
    parser = argparse.ArgumentParser(description="MCP AI Agent — Fix code issues using MCP tools")
    parser.add_argument("--repo", required=True, help="GitHub repo slug (owner/repo)")
    parser.add_argument("--model", default=None, help="LLM model name for litellm (e.g. gemini/gemini-2.0-flash). Overrides LLM_MODEL env var.")
    parser.add_argument("--source-branch", required=True, help="Current branch name")
    parser.add_argument("--workspace", default=".", help="Path to the workspace root")
    parser.add_argument("--max-iterations", type=int, default=25, help="Max agent loop iterations")
    parser.add_argument("--dry-run", action="store_true", help="Discover issues but skip git/PR operations")
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
