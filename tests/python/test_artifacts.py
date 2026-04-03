import json

from mcp_agent_pkg.artifacts import persist_agent_artifacts


def test_persist_agent_artifacts_writes_extended_trace_files(tmp_path):
    messages = [
        {
            "role": "assistant",
            "content": "Applying fix",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps(
                            {
                                "path": "src/calculator/suma.py",
                                "edits": [{"oldText": "a+b", "newText": "a + b"}],
                            }
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": json.dumps(
                {
                    "results": [
                        {"name": "python-unit-tests", "passed": True, "status": "passed"}
                    ]
                }
            ),
        },
        {"role": "assistant", "content": "Done"},
    ]

    persist_agent_artifacts(
        str(tmp_path),
        repo_slug="owner/repo",
        source_branch="feature/demo",
        workspace="C:/workspace",
        model="gemini/demo",
        dry_run=True,
        max_iterations=4,
        messages=messages,
        status="completed",
    )

    trace_payload = json.loads((tmp_path / "agent_trace.json").read_text(encoding="utf-8"))
    manifest_payload = json.loads((tmp_path / "change_manifest.json").read_text(encoding="utf-8"))
    policy_payload = json.loads(
        (tmp_path / "execution_policy_snapshot.json").read_text(encoding="utf-8")
    )

    assert trace_payload["toolCalls"][0]["name"] == "edit_file"
    assert trace_payload["toolCalls"][0]["path"] == "src/calculator/suma.py"
    assert trace_payload["toolCalls"][0]["status"] == "ok"
    assert manifest_payload["changedFiles"] == ["src/calculator/suma.py"]
    assert manifest_payload["writeOperations"][0]["tool"] == "edit_file"
    assert policy_payload["dryRun"] is True
    assert "edit_file" in policy_payload["blockedTools"]
