# Test configuration contract

The shared library test runner understands a base JSON contract named `.ai-tests.json`. The demo repository uses `ai-tests-config.json` as an explicit override passed through `FixWithAI(...)`.

## Resolution order

1. If `AI_TEST_CONFIG_FILE` is defined, the test runner loads that file.
2. Otherwise it falls back to `.ai-tests.json` in the workspace root.

This is why the demo repository can keep `ai-tests-config.json` while the underlying MCP server still documents `.ai-tests.json` as the default convention.

## Expected JSON format

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

## Supported fields

- `name`: unique identifier for the suite.
- `framework`: one of `pytest`, `node`, or `generic`.
- `setup`: optional command executed before the suite.
- `command`: required command that runs the suite.
- `test_dir`: optional informational field.
- `timeout`: positive timeout in seconds.

## Execution and security notes

- Commands are executed as subprocesses without `shell=True`.
- Leading environment assignments such as `PYTHONPATH=src/calculator` are supported.
- `extra_args` are tokenized and appended as arguments, not shell-evaluated.
- Invalid or malformed commands are reported as structured runner errors.

## Demo repository example

The reference application `AI-agents-for-continuous-integration` passes:

- `testConfigFile: 'ai-tests-config.json'`

Its current suites cover:

- Python with `pytest`
- JavaScript with `node --test`
