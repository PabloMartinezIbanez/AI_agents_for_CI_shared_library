import importlib
import json

import pytest


MODULE_NAME = "mcp_servers.test_runner_server"


def _load_module(monkeypatch, workspace):
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.delenv("AI_TEST_CONFIG_FILE", raising=False)
    module = importlib.import_module(MODULE_NAME)
    return importlib.reload(module)


def test_prepare_command_rejects_unsafe_extra_args(tmp_path, monkeypatch):
    module = _load_module(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="unsafe"):
        module._prepare_command("python -m pytest tests", extra_args="; rm -rf /")


def test_load_config_rejects_unapproved_pytest_executable(tmp_path, monkeypatch):
    config_path = tmp_path / ".ai-tests.json"
    config_path.write_text(
        json.dumps(
            {
                "test_suites": [
                    {
                        "name": "bad-pytest-suite",
                        "framework": "pytest",
                        "command": "bash run-tests.sh",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    module = _load_module(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="approved executable"):
        module._load_config(str(tmp_path))
