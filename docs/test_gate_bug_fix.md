# Bug Fix: Test Gate Opens After Partial Suite Run

## The Bug

The test gate was opening even when not all test suites had passed.

In the scenario that exposed this bug, the agent ran two separate `run_tests` calls:
1. `run_tests("javascript-unit-tests")` → **FAILED** (1/4 tests failing) → gate correctly set to CLOSED
2. `run_tests("java-unit-tests")` → **PASSED** → gate incorrectly set to OPEN

After step 2, the gate reported `"All 1 configured suite(s) passed."` and allowed repository mutations (branch creation, push, PR), despite the JavaScript failures never being fixed.

## Root Cause

In `agent_loop.py`, the function `_update_test_gate_from_run_results` evaluated **only the suites returned in the current `run_tests` call**. Each call would overwrite the gate state completely:

```python
# Before the fix — overwrites state based only on current call
if failed_suites:
    test_gate["passed"] = False
    ...
    return

test_gate["passed"] = True
test_gate["summary"] = f"All {len(results)} configured suite(s) passed."
```

Since `run_tests` can be called with a single suite at a time, a passing call on any one suite was enough to open the gate, regardless of what earlier calls had returned.

## How It Was Detected

Reviewing the agent execution log, the sequence was visible:

```
Iteration 20 — run_tests("javascript-unit-tests")
  ✖ formatDate formats date for UI cards
  fail 1 / pass 3
  🧪 Test gate: CLOSED (Failing suites: javascript-unit-tests)

Iteration 21 — run_tests("java-unit-tests")
  returncode: 0 / passed: true
  🧪 Test gate: OPEN (All 1 configured suite(s) passed.)
```

The gate opened on iteration 21 reporting only 1 suite, but the JavaScript failure from iteration 20 was never resolved. The discrepancy between "1 suite passed" and the known multi-suite context was the tell.

## Fix Applied

A `suite_results` dict was added to `test_gate` to accumulate per-suite results across all `run_tests` calls. The gate now only opens when **every suite ever seen has passed**.

```python
# test_gate now includes:
"suite_results": {}  # maps suite_name -> bool, updated on every run_tests call
```

The update function was changed to merge results into this dict before evaluating the gate:

```python
# After the fix — evaluates cumulative history
for suite in results:
    suite_name = (suite.get("name") if isinstance(suite, dict) else None) or "unknown"
    test_gate["suite_results"][suite_name] = isinstance(suite, dict) and suite.get("passed", False)

failed_suites = [name for name, passed in test_gate["suite_results"].items() if not passed]
```

With this change, re-running a failing suite can recover its entry (setting it to `True`), but a passing run on a different suite no longer erases the memory of previous failures.

Additionally, `suite_results` is reset to `{}` when code is edited, so stale passing results from before a code change cannot carry over.

### Outcome of the same scenario after the fix

| Call | suite_results state | Gate |
|------|---------------------|------|
| `run_tests("javascript-unit-tests")` fails | `{"javascript-unit-tests": False}` | CLOSED |
| `run_tests("java-unit-tests")` passes | `{"javascript-unit-tests": False, "java-unit-tests": True}` | CLOSED — *Failing suites: javascript-unit-tests* |
| `run_tests("javascript-unit-tests")` passes (after fix) | `{"javascript-unit-tests": True, "java-unit-tests": True}` | OPEN |
