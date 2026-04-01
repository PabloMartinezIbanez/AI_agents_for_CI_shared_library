# AI Agents for CI Shared Library

Reusable Jenkins Shared Library for the thesis workspace. This repository contains the platform-facing part of the solution: Jenkins steps, the Python MCP agent runtime, and the local MCP test runner consumed by the reference application in `AI-agents-for-continuous-integration`.

## What this repository provides

- `FixWithAI(...)`: remediation step that prepares the MCP runtime, injects credentials, enforces preflight checks, and launches the agent.
- `resources/scripts/mcp_agent.py`: the MCP-based Python agent.
- `resources/scripts/mcp_servers/test_runner_server.py`: local MCP server for configured test discovery, execution, and failure analysis.

## Repository structure

```text
.
|-- README.md
|-- docs/
|   |-- fixwithai-contract.md
|   `-- test-config-contract.md
|-- resources/
|   `-- scripts/
|       |-- mcp_agent.py
|       |-- requirements-ai.txt
|       `-- mcp_servers/
|           `-- test_runner_server.py
```

## Runtime model

The intended deployment model is:

1. A Jenkins pipeline loads this shared library with `@Library(...)`.
2. The demo repository prepares SonarQube analysis and build context.
3. `FixWithAI(...)` runs only outside PR builds in the current demo flow.
4. The MCP agent uses SonarQube, filesystem, GitHub, and test-runner tools to analyze and validate changes.

## Public contracts

- [`FixWithAI` contract](docs/fixwithai-contract.md)
- [`ai-tests-config.json` and `.ai-tests.json` contract](docs/test-config-contract.md)

## Credentials and environment

The library expects Jenkins credentials and environment variables to be injected by the pipeline.

Main inputs:

- `SONARQUBE_URL`
- `SONARQUBE_TOKEN`
- `SONARQUBE_EFFECTIVE_PROJECT_KEY`
- LLM provider credential exported by `FixWithAI(...)`
- GitHub PAT exported as `GITHUB_PERSONAL_ACCESS_TOKEN`
- Optional `AI_TEST_CONFIG_FILE`

See the contract documents for the exact parameter-level behavior.

## Structured artifacts

When the MCP agent runs through `FixWithAI(...)`, the runtime writes structured artifacts to `reports_for_IA/` in the workspace:

- `agent_summary.json`
- `validation_results.json`

These artifacts are meant to support debugging, reproducibility, and thesis evidence collection.
