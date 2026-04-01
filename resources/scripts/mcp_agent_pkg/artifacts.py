import json
from datetime import datetime, timezone
from pathlib import Path


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

