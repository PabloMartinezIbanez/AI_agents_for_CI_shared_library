import json
from datetime import datetime, timezone
from pathlib import Path


WRITE_TOOLS = {"edit_file", "write_file", "create_or_update_file"}
DRY_RUN_BLOCKED_TOOLS = sorted(
    ["create_branch", "push_files", "create_pull_request", "create_or_update_file", "edit_file", "write_file"]
)


def write_json_artifact(reports_dir, filename, payload):
    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    output_path = Path(reports_dir) / filename
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(output_path)


def extract_validation_results(messages):
    aggregated_results = []

    for message in messages:
        if message.get("role") != "tool":
            continue

        content = message.get("content")
        if not isinstance(content, str):
            continue

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            aggregated_results.extend(payload["results"])

    return {"results": aggregated_results}


def _parse_tool_arguments(raw_arguments):
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return {}

    try:
        return json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {"raw": raw_arguments}


def extract_tool_trace(messages):
    tool_calls = []
    tool_index = {}

    for message in messages:
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function_payload = tool_call.get("function", {})
                arguments = _parse_tool_arguments(function_payload.get("arguments"))
                entry = {
                    "id": tool_call.get("id"),
                    "name": function_payload.get("name", ""),
                    "path": arguments.get("path", ""),
                    "arguments": arguments,
                    "status": "pending",
                    "resultPreview": "",
                }
                tool_calls.append(entry)
                tool_index[entry["id"]] = entry
            continue

        if message.get("role") != "tool":
            continue

        entry = tool_index.get(message.get("tool_call_id"))
        if not entry:
            continue

        content = message.get("content", "")
        if isinstance(content, str):
            if content.startswith("[DRY RUN]"):
                entry["status"] = "dry_run"
            elif content.startswith("Error"):
                entry["status"] = "error"
            else:
                entry["status"] = "ok"
            entry["resultPreview"] = content[:500]
        else:
            entry["status"] = "ok"
            entry["resultPreview"] = str(content)[:500]

    return tool_calls


def extract_change_manifest(tool_trace):
    changed_files = []
    write_operations = []

    for entry in tool_trace:
        if entry.get("name") not in WRITE_TOOLS:
            continue

        path = entry.get("path")
        if isinstance(path, str) and path:
            changed_files.append(path)

        write_operations.append(
            {
                "tool": entry.get("name"),
                "path": path or "",
                "status": entry.get("status", "unknown"),
            }
        )

    return {
        "changedFiles": sorted(set(changed_files)),
        "writeOperations": write_operations,
    }


def persist_agent_artifacts(
    reports_dir,
    *,
    repo_slug,
    source_branch,
    workspace,
    model,
    dry_run,
    max_iterations,
    messages,
    status,
    error_message="",
):
    validation_results = extract_validation_results(messages)
    tool_trace = extract_tool_trace(messages)
    change_manifest = extract_change_manifest(tool_trace)
    assistant_messages = [msg for msg in messages if msg.get("role") == "assistant"]
    final_assistant_message = assistant_messages[-1]["content"] if assistant_messages else ""

    summary = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "error": error_message,
        "repo": repo_slug,
        "sourceBranch": source_branch,
        "workspace": workspace,
        "model": model,
        "dryRun": dry_run,
        "maxIterations": max_iterations,
        "reportsDir": reports_dir,
        "messageCount": len(messages),
        "toolMessageCount": sum(1 for message in messages if message.get("role") == "tool"),
        "finalAssistantMessage": final_assistant_message,
        "validationSummary": {
            "totalSuites": len(validation_results["results"]),
            "passedSuites": sum(1 for result in validation_results["results"] if result.get("passed")),
            "failedSuites": sum(1 for result in validation_results["results"] if not result.get("passed")),
        },
    }

    write_json_artifact(reports_dir, "agent_summary.json", summary)
    write_json_artifact(reports_dir, "validation_results.json", validation_results)
    write_json_artifact(
        reports_dir,
        "agent_trace.json",
        {
            "toolCalls": tool_trace,
            "assistantMessageCount": len(assistant_messages),
        },
    )
    write_json_artifact(reports_dir, "change_manifest.json", change_manifest)
    write_json_artifact(
        reports_dir,
        "execution_policy_snapshot.json",
        {
            "dryRun": dry_run,
            "blockedTools": DRY_RUN_BLOCKED_TOOLS if dry_run else [],
            "reportsDir": reports_dir,
            "maxIterations": max_iterations,
        },
    )
