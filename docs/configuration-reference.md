# Configuration Reference

This document lists every parameter the user can configure, grouped by where each one is set. All parameters have working defaults — only change what your situation requires.

---

## 1. `Jenkinsfile` — `FixWithAI(config)` block

These are the primary knobs. Pass them as a Groovy map to the `FixWithAI` step:

```groovy
FixWithAI(
    llmModel:           'gemini/gemini-2.0-flash',
    maxIterations:      80,
    dryRun:             false,
    // ... etc.
)
```

### Model and credentials

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llmModel` | String | `'gemini-3.1-pro-preview'` | LLM to use. The prefix determines which API key variable is injected (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.). See supported prefixes below. |
| `llmCredentialId` | String | `'Gemini_Api_token'` | Jenkins credential ID that holds the LLM API key. Must be a Secret Text credential. |
| `githubCredentialId` | String | `'GITHUB_PAT'` | Jenkins credential ID for the GitHub Personal Access Token used to create branches and pull requests. Must have `repo` scope. |
| `sonarqubeCredentialId` | String | `'SONARQUBE_TOKEN'` | Jenkins credential ID for the SonarQube user token. Must have permission to read issues on the target project. |

**Supported `llmModel` prefixes and their effects:**

| Prefix | API key injected | Notes |
|--------|-----------------|-------|
| `gemini/` or `google/` | `GEMINI_API_KEY` | Recommended. e.g. `gemini/gemini-2.0-flash` |
| `gemini-` or `gemini_` (no slash) | `GEMINI_API_KEY` | Prefix `gemini/` is prepended automatically |
| `claude` or `anthropic/` | `ANTHROPIC_API_KEY` | Anthropic models |
| `nvidia/` or `deepseek` | `OPENAI_API_KEY` | Routes through NVIDIA NIM endpoint |
| `kimi` | `OPENAI_API_KEY` | Routes through NVIDIA NIM endpoint |
| `ollama/` or `ollama_chat/` | `OLLAMA_API_KEY` | Local Ollama instance |
| anything else | `OPENAI_API_KEY` | OpenAI-compatible providers |

**When to change:**
- Change `llmModel` when you want to try a different model or provider. More capable models (e.g. `gemini-3.1-pro-preview`) fix more issues per run but are slower and more expensive. Faster/cheaper models (e.g. `gemini/gemini-2.0-flash`) are better for high-frequency pipelines.
- Change `llmCredentialId`, `githubCredentialId`, `sonarqubeCredentialId` when your Jenkins credentials use different IDs than the defaults, or when you rotate credentials.

---

### Repository

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `repoSlug` | String | _(auto-inferred from `git remote get-url origin`)_ | GitHub repository in `owner/repo` format. Only needed if the remote URL cannot be parsed automatically (e.g. non-standard git hosts). |

**When to change:** Set explicitly when `git remote` does not point to GitHub, or when the auto-detection fails in the pipeline log with _"No se pudo inferir el repo slug"_.

---

### Iteration budget

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `maxIterations` | Integer | _(not set — uses dynamic formula)_ | Fixes the iteration limit to an exact number, bypassing the dynamic formula entirely. Use only when you need a hard, predictable cap. |
| `dynamicMaxIterations` | Boolean | `true` | Enables the automatic budget calculation based on open SonarQube issues. Set to `false` to always use `minIterations`. |
| `minIterations` | Integer | `30` | Lower bound for the dynamic formula. The agent always gets at least this many iterations, even if there are zero open issues. |
| `maxIterationsCap` | Integer | `200` | Upper bound for the dynamic formula. The computed value is clamped to this ceiling. Must be ≥ `minIterations`. |
| `issuesPerIteration` | Integer | `1` | Number of issues treated as one group in the formula. Increasing this reduces the budget (fewer groups → fewer extra iterations). |
| `iterationsPerIssue` | Integer | `4` | Iterations allocated per group. Increasing this makes the budget more generous. |

**How the formula works:**

```
effectiveMaxIterations = clamp(
    minIterations + ceil(openIssues / issuesPerIteration) * iterationsPerIssue,
    minIterations,
    maxIterationsCap
)
```

With the current defaults (`issuesPerIteration=1`, `iterationsPerIssue=4`):

```
effectiveMaxIterations = clamp(30 + openIssues * 4, 30, 200)
```

**When to change:**
- `maxIterations`: set a fixed value when you need a hard time/cost constraint per run, regardless of issue count.
- `dynamicMaxIterations: false`: useful in cost-sensitive environments where you always want `minIterations`.
- `minIterations`: raise it if the agent consistently runs out of iterations even on projects with few issues (lots of setup overhead per run). Lower it to reduce cost on fast pipelines.
- `maxIterationsCap`: raise it if large projects (50+ issues) regularly hit the ceiling and the agent stops mid-work. Lower it to enforce a strict cost ceiling.
- `iterationsPerIssue`: raise it if issues are complex (e.g. cross-file refactors, many failing tests) and the agent runs out before finishing. Lower it if issues are trivial and the current budget is too conservative.
- `issuesPerIteration`: raise it to reduce the budget (treat every N issues as one unit). Useful only if you want to fine-tune grouping.

---

### SonarQube

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sonarqubeUrl` | String | `env.SONARQUBE_URL` | Base URL of your SonarQube instance, e.g. `http://sonarqube:9000`. Trailing slashes are stripped automatically. |
| `sonarqubeProjectKey` | String | `env.SONARQUBE_EFFECTIVE_PROJECT_KEY` | SonarQube project key to read issues from. Shown in SonarQube under _Project Settings → General_. |

**When to change:** Pass these in the config block when they differ per-pipeline and cannot be set as global environment variables in Jenkins.

---

### Tests

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `testConfigFile` | String | `''` _(disabled)_ | Path (relative to workspace root) to a YAML/JSON file that tells the agent which test suites to run and how. If not set, the agent auto-discovers tests via the `discover_tests` MCP tool. |

**When to change:** Set this when auto-discovery picks up the wrong test commands, when you want to restrict which suites the agent runs, or when you need to pass custom environment variables or working directories to the test runner. See `docs/test-config-contract.md` for the file format.

---

### Output

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reportsDir` | String | `'reports_for_IA'` | Directory (relative to workspace) where the agent writes its JSON artifacts: `agent_summary.json`, `agent_trace.json`, `validation_results.json`, `change_manifest.json`. |
| `dryRun` | Boolean | `false` | When `true`, the agent reads files and reasons about fixes but never creates branches, pushes commits, or opens pull requests. All repository-mutation tool calls are skipped and logged with a `[DRY RUN]` prefix. |

**When to change:**
- `dryRun: true`: always use when first integrating the step into a pipeline, to verify the agent behaves correctly without touching the repository.
- `reportsDir`: change if your pipeline archives artifacts from a non-default path, or if `reports_for_IA` conflicts with another tool.

---

## 2. Jenkins environment variables / global configuration

Set these in _Manage Jenkins → System → Global properties → Environment variables_, or in a `withEnv` block in the pipeline.

| Variable | Read by | Description |
|----------|---------|-------------|
| `SONARQUBE_URL` | `FixWithAI.groovy`, `entrypoint.py` | Fallback for `sonarqubeUrl` when it is not set in the config block. |
| `SONARQUBE_EFFECTIVE_PROJECT_KEY` | `entrypoint.py` | Fallback for `sonarqubeProjectKey`. Takes priority over `SONARQUBE_PROJECT_KEY`. |
| `SONARQUBE_PROJECT_KEY` | `entrypoint.py` | Secondary fallback for the SonarQube project key, checked if `SONARQUBE_EFFECTIVE_PROJECT_KEY` is not set. |
| `AI_REPORTS_DIR` | `env_config.py` | Overrides `reportsDir` at the OS level. Useful when the directory must be set outside the `Jenkinsfile` (e.g. shared infrastructure scripts). |
| `LLM_MODEL` | `entrypoint.py` | Overrides the model passed via `--model`. Lower priority than the CLI flag; the Groovy step always passes `--model` explicitly, so this env var only applies when running `mcp_agent.py` directly. |
| `AI_BASE_URL` | `entrypoint.py` | Custom base URL for the LLM provider API. Set automatically by `FixWithAI.groovy` for NVIDIA NIM / Kimi models. Only set manually when using a self-hosted or proxy endpoint. |
| `LITELLM_TELEMETRY` | `agent_loop.py` | Controls litellm's background telemetry. Defaults to `False` (disabled) via `os.environ.setdefault`. Set to `True` only if you specifically want to contribute usage data to BerriAI. Has no operational effect on the agent. |

---

## 3. Running `mcp_agent.py` directly (advanced / debugging)

The Python entrypoint accepts these CLI flags. In normal operation `FixWithAI.groovy` constructs this command automatically — these flags are only relevant when running the agent manually outside Jenkins.

```
python3 mcp_agent.py \
    --repo         owner/repo    \   # required
    --source-branch main         \   # required
    --model        gemini/...    \   # optional, overrides LLM_MODEL env var
    --workspace    /path/to/ws   \   # default: current directory
    --max-iterations 80          \   # default: 25 (note: Groovy passes effectiveMaxIterations here)
    --dry-run                        # flag, no value needed
```

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | _(required)_ | GitHub repository in `owner/repo` format. |
| `--source-branch` | _(required)_ | Name of the branch the agent is operating on. Used to name the fix branch and the PR base. |
| `--model` | `LLM_MODEL` env var, then `gemini/gemini-2.0-flash` | litellm model string. |
| `--workspace` | `.` (current directory) | Absolute or relative path to the repository root. The agent's filesystem tools operate relative to this path. |
| `--max-iterations` | `25` | Maximum agent loop iterations when called directly. When called from Jenkins, `FixWithAI.groovy` overrides this with `effectiveMaxIterations`. |
| `--dry-run` | `false` | Same effect as `dryRun: true` in the Groovy config. |

---

## Quick-reference: most common adjustments

| Goal | What to change | Where |
|------|---------------|-------|
| Use a different LLM | `llmModel` | `Jenkinsfile` |
| Agent runs out of iterations on large projects | Increase `maxIterationsCap` or `iterationsPerIssue` | `Jenkinsfile` |
| Agent runs out of iterations on simple projects | Increase `minIterations` | `Jenkinsfile` |
| Fix the budget for cost control | Set `maxIterations` to a fixed number | `Jenkinsfile` |
| Test the integration without touching the repo | `dryRun: true` | `Jenkinsfile` |
| Agent runs wrong tests | Set `testConfigFile` | `Jenkinsfile` |
| Credentials have different IDs in your Jenkins | `llmCredentialId`, `githubCredentialId`, `sonarqubeCredentialId` | `Jenkinsfile` |
| SonarQube is at a non-standard URL | `sonarqubeUrl` | `Jenkinsfile` or env var |
| Save artifacts to a different directory | `reportsDir` | `Jenkinsfile` |
