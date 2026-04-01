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
|-- pom.xml
|-- resources/
|   `-- scripts/
|       |-- mcp_agent.py
|       |-- requirements-ai.txt
|       `-- mcp_servers/
|           `-- test_runner_server.py
|-- src/test/groovy/
|   |-- FixWithAITest.groovy
|   `-- support/
`-- tests/python/
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

## Testing

### Python tests

```bash
pytest tests/python -q
```

### Groovy contract tests

Locally verified with JDK 21 plus Maven:

```powershell
$env:JAVA_HOME='C:\Program Files\Java\jdk-21'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
mvn -q test
```

The Groovy suite is intentionally lightweight: it validates contract and preflight behavior of the Jenkins steps without requiring a full Jenkins test harness.
