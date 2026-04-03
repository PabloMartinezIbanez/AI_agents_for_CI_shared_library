# Test Configuration Contract

The local MCP test runner reads a JSON file that declares how the repository test suites should be discovered and executed.

By default the runner expects `.ai-tests.json` in the workspace root.

The demo repository overrides that default with `ai-tests-config.json`, passed through `FixWithAI(testConfigFile: 'ai-tests-config.json')`.

## Base schema

```json
{
  "test_suites": [
    {
      "name": "python-unit-tests",
      "framework": "pytest",
      "setup": "pip install -r requirements/python_requirements.txt",
      "command": "PYTHONPATH=src/calculator python3 -m pytest tests/python/test_suma.py --tb=long -v",
      "test_dir": "tests/python/",
      "timeout": 120
    }
  ]
}
```

## Required fields

- `test_suites`: non-empty array.
- `name`: unique string per suite.
- `command`: string command to execute.

## Optional fields

- `framework`: `pytest`, `node`, or `generic`. Default: `generic`.
- `setup`: preparation command run before the suite command.
- `test_dir`: informational path for the suite location.
- `timeout`: positive number in seconds. Default: `120`.

## Security model

The runner is configuration-driven and should only be fed trusted repository configuration.

Hardening currently enforced:

- `subprocess.run(..., shell=False)` is always used.
- `extra_args` rejects shell-like separators such as `;`, `&&`, `||`, `|`, redirections, and command-substitution markers.
- `pytest` suites must use an approved executable: `pytest`, `python`, `python3`, or `py`.
- `node` suites must use an approved executable: `node`, `nodejs`, `npm`, or `npx`.
- `setup` commands must use an approved executable from the Python/Node toolchain set.
- `test_dir`, when present, must stay inside the workspace.

What is still intentionally flexible:

- `generic` suites remain available for non-standard runners.
- repository authors still choose the exact command arguments for trusted use cases.

## Demo repository mapping

The current demo repository defines two suites:

- `python-unit-tests`
- `javascript-unit-tests`

It uses `ai-tests-config.json` only as an override name. The JSON structure is the same contract understood by the base `.ai-tests.json` loader.
