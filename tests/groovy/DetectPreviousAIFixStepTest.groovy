import groovy.lang.GroovyShell
import org.junit.jupiter.api.Test

import static org.junit.jupiter.api.Assertions.assertFalse
import static org.junit.jupiter.api.Assertions.assertThrows
import static org.junit.jupiter.api.Assertions.assertTrue

class DetectPreviousAIFixStepTest {

    @Test
    void "returns true when merged ai-fix PR exists for source branch"() {
        def harness = new DetectPreviousAIFixHarness()
        harness.readFilePayload = '''
[
  {
    "merged_at": "2026-04-13T10:00:00Z",
    "head": { "ref": "ai-fix/feature-login-20260413-100000" }
  }
]
'''
        def script = harness.load()

        def result = script.call(repoSlug: 'owner/repo', sourceBranch: 'feature-login')

        assertTrue(result)
        assertTrue(harness.shellCommands.any { it.contains('curl -fsSL') })
    }

    @Test
    void "returns false when github api request fails"() {
        def harness = new DetectPreviousAIFixHarness()
        harness.apiStatus = 22
        def script = harness.load()

        def result = script.call(repoSlug: 'owner/repo', sourceBranch: 'feature-login')

        assertFalse(result)
    }

    @Test
    void "fails preflight when repo slug is invalid"() {
        def harness = new DetectPreviousAIFixHarness()
        def script = harness.load()

        def error = assertThrows(RuntimeException) {
            script.call(repoSlug: 'invalid-slug')
        }

        assertTrue(error.message.contains('repoSlug'))
    }
}

class DetectPreviousAIFixHarness {
    Map env = [
        CHANGE_BRANCH : 'feature-login',
        AI_REPORTS_DIR: 'reports_for_IA',
        Github_AI_Auth: 'token-value',
    ]
    List<String> shellCommands = []
    int apiStatus = 0
    String readFilePayload = '[]'

    def load() {
        def scriptFile = new File('vars/DetectPreviousAIFix.groovy')
        def script = new GroovyShell().parse(scriptFile)
        script.binding.setVariable('env', env)

        script.metaClass.script = { Closure body -> body.call() }
        script.metaClass.error = { String message -> throw new RuntimeException(message) }
        script.metaClass.echo = { String message -> null }
        script.metaClass.withEnv = { List values, Closure body -> body.call() }
        script.metaClass.readFile = { String path -> readFilePayload }
        script.metaClass.sh = { Object args ->
            if (args instanceof Map) {
                shellCommands << args.script.toString()
                if (args.returnStatus) {
                    return apiStatus
                }
                return 0
            }
            shellCommands << args.toString()
            0
        }

        script
    }
}