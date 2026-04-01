import org.junit.jupiter.api.Test
import support.PipelineScriptLoader
import support.PipelineTestScript

import static org.junit.jupiter.api.Assertions.assertThrows
import static org.junit.jupiter.api.Assertions.assertTrue

class FixWithAITest {
    private final File repoRoot = new File(".")

    private PipelineTestScript loadScript() {
        PipelineScriptLoader.loadScript(repoRoot, "vars/FixWithAI.groovy")
    }

    @Test
    void "cleans up ai_fixer even when agent execution fails"() {
        PipelineTestScript script = loadScript()
        script.resources.put("scripts/mcp_agent.py", "print('agent')")
        script.resources.put("scripts/mcp_servers/test_runner_server.py", "print('runner')")
        script.resources.put("scripts/requirements-ai.txt", "litellm")
        script.env.put("SONARQUBE_URL", "http://sonarqube.local")
        script.env.put("SONARQUBE_EFFECTIVE_PROJECT_KEY", "project-key")
        script.env.put("BRANCH_NAME", "main")
        script.env.put("WORKSPACE", "/tmp/workspace")
        script.failOnShellCall = 2

        assertThrows(RuntimeException) {
            script.call([
                repoSlug: "owner/repo",
                llmCredentialId: "LLM_TOKEN",
                githubCredentialId: "GITHUB_PAT",
                sonarqubeCredentialId: "SONAR_TOKEN",
            ])
        }

        assertTrue(script.shellCommands.any { it.contains("rm -rf .ai_fixer") })
    }

    @Test
    void "exports reports directory and dry run flag to the agent runtime"() {
        PipelineTestScript script = loadScript()
        script.resources.put("scripts/mcp_agent.py", "print('agent')")
        script.resources.put("scripts/mcp_servers/test_runner_server.py", "print('runner')")
        script.resources.put("scripts/requirements-ai.txt", "litellm")
        script.env.put("SONARQUBE_URL", "http://sonarqube.local")
        script.env.put("SONARQUBE_EFFECTIVE_PROJECT_KEY", "project-key")
        script.env.put("BRANCH_NAME", "main")
        script.env.put("WORKSPACE", "/tmp/workspace")

        script.call([
            repoSlug: "owner/repo",
            llmCredentialId: "LLM_TOKEN",
            githubCredentialId: "GITHUB_PAT",
            sonarqubeCredentialId: "SONAR_TOKEN",
            reportsDir: "reports_for_IA",
            dryRun: true,
        ])

        String command = script.shellCommands.find { it.contains("python3 .ai_fixer/mcp_agent.py") }
        assertTrue(command.contains("export AI_REPORTS_DIR='reports_for_IA'"))
        assertTrue(command.contains("--dry-run"))
    }
}
