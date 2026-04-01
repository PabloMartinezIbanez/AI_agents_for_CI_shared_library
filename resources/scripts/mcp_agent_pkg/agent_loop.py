import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .logging_utils import log


SYSTEM_PROMPT = (
    Path(__file__).with_name("system_prompt.md").read_text(encoding="utf-8")
)


async def run_agent_loop(
    tool_to_session,
    openai_tools,
    model,
    system_prompt,
    repo_slug,
    source_branch,
    sonarqube_project_key,
    max_iterations=25,
    dry_run=False,
):
    """Run the agent reasoning loop: LLM decides -> call tool -> observe -> repeat."""

    import litellm

    os.environ.setdefault("LITELLM_REQUEST_TIMEOUT", "300")

    owner, repo = repo_slug.split("/", 1)
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    branch_time_tag = datetime.now(timezone.utc).strftime("%H%M%S")
    default_fix_branch = f"ai-fix/{source_branch}-{date_tag}-{branch_time_tag}"

    def _append_time_suffix(branch_name):
        if not isinstance(branch_name, str) or not branch_name:
            return branch_name
        suffix = f"-{branch_time_tag}"
        if branch_name.endswith(suffix):
            return branch_name
        return f"{branch_name}{suffix}"

    user_message = f"""Fix all code quality issues in the project and create a Pull Request.

Context:
- SonarQube project key: `{sonarqube_project_key}`
- GitHub repository: `{owner}/{repo}`
- Source branch: `{source_branch}`
- Branch for fixes: `{default_fix_branch}`
- Date tag: {date_tag}
- Time tag (UTC): {branch_time_tag}
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
        messages.append(message.model_dump())

        if not message.tool_calls:
            log("\n🏁 Agent finished reasoning.")
            if message.content:
                log(f"📝 Final message:\n{message.content}")
            break

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
                branch_name_set = False
                for key in ("branch", "branch_name", "name", "ref"):
                    branch_value = func_args.get(key)
                    if isinstance(branch_value, str) and branch_value:
                        func_args[key] = _append_time_suffix(branch_value)
                        branch_name_set = True
                        break

                if not branch_name_set:
                    func_args["branch"] = default_fix_branch
            elif func_name == "create_pull_request":
                existing_body = func_args.get("body", "")
                func_args["body"] = (
                    f"> 🤖 This branch and PR were created automatically by **{BOT_NAME}** "
                    f"(`{BOT_EMAIL}`) as part of the CI/CD pipeline.\n\n"
                    f"{existing_body}"
                )

            preprocessed.append((tool_call, func_name, func_args))

        DRY_RUN_TOOLS = {
            "create_branch",
            "push_files",
            "create_pull_request",
            "create_or_update_file",
            "write_file",
        }

        async def _execute_one(tool_call, func_name, func_args):
            if dry_run and func_name in DRY_RUN_TOOLS:
                result_text = (
                    f"[DRY RUN] Skipped {func_name} - would have been called with: "
                    f"{json.dumps(func_args)[:300]}"
                )
                return (tool_call, func_name, func_args, result_text, "dry_run")

            session = tool_to_session.get(func_name)
            if not session:
                result_text = (
                    f"Error: Unknown tool '{func_name}'. Available tools: {list(tool_to_session.keys())}"
                )
                return (tool_call, func_name, func_args, result_text, "error")

            try:
                result = await session.call_tool(func_name, func_args)
                if result.content:
                    result_text = "\n".join(
                        item.text for item in result.content if hasattr(item, "text")
                    )
                else:
                    result_text = "(empty result)"
                return (tool_call, func_name, func_args, result_text, "ok")
            except Exception as e:
                result_text = f"Error calling {func_name}: {e}"
                return (tool_call, func_name, func_args, result_text, "error")

        results = await asyncio.gather(*[_execute_one(tc, fn, fa) for tc, fn, fa in preprocessed])

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

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                }
            )

    else:
        log(f"\n⚠️  Max iterations ({max_iterations}) reached. Agent stopped.")

    return messages
