# `FixWithAI(...)` Contract

`FixWithAI(...)` is the public Jenkins step exposed by this shared library.

It prepares the local MCP runtime, validates the required Jenkins context, injects credentials, and launches the Python agent that coordinates SonarQube, filesystem, GitHub, and test-runner tools.

## Supported inputs

- `llmModel`: optional model id. Default: `gemini-3.1-pro-preview`.
- `llmCredentialId`: Jenkins string credential for the LLM API key.
- `githubCredentialId`: Jenkins string credential for the GitHub PAT.
- `repoSlug`: GitHub repository in `owner/repo` format. If omitted, the step tries to infer it from `origin`.
- `dryRun`: when `true`, the agent stays read-only from the repository-mutation point of view.
- `maxIterations`: optional explicit override for agent loop iterations. When set, it takes precedence over dynamic sizing.
- `dynamicMaxIterations`: enable/disable automatic sizing based on SonarQube open issues. Default: `true`.
- `minIterations`: lower bound used by dynamic sizing. Default: `25`.
- `maxIterationsCap`: upper bound used by dynamic sizing. Default: `120`.
- `issuesPerIteration`: sizing ratio for dynamic mode (higher value -> fewer iterations). Default: `3`.
- `sonarqubeCredentialId`: Jenkins string credential for the SonarQube token.
- `sonarqubeUrl`: explicit SonarQube URL. Falls back to `env.SONARQUBE_URL`.
- `sonarqubeProjectKey`: explicit effective project key. Falls back to `env.SONARQUBE_EFFECTIVE_PROJECT_KEY`.
- `testConfigFile`: optional override for the test-runner config file.
- `reportsDir`: optional workspace-relative reports directory. Default: `reports_for_IA`.

## Preflight rules

The step fails fast when:

- `SONARQUBE_URL` is missing.
- `SONARQUBE_EFFECTIVE_PROJECT_KEY` is missing.
- `maxIterations`, `minIterations`, `maxIterationsCap`, or `issuesPerIteration` are invalid (non-positive).
- `dynamicMaxIterations` is not boolean.
- `maxIterationsCap` is lower than `minIterations`.
- `reportsDir` is empty or contains `..`.
- `repoSlug`, when provided, does not match `owner/repo`.
- `testConfigFile`, when provided, does not exist in the workspace.

The step also skips execution entirely when the source branch starts with `ai-fix/` in order to avoid self-triggering loops.

## Dynamic max-iteration sizing

When `maxIterations` is not provided and `dynamicMaxIterations` is `true`, the step queries SonarQube issue totals via `/api/issues/search` and computes:

- `dynamicCandidate = minIterations + ceil(openIssues / issuesPerIteration)`
- `effectiveMaxIterations = clamp(dynamicCandidate, minIterations, maxIterationsCap)`

If SonarQube cannot be queried (token/API/parsing error), the step falls back to `minIterations`.

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

## `DetectPreviousAIFix(...)` helper step

The shared library also exposes `DetectPreviousAIFix(...)` to centralize PR loop-guard logic.

Purpose:

- Query closed PRs for the current source branch and detect merged remediation branches matching `ai-fix/{sourceBranch}-*`.
- Return `true` only when a matching merged AI remediation PR is recent (inside the cooldown window).
- Return `false` otherwise (including fail-open scenarios such as API errors or malformed payloads).

Supported inputs:

- `repoSlug` (required): GitHub repository in `owner/repo` format.
- `sourceBranch` (optional): branch to evaluate, defaults to `env.CHANGE_BRANCH`.
- `reportsDir` (optional): output directory for API payload files, defaults to `env.AI_REPORTS_DIR` or `reports_for_IA`.
- `githubTokenVar` (optional): env var name containing the GitHub token, defaults to `Github_AI_Auth`.
- `perPage` (optional): number of closed PRs fetched from GitHub API, default `100`.
- `cooldownMinutes` (optional): minutes to block new AI runs after a merged `ai-fix/*` PR. Default `5`.

Timestamp behavior:

- GitHub closed PR payload includes `merged_at` and `closed_at` timestamps.
- `DetectPreviousAIFix(...)` uses the merge timestamp to compute PR age.
- If a matching merged AI PR is newer than `cooldownMinutes`, it returns `true` (skip IA fix).
- If all matching merged AI PRs are older than `cooldownMinutes`, it returns `false` (allow IA fix again).

Example usage in a Jenkinsfile:

```groovy
env.AI_ALREADY_APPLIED = DetectPreviousAIFix(
	repoSlug: 'owner/repo',
	reportsDir: env.AI_REPORTS_DIR,
	sourceBranch: env.CHANGE_BRANCH
) ? 'true' : 'false'
```
