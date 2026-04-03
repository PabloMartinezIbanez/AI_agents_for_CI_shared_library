# MCP Agent Architecture

## Runtime layers

The shared library runtime is intentionally split into two levels.

### Jenkins layer

`vars/FixWithAI.groovy`

- validates Jenkins inputs and environment
- extracts runtime resources into `.ai_fixer/`
- injects credentials and execution env vars
- launches the Python runtime

### Python entrypoint layer

`resources/scripts/mcp_agent.py`

- stays as the Jenkins-facing wrapper
- delegates to `mcp_agent_pkg/`

### Internal runtime package

`resources/scripts/mcp_agent_pkg/`

- `entrypoint.py`: orchestration and argument parsing
- `servers.py`: MCP server definitions for SonarQube, filesystem, GitHub, and test runner
- `mcp_client.py`: session connection and tool mapping
- `agent_loop.py`: iterative tool-calling loop
- `artifacts.py`: structured artifact persistence
- `env_config.py`: environment and reports-dir resolution
- `logging_utils.py`: stderr logging helpers
- `system_prompt.md`: base agent prompt

## Execution flow

1. Jenkins calls `FixWithAI(...)`.
2. The step validates SonarQube, branch, reports dir, and optional config file inputs.
3. The step writes the runtime into `.ai_fixer/`.
4. The Python runtime resolves environment variables and reports dir.
5. MCP servers are started for:
   - SonarQube
   - filesystem
   - GitHub
   - local test runner
6. The LLM loop discovers issues and decides which tool to call next.
7. The runtime persists structured artifacts even if execution fails.

## Write policy

Normal mode allows the agent to edit files and create remediation branches/PRs.

`dryRun` mode keeps the execution in inspection/proposal mode and blocks write-oriented tools. The current blocklist is also persisted to `execution_policy_snapshot.json`.

## Structured outputs

The runtime produces five JSON artifacts:

- `agent_summary.json`: high-level run metadata
- `validation_results.json`: aggregated test-runner results
- `agent_trace.json`: ordered tool-call trace with statuses and previews
- `change_manifest.json`: files targeted by write tools
- `execution_policy_snapshot.json`: effective dry-run policy snapshot

## Operational boundary

The filesystem MCP server is rooted at the workspace passed from Jenkins. This is the primary path boundary for file operations in the current architecture.
