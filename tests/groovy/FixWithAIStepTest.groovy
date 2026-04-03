import groovy.lang.GroovyShell
import org.junit.jupiter.api.Test

import static org.junit.jupiter.api.Assertions.assertThrows
import static org.junit.jupiter.api.Assertions.assertTrue

class FixWithAIStepTest {

    @Test
    void "exports reports dir and clean SonarQube project key"() {
        def harness = new FixWithAIHarness()
        def script = harness.load()

        script.call(
            llmCredentialId: 'llm',
            githubCredentialId: 'github',
            sonarqubeCredentialId: 'sonar',
            repoSlug: 'owner/repo',
            testConfigFile: 'ai-tests-config.json',
            reportsDir: 'reports_for_IA'
        )

        def command = harness.shellCommands.find { it.contains('python3 .ai_fixer/mcp_agent.py') }
        assertTrue(command.contains("mkdir -p 'reports_for_IA'"))
        assertTrue(command.contains("export AGENT_REPORTS_DIR='reports_for_IA'"))
        assertTrue(command.contains("export SONARQUBE_EFFECTIVE_PROJECT_KEY='demo:key'"))
        assertTrue(!command.contains("demo:key''"))
    }

    @Test
    void "fails preflight when configured test file is missing"() {
        def harness = new FixWithAIHarness()
        harness.existingFiles.clear()
        def script = harness.load()

        def error = assertThrows(RuntimeException) {
            script.call(
                repoSlug: 'owner/repo',
                testConfigFile: 'missing.json'
            )
        }

        assertTrue(error.message.contains('testConfigFile'))
    }

    @Test
    void "copies the internal mcp_agent_pkg runtime resources"() {
        def harness = new FixWithAIHarness()
        def script = harness.load()

        script.call(repoSlug: 'owner/repo')

        def normalizedFiles = harness.writtenFiles.collect { it.replace('\\', '/') }
        assertTrue(normalizedFiles.any { it.endsWith('/mcp_agent_pkg/agent_loop.py') })
        assertTrue(normalizedFiles.any { it.endsWith('/mcp_agent_pkg/entrypoint.py') })
        assertTrue(normalizedFiles.any { it.endsWith('/mcp_agent_pkg/system_prompt.md') })
    }

    @Test
    void "escapes shell-sensitive values before building the runtime command"() {
        def harness = new FixWithAIHarness()
        harness.env.SONARQUBE_URL = "http://host/'sq"
        harness.env.SONARQUBE_EFFECTIVE_PROJECT_KEY = "demo'key"
        def script = harness.load()

        script.call(
            repoSlug: 'owner/repo',
            reportsDir: "reports_'IA",
            testConfigFile: "ai-tests-config.json"
        )

        def command = harness.shellCommands.find { it.contains('python3 .ai_fixer/mcp_agent.py') }
        assertTrue(command.contains("mkdir -p 'reports_'\"'\"'IA'"))
        assertTrue(command.contains("export SONARQUBE_URL='http://host/'\"'\"'sq'"))
        assertTrue(command.contains("export SONARQUBE_EFFECTIVE_PROJECT_KEY='demo'\"'\"'key'"))
    }
}

class FixWithAIHarness {
    Map env = [
        SONARQUBE_URL                  : 'http://host.docker.internal:9000',
        SONARQUBE_EFFECTIVE_PROJECT_KEY: 'demo:key',
        BRANCH_NAME                    : 'feature/demo',
        WORKSPACE                      : '/workspace',
        AI_REPORTS_DIR                 : 'reports_for_IA',
    ]
    List<String> shellCommands = []
    List<String> writtenFiles = []
    Set<String> existingFiles = ['ai-tests-config.json'] as Set

    def load() {
        def scriptFile = new File('vars/FixWithAI.groovy')
        def script = new GroovyShell().parse(scriptFile)
        script.binding.setVariable('env', env)

        script.metaClass.script = { Closure body -> body.call() }
        script.metaClass.error = { String message -> throw new RuntimeException(message) }
        script.metaClass.echo = { String message -> null }
        script.metaClass.libraryResource = { String path -> "resource:${path}" }
        script.metaClass.writeFile = { Map args ->
            writtenFiles << args.file
            null
        }
        script.metaClass.fileExists = { String path -> existingFiles.contains(path) }
        script.metaClass.string = { Map args -> args }
        script.metaClass.withCredentials = { List bindings, Closure body -> body.call() }
        script.metaClass.sh = { Object args ->
            if (args instanceof Map) {
                if (args.returnStdout) {
                    return 'feature/demo'
                }
                shellCommands << args.script.toString()
                return 0
            }
            shellCommands << args.toString()
            0
        }

        script
    }
}
