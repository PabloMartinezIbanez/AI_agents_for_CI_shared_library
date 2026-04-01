package support

import org.codehaus.groovy.control.CompilerConfiguration

class PipelineScriptLoader {
    static PipelineTestScript loadScript(File repoRoot, String relativePath) {
        CompilerConfiguration configuration = new CompilerConfiguration()
        configuration.scriptBaseClass = PipelineTestScript.name

        GroovyShell shell = new GroovyShell(new Binding(), configuration)
        return (PipelineTestScript) shell.parse(new File(repoRoot, relativePath))
    }
}
