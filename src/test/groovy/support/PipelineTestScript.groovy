package support

abstract class PipelineTestScript extends Script {
    Map env = [:]
    Map resources = [:]
    List<Map> writtenFiles = []
    List<String> shellCommands = []
    List<String> echoes = []
    List<List<Map>> credentialBindings = []
    int shellCallCount = 0
    int failOnShellCall = -1

    def libraryResource(String path) {
        return resources.get(path, "# stub ${path}")
    }

    def writeFile(Map args) {
        writtenFiles << args
    }

    def sh(Object args) {
        shellCallCount += 1
        if (failOnShellCall == shellCallCount) {
            throw new RuntimeException("simulated shell failure")
        }

        if (args instanceof Map) {
            shellCommands << String.valueOf(args.script)
            if (args.returnStdout) {
                return ""
            }
            return 0
        }

        shellCommands << String.valueOf(args)
        return 0
    }

    def script(Closure body) {
        body.delegate = this
        body.resolveStrategy = Closure.DELEGATE_FIRST
        body.call()
    }

    def echo(String message) {
        echoes << message
    }

    def error(String message) {
        throw new RuntimeException(message)
    }

    def withCredentials(List bindings, Closure body) {
        credentialBindings << bindings
        body.call()
    }

    def string(Map args) {
        return args
    }
}
