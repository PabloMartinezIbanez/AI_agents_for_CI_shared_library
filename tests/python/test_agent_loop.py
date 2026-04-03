import asyncio
import json
import sys
import types

from mcp_agent_pkg.agent_loop import run_agent_loop


class DummySession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return types.SimpleNamespace(content=[types.SimpleNamespace(text="edited")])


class FakeMessage:
    def __init__(self, *, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self):
        dumped_tool_calls = []
        for tool_call in self.tool_calls:
            dumped_tool_calls.append(
                {
                    "id": tool_call.id,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
            )
        return {"role": "assistant", "content": self.content, "tool_calls": dumped_tool_calls}


def _make_completion_responses():
    tool_call = types.SimpleNamespace(
        id="call-1",
        function=types.SimpleNamespace(
            name="edit_file",
            arguments=json.dumps({"path": "src/example.py", "edits": []}),
        ),
    )
    return iter(
        [
            types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=FakeMessage(content="", tool_calls=[tool_call]))]
            ),
            types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=FakeMessage(content="No more actions"))]
            ),
        ]
    )


def test_dry_run_skips_edit_file(monkeypatch):
    responses = _make_completion_responses()
    litellm_module = types.ModuleType("litellm")
    litellm_module.completion = lambda **kwargs: next(responses)
    monkeypatch.setitem(sys.modules, "litellm", litellm_module)

    session = DummySession()
    messages = asyncio.run(
        run_agent_loop(
            tool_to_session={"edit_file": session},
            openai_tools=[],
            model="gemini/demo",
            system_prompt="demo",
            repo_slug="owner/repo",
            source_branch="feature/demo",
            sonarqube_project_key="demo:key",
            max_iterations=2,
            dry_run=True,
        )
    )

    assert session.calls == []
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("[DRY RUN] Skipped edit_file")
