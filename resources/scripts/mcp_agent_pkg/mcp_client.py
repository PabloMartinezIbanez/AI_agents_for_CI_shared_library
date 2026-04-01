import os
import sys

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from .logging_utils import log


async def connect_servers(server_configs, exit_stack):
    """Connect to MCP servers and return {name: session} + merged tool list.

    Note: connections are opened on the same task that will close them via
    `exit_stack` to avoid AnyIO cancel-scope task-affinity errors.
    """

    # Higher number means higher priority when two servers expose same tool name.
    server_priority = {
        "github": 30,
        "sonarqube": 20,
        "test_runner": 15,
        "filesystem": 10,
    }

    sessions = {}
    all_tools_by_name = {}
    tool_owner = {}
    tool_to_session = {}

    for name, params in server_configs.items():
        log(f"🔌 Connecting to MCP server: {name}...")
        try:
            errlog = sys.stderr
            if name == "sonarqube":
                errlog = open(os.devnull, "w")
                exit_stack.callback(errlog.close)

            stdio_transport = await exit_stack.enter_async_context(
                stdio_client(params, errlog=errlog)
            )
            read_stream, write_stream = stdio_transport
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            response = await session.list_tools()
        except Exception as e:
            log(f"   ❌ Failed to connect to {name}: {e}")
            continue

        sessions[name] = session

        for tool in response.tools:
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

    return sessions, list(all_tools_by_name.values()), tool_to_session


def mcp_tools_to_openai_format(mcp_tools):
    """Convert MCP tool definitions to OpenAI function-calling schema."""
    openai_tools = []
    for tool in mcp_tools:
        schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": schema,
                },
            }
        )
    return openai_tools
