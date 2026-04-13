# AI Agents for CI Shared Library

Reusable Jenkins Shared Library for the thesis workspace. This repository contains the platform-facing part of the solution: Jenkins steps, the Python MCP agent runtime, and the local MCP test runner consumed by the reference application in `AI-agents-for-continuous-integration`.

## What this repository provides

- `FixWithAI(...)`: remediation step that prepares the MCP runtime, injects credentials, enforces preflight checks, and launches the agent.
- `DetectPreviousAIFix(...)`: helper step that checks whether a source PR branch already has a merged `ai-fix/*` remediation PR and returns a boolean guard value.
- `resources/scripts/mcp_agent.py`: compatibility wrapper for the MCP-based Python agent.
- `resources/scripts/mcp_agent_pkg/`: internal MCP runtime package split by responsibility.
- `resources/scripts/mcp_servers/test_runner_server.py`: local MCP server for configured test discovery, execution, and failure analysis.
- visible documentation, tests, and CI for the shared-library layer itself.

## Repository structure

```text
.
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- CHANGELOG.md
|-- README.md
|-- docs/
|   |-- fixwithai-contract.md
|   |-- mcp-agent-architecture.md
|   |-- test-config-contract.md
|   `-- versioning.md
|-- pom.xml
|-- resources/
|   `-- scripts/
|       |-- mcp_agent.py
|       |-- mcp_agent_pkg/
|       |   |-- README.md
|       |   |-- agent_loop.py
|       |   |-- artifacts.py
|       |   |-- entrypoint.py
|       |   |-- env_config.py
|       |   |-- logging_utils.py
|       |   |-- mcp_client.py
|       |   |-- servers.py
|       |   `-- system_prompt.md
|       |-- requirements-ai.txt
|       `-- mcp_servers/
|           `-- test_runner_server.py
`-- tests/
    |-- groovy/
    |   `-- FixWithAIStepTest.groovy
    `-- python/
        |-- conftest.py
        |-- test_agent_loop.py
        |-- test_artifacts.py
        `-- test_test_runner_server.py
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
- [`MCP agent architecture`](docs/mcp-agent-architecture.md)
- [`Versioning guidance`](docs/versioning.md)

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
- `agent_trace.json`
- `change_manifest.json`
- `execution_policy_snapshot.json`

These artifacts are meant to support debugging, reproducibility, and thesis evidence collection.

## Testing and CI

The shared library now has visible regression coverage for both its Python runtime and the Jenkins-step layer.

Local commands:

```bash
pytest tests/python -q
mvn -q test
```

CI:

- `.github/workflows/ci.yml` runs the same Python and Groovy checks on pushes and pull requests.
