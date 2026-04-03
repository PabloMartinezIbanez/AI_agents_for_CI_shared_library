# MCP Agent Package

Internal package for the Python MCP runtime.

## Purpose

`resources/scripts/mcp_agent.py` remains the public Jenkins-facing entrypoint.

`mcp_agent_pkg/` contains the internal implementation split by responsibility so the runtime is easier to maintain and test.

## Modules

- `env_config.py`: environment and reports-dir resolution
- `artifacts.py`: runtime artifact generation
- `logging_utils.py`: shared logging helpers
- `servers.py`: MCP server configuration
- `mcp_client.py`: MCP connection and tool conversion
- `agent_loop.py`: system prompt loading and agent execution loop
- `system_prompt.md`: base agent prompt text
- `entrypoint.py`: top-level runtime orchestration

## Prompt handling

The agent system prompt is stored in `system_prompt.md` and loaded by `agent_loop.py` into `SYSTEM_PROMPT`.

This keeps long prompt text outside the Python implementation while preserving the same runtime API.
