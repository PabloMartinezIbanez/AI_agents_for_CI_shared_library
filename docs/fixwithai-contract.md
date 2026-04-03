# `FixWithAI(...)` Contract

`FixWithAI(...)` is the public Jenkins step exposed by this shared library.

It prepares the local MCP runtime, validates the required Jenkins context, injects credentials, and launches the Python agent that coordinates SonarQube, filesystem, GitHub, and test-runner tools.

## Supported inputs

- `llmModel`: optional model id. Default: `gemini-3.1-pro-preview`.
- `llmCredentialId`: Jenkins string credential for the LLM API key.
- `githubCredentialId`: Jenkins string credential for the GitHub PAT.
- `repoSlug`: GitHub repository in `owner/repo` format. If omitted, the step tries to infer it from `origin`.
- `dryRun`: when `true`, the agent stays read-only from the repository-mutation point of view.
- `maxIterations`: max reasoning iterations for the agent loop.
- `sonarqubeCredentialId`: Jenkins string credential for the SonarQube token.
- `sonarqubeUrl`: explicit SonarQube URL. Falls back to `env.SONARQUBE_URL`.
- `sonarqubeProjectKey`: explicit effective project key. Falls back to `env.SONARQUBE_EFFECTIVE_PROJECT_KEY`.
- `testConfigFile`: optional override for the test-runner config file.
- `reportsDir`: optional workspace-relative reports directory. Default: `reports_for_IA`.

## Preflight rules

The step fails fast when:

- `SONARQUBE_URL` is missing.
- `SONARQUBE_EFFECTIVE_PROJECT_KEY` is missing.
- `maxIterations` is not a positive number.
- `reportsDir` is empty or contains `..`.
- `repoSlug`, when provided, does not match `owner/repo`.
- `testConfigFile`, when provided, does not exist in the workspace.

The step also skips execution entirely when the source branch starts with `ai-fix/` in order to avoid self-triggering loops.

## Runtime extraction

Before launching the agent, the step writes these resources into `.ai_fixer/`:

- `mcp_agent.py`
- `requirements-ai.txt`
- `mcp_servers/test_runner_server.py`
- the full `mcp_agent_pkg/` package

This makes the Jenkins-facing wrapper and the internal runtime package available in the temporary execution directory.

## Exported environment

During execution the step exports:

- `LLM_MODEL`
- provider-specific API key env var such as `GEMINI_API_KEY` or `OPENAI_API_KEY`
- `GITHUB_PERSONAL_ACCESS_TOKEN`
- `Github_AI_Auth`
- `SONARQUBE_URL`
- `SONARQUBE_TOKEN`
- `SONARQUBE_EFFECTIVE_PROJECT_KEY`
- `AGENT_REPORTS_DIR`
- `AI_TEST_CONFIG_FILE` when configured

## `dryRun` behaviour

`dryRun` is not a no-op. The runtime still reads issues, reads files, and can run validation tools, but it blocks repository mutations.

Blocked in `dryRun`:

- `edit_file`
- `write_file`
- `create_or_update_file`
- `create_branch`
- `push_files`
- `create_pull_request`

## Structured artifacts

The runtime writes these JSON artifacts into `reports_for_IA/` or the configured `reportsDir`:

- `agent_summary.json`
- `validation_results.json`
- `agent_trace.json`
- `change_manifest.json`
- `execution_policy_snapshot.json`

## Failure and cleanup

- The step always removes `.ai_fixer/` in `finally`.
- SonarQube connectivity failures are surfaced by the runtime instead of being silently ignored here.
- Credential binding happens only around the runtime execution block.
