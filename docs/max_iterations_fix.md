# Bug Fix: Incorrect Status, Spurious litellm Error, and Undersized Iteration Budget

## The Bug

Three problems appeared together whenever the agent exhausted its iteration budget:

1. `status` was recorded as `"completed"` in the artifact even though the agent was force-stopped, not finished.
2. A red ANSI-formatted message printed to stderr after all application output:

```
[1;31mProvider List: https://docs.litellm.ai/docs/providers[0m
```

3. The dynamic iteration budget formula was too conservative, consistently producing a limit too small for the agent to finish its work.

Additionally, a fourth latent bug existed in the function signature: `base_url` and `extra_body` were required positional parameters with no default value, causing a `TypeError` in any call site that omitted them (including the test suite).

## Root Cause

### 1. Status always set to `"completed"`

In `entrypoint.py`, `status = "completed"` was set unconditionally after `run_agent_loop` returned, regardless of how the loop exited:

```python
# Before — always "completed"
messages = await run_agent_loop(...)
status = "completed"
```

`run_agent_loop` uses a `for/else` construct: the `else` clause fires only when the loop is exhausted without a `break`. When the agent stops naturally it executes `break`; when it hits max iterations it does not. Both paths return `messages` normally — no exception is raised — so the caller had no way to distinguish them without inspecting the messages themselves.

When max iterations is reached, the last entry in `messages` always has `role: "tool"` (the results of the tool calls requested in the final iteration, after which the loop ran out of budget). When the agent stops naturally, the last entry has `role: "assistant"` with no tool calls. This difference is the reliable signal.

### 2. Spurious litellm `Provider List` message

litellm's telemetry system registers background callbacks that execute after each `litellm.completion()` call. These callbacks run in a thread pool and may schedule asyncio tasks.

When the agent terminates normally, the last `litellm.completion()` call returns a response with no tool calls. The agent breaks immediately and the function returns. There is no further litellm work outstanding, so by the time `asyncio.run()` closes the event loop the background tasks have already completed.

When max iterations is reached, the last `litellm.completion()` call returns a response *with* tool calls. The agent then executes those tool calls (which takes additional time), the loop exhausts, and the function returns. The background callback from that final litellm call was spawned more recently and has not yet completed. When `asyncio.run()` cancels all remaining asyncio tasks and closes the event loop, litellm's error handler catches the cancellation and prints the Provider List URL to stderr in red.

The telemetry serves **only** BerriAI (the creators of litellm): it collects anonymous usage metrics (model names, call frequency, latencies, errors) to improve the library. It provides no benefit to the consuming project. Disabling it is the standard recommendation for CI/CD environments where uncontrolled outbound network traffic is undesirable.

### 3. Dynamic formula underestimated the required budget

The formula in `FixWithAI.groovy` was:

```groovy
// Before
dynamicCandidate = minIterations + ceil(openIssues / issuesPerIteration)
//               = 25            + ceil(openIssues / 3)
```

With the default `issuesPerIteration = 3`, each group of three issues received exactly **one** extra iteration — roughly 0.33 iterations per issue. In practice, fixing a single issue requires several steps: querying SonarQube for details, reading the affected file, editing it, running tests, and handling any failures. That typically takes 3–6 iterations per issue, not a fraction of one.

The formula also had a low ceiling (`maxIterationsCap = 120`) and a small base (`minIterations = 25`). A project with 30 open issues would receive at most `25 + ceil(30/3) = 35` iterations — far too few for meaningful work.

### 4. Missing defaults on `base_url` and `extra_body`

```python
# Before — required positional parameters
async def run_agent_loop(
    tool_to_session,
    openai_tools,
    model,
    base_url,        # no default
    extra_body,      # no default
    system_prompt,
    ...
):
```

The test in `tests/python/test_agent_loop.py` calls `run_agent_loop` without these arguments and would raise `TypeError` at runtime.

## Fix Applied

### `FixWithAI.groovy` — dynamic iteration budget

A new parameter `iterationsPerIssue` was introduced as a multiplier in the formula:

```groovy
// After
dynamicCandidate = minIterations + ceil(openIssues / issuesPerIteration) * iterationsPerIssue
//               = 30            + ceil(openIssues / 1)                  * 4
//               = 30            + openIssues * 4
```

The defaults were updated accordingly:

| Parameter | Before | After | Role |
|-----------|--------|-------|------|
| `minIterations` | 25 | 30 | Base budget regardless of issue count |
| `maxIterationsCap` | 120 | 200 | Upper bound |
| `issuesPerIteration` | 3 | 1 | Issues treated as one group |
| `iterationsPerIssue` | — | 4 | Iterations allocated per group |

`iterationsPerIssue = 4` reflects that fixing one issue realistically takes around four agent steps (read issue → read file → edit → run tests). All parameters remain overridable from the `Jenkinsfile`.

Effect on representative issue counts:

| Open issues | Budget before | Budget after |
|-------------|---------------|--------------|
| 5  | 27 | 50  |
| 15 | 30 | 90  |
| 30 | 35 | 150 |
| 40 | 39 | 190 |
| 50 | 42 | 200 (capped) |



### `agent_loop.py`

`base_url` and `extra_body` were given `None` defaults and moved after the required parameters so existing keyword-argument call sites are unaffected:

```python
async def run_agent_loop(
    tool_to_session,
    openai_tools,
    model,
    system_prompt,
    repo_slug,
    source_branch,
    sonarqube_project_key,
    base_url=None,
    extra_body=None,
    max_iterations=25,
    dry_run=False,
):
```

litellm telemetry is disabled and debug output suppressed before the first litellm call:

```python
os.environ.setdefault("LITELLM_TELEMETRY", "False")
import litellm
litellm.suppress_debug_info = True
```

`setdefault` is used so the env var can still be overridden from outside the process if needed.

### `entrypoint.py`

Status is now derived from the last message role instead of being hardcoded:

```python
messages = await run_agent_loop(...)
if messages and messages[-1].get("role") == "tool":
    status = "max_iterations_reached"
else:
    status = "completed"
```

The completion log line was updated to reflect the actual outcome:

```python
if status == "max_iterations_reached":
    log(f"\n⚠️  Agent stopped at max iterations. Total messages exchanged: {len(messages)}")
else:
    log(f"\n✅ Agent completed. Total messages exchanged: {len(messages)}")
```

## Outcome

After the fix, reaching max iterations produces:

```
⚠️  Max iterations (30) reached. Agent stopped.
⚠️  Agent stopped at max iterations. Total messages exchanged: 62
🛑 Stopping SonarQube Docker container: mcp-sonarqube-11175
   ✅ SonarQube container stopped gracefully
```

No spurious litellm output follows. The artifact records `status: "max_iterations_reached"` instead of the misleading `"completed"`.
