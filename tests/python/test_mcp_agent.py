import asyncio
import json
import types


def test_build_server_configs_forwards_reports_dir_to_the_test_runner(
    load_module, mcp_stubs, monkeypatch, tmp_path
):
    module = load_module(
        "mcp_agent_module",
        "resources/scripts/mcp_agent.py",
        stubs=mcp_stubs,
    )

    server_path = tmp_path / ".ai_fixer" / "mcp_servers"
    server_path.mkdir(parents=True)
    (server_path / "test_runner_server.py").write_text("# stub\n", encoding="utf-8")

    servers, _ = module.build_server_configs(
        workspace=str(tmp_path),
        sonarqube_url="http://localhost:9000",
        sonarqube_token="token",
        sonarqube_project_key="project-key",
        github_token="github-token",
    )



def test_async_main_writes_agent_artifacts_and_uses_github_pat_fallback(
    load_module, mcp_stubs, monkeypatch, tmp_path
):
    module = load_module(
        "mcp_agent_module",
        "resources/scripts/mcp_agent.py",
        stubs=mcp_stubs,
    )

    captured = {}
    reports_dir = tmp_path / "reports_for_IA"

    monkeypatch.setenv("LLM_MODEL", "gemini/test-model")
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_test_token")
    monkeypatch.delenv("Github_AI_Auth", raising=False)
    monkeypatch.setenv("SONARQUBE_URL", "http://sonarqube.local")
    monkeypatch.setenv("SONARQUBE_TOKEN", "sonar-token")
    monkeypatch.setenv("SONARQUBE_EFFECTIVE_PROJECT_KEY", "demo-project")
    def fake_build_server_configs(**kwargs):
        captured.update(kwargs)
        return {"sonarqube": object()}, None

    async def fake_connect_servers(server_configs, exit_stack):
        tool = types.SimpleNamespace(
            name="run_tests",
            description="Run configured tests",
            inputSchema={"type": "object", "properties": {}},
        )
        return {"sonarqube": object()}, [tool], {}

    async def fake_run_agent_loop(**kwargs):
        return [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "completed"},
            {
                "role": "tool",
                "content": json.dumps(
                    {
                        "results": [
                            {
                                "name": "python-unit-tests",
                                "status": "passed",
                                "passed": True,
                            }
                        ]
                    }
                ),
            },
        ]

    monkeypatch.setattr(module, "build_server_configs", fake_build_server_configs)
    monkeypatch.setattr(module, "connect_servers", fake_connect_servers)
    monkeypatch.setattr(module, "run_agent_loop", fake_run_agent_loop)

    args = types.SimpleNamespace(
        workspace=str(tmp_path),
        model=None,
        repo="owner/repo",
        source_branch="main",
        max_iterations=3,
        dry_run=True,
    )

    asyncio.run(module.async_main(args))

    assert captured["github_token"] == "ghp_test_token"

    summary = json.loads((reports_dir / "agent_summary.json").read_text(encoding="utf-8"))
    validation = json.loads(
        (reports_dir / "validation_results.json").read_text(encoding="utf-8")
    )

    assert summary["repo"] == "owner/repo"
    assert summary["dryRun"] is True
    assert validation["results"][0]["name"] == "python-unit-tests"
