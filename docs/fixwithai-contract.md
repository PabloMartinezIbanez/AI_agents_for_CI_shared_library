# `FixWithAI(...)` contract

`FixWithAI(...)` is the public remediation entrypoint of the shared library.

## Purpose

- prepare the MCP runtime inside the Jenkins workspace;
- resolve branch and repository context;
- inject credentials and environment variables;
- launch `mcp_agent.py`;
- clean temporary runtime files even if execution fails.

## Parameters

- `llmModel`: model identifier used by the runtime. Defaults to `gemini-3.1-pro-preview`.
- `llmCredentialId`: Jenkins string credential for the LLM provider key.
- `githubCredentialId`: Jenkins string credential for the GitHub PAT.
- `sonarqubeCredentialId`: Jenkins string credential for the SonarQube token.
- `repoSlug`: GitHub repository slug in `owner/repo` form. If omitted, the step tries to infer it from `origin`.
- `dryRun`: when `true`, forwards `--dry-run` to the MCP agent and skips branch/PR mutations inside the agent workflow.
- `maxIterations`: maximum reasoning loop iterations for the agent. Must be a positive number.
- `sonarqubeUrl`: explicit override for `SONARQUBE_URL`.
- `sonarqubeProjectKey`: explicit override for `SONARQUBE_EFFECTIVE_PROJECT_KEY`.
- `testConfigFile`: optional override for the base `.ai-tests.json` contract.

## Required environment and credentials

Required before runtime launch:

- `SONARQUBE_URL`
- `SONARQUBE_EFFECTIVE_PROJECT_KEY`
- Jenkins credentials referenced by `llmCredentialId`, `githubCredentialId`, and `sonarqubeCredentialId`

Exported to the Python runtime:

- `LLM_MODEL`
- provider-specific API key variable such as `GEMINI_API_KEY` or `OPENAI_API_KEY`
- `GITHUB_PERSONAL_ACCESS_TOKEN`
- compatibility export `Github_AI_Auth`
- `SONARQUBE_URL`
- `SONARQUBE_TOKEN`
- `SONARQUBE_EFFECTIVE_PROJECT_KEY`
- `AGENT_REPORTS_DIR`
- optional `AI_TEST_CONFIG_FILE`

## Branch behavior

- Source branch is resolved from Jenkins branch variables first, then from git if needed.
- If the source branch starts with `ai-fix/`, the step exits early to avoid remediation loops.
- If branch resolution fails, the step aborts with an explicit error.

## Workspace behavior

- Creates `.ai_fixer/` as temporary runtime directory.
- Writes the MCP agent, test runner, and requirements into that directory.
- Removes `.ai_fixer/` in a `finally` block so cleanup still happens on failure.

## Structured artifacts

The Python runtime may write these files into `reports_for_IA/` inside the workspace:

- `agent_summary.json`
- `validation_results.json`

## Failure conditions

The step aborts early when:

- `SONARQUBE_URL` is missing;
- `SONARQUBE_EFFECTIVE_PROJECT_KEY` is missing;
- `maxIterations` is not positive;
- `repoSlug` cannot be inferred and was not provided.

## Example

```groovy
FixWithAI(
    llmModel: 'gemini-3.1-pro-preview',
    llmCredentialId: 'LLM_API_KEY_VALUE',
    githubCredentialId: 'Github_AI_Auth',
    repoSlug: 'owner/repository',
    testConfigFile: 'ai-tests-config.json',
    dryRun: false
)
```
