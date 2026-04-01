import json
import sys


def test_run_suite_does_not_execute_extra_args_through_the_shell(
    load_module, mcp_stubs, tmp_path
):
    module = load_module(
        "test_runner_server_module",
        "resources/scripts/mcp_servers/test_runner_server.py",
        stubs=mcp_stubs,
    )

    script_path = tmp_path / "print_safe.py"
    script_path.write_text("print('SAFE')\n", encoding="utf-8")

    suite = {
        "name": "safe-suite",
        "framework": "generic",
        "command": f"\"{sys.executable}\" \"{script_path}\"",
        "timeout": 30,
    }

    result = module.run_suite(str(tmp_path), suite, extra_args="&& echo INJECTED")

    assert result["passed"] is True
    assert result["output"].strip() == "SAFE"


def test_discover_tests_uses_the_override_config_file(load_module, mcp_stubs, monkeypatch, tmp_path):
    module = load_module(
        "test_runner_server_module",
        "resources/scripts/mcp_servers/test_runner_server.py",
        stubs=mcp_stubs,
    )

    config_path = tmp_path / "custom-tests.json"
    config_path.write_text(
        json.dumps(
            {
                "test_suites": [
                    {
                        "name": "python-unit-tests",
                        "command": "python -m pytest tests",
                        "framework": "pytest",
                        "timeout": 30,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AI_TEST_CONFIG_FILE", str(config_path))

    result = module.discover_tests(str(tmp_path))

    assert result["configured"] is True
    assert result["config_file"] == str(config_path)
    assert result["test_suites"][0]["name"] == "python-unit-tests"
