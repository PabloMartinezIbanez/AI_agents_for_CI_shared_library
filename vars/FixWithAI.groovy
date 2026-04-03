def call(Map config = [:]) {
    // ── Parámetros con valores por defecto ──
    def llmModel = config.llmModel ?: 'gemini-3.1-pro-preview'
    def llmCredentialId = config.llmCredentialId ?: 'Gemini_Api_token'
    def githubCredentialId = config.githubCredentialId ?: 'GITHUB_PAT'
    def repoSlug = config.repoSlug ?: ''
    def dryRun = config.dryRun ?: false
    def maxIterations = config.maxIterations ?: 25
    def sonarqubeCredentialId = config.sonarqubeCredentialId ?: 'SONARQUBE_TOKEN'
    def sonarqubeUrl = config.sonarqubeUrl ?: (env.SONARQUBE_URL ?: '')
    def sonarqubeProjectKey = config.sonarqubeProjectKey ?: (env.SONARQUBE_EFFECTIVE_PROJECT_KEY ?: '')
    def testConfigFile = config.testConfigFile ?: ''
    def reportsDir = (config.reportsDir ?: (env.AI_REPORTS_DIR ?: 'reports_for_IA')).toString().trim()
    def shellQuote = { value ->
        def normalized = (value ?: '').toString().replace("'", "'\"'\"'")
        return "'${normalized}'"
    }

    script {
        def preparedAiFixer = false

        try {
            if (!sonarqubeUrl?.trim()) {
                error 'SONARQUBE_URL no está definido. Pásalo en config.sonarqubeUrl o en env.SONARQUBE_URL.'
            }
            if (!sonarqubeProjectKey?.trim()) {
                error 'SONARQUBE_EFFECTIVE_PROJECT_KEY no está definido. Pásalo en config.sonarqubeProjectKey o en env.SONARQUBE_EFFECTIVE_PROJECT_KEY.'
            }
            if (!(maxIterations instanceof Number) || maxIterations <= 0) {
                error 'maxIterations debe ser un número positivo.'
            }
            if (!reportsDir) {
                error 'reportsDir no puede estar vacío.'
            }
            if (reportsDir.contains('..')) {
                error "reportsDir '${reportsDir}' no puede contener '..'."
            }
            if (repoSlug?.trim() && !(repoSlug ==~ /[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/)) {
                error "repoSlug '${repoSlug}' no tiene el formato esperado owner/repo."
            }
            if (testConfigFile?.trim() && !fileExists(testConfigFile)) {
                error "testConfigFile '${testConfigFile}' no existe en el workspace."
            }

            // ── 1. Extraer scripts MCP de la shared library ──
            def mcpAgentScript = libraryResource 'scripts/mcp_agent.py'
            def testRunnerScript = libraryResource 'scripts/mcp_servers/test_runner_server.py'
            def aiRequirements = libraryResource 'scripts/requirements-ai.txt'
            def mcpAgentPackageResources = [
                'scripts/mcp_agent_pkg/__init__.py',
                'scripts/mcp_agent_pkg/agent_loop.py',
                'scripts/mcp_agent_pkg/artifacts.py',
                'scripts/mcp_agent_pkg/entrypoint.py',
                'scripts/mcp_agent_pkg/env_config.py',
                'scripts/mcp_agent_pkg/logging_utils.py',
                'scripts/mcp_agent_pkg/mcp_client.py',
                'scripts/mcp_agent_pkg/servers.py',
                'scripts/mcp_agent_pkg/system_prompt.md',
            ]

            sh 'mkdir -p .ai_fixer .ai_fixer/mcp_servers .ai_fixer/mcp_agent_pkg'
            preparedAiFixer = true
            writeFile file: '.ai_fixer/mcp_agent.py', text: mcpAgentScript
            writeFile file: '.ai_fixer/requirements-ai.txt', text: aiRequirements
            writeFile file: '.ai_fixer/mcp_servers/__init__.py', text: ''
            writeFile file: '.ai_fixer/mcp_servers/test_runner_server.py', text: testRunnerScript
            for (resourcePath in mcpAgentPackageResources) {
                def targetPath = resourcePath.replaceFirst(/^scripts\//, '')
                writeFile file: ".ai_fixer/${targetPath}", text: libraryResource(resourcePath)
            }

            // ── 2. Determinar rama actual ──
            // En Jenkins puede haber detached HEAD; priorizamos variables del job.
            def sourceBranch = env.CHANGE_BRANCH ?: env.BRANCH_NAME ?: env.GIT_LOCAL_BRANCH ?: ''
            if (!sourceBranch?.trim()) {
                sourceBranch = env.GIT_BRANCH ?: ''
            }
            if (sourceBranch?.startsWith('origin/')) {
                sourceBranch = sourceBranch - 'origin/'
            }
            if (!sourceBranch?.trim() || sourceBranch == 'HEAD') {
                sourceBranch = sh(
                    script: "git symbolic-ref --short -q HEAD || git branch --contains HEAD --format='%(refname:short)' | head -n 1 || git rev-parse --abbrev-ref HEAD",
                    returnStdout: true
                ).trim()
            }
            if (!sourceBranch?.trim() || sourceBranch == 'HEAD') {
                error 'No se pudo determinar la rama fuente. Define BRANCH_NAME/CHANGE_BRANCH o pasa sourceBranch explícitamente.'
            }

            // ── 2b. Evitar bucle infinito: no ejecutar FixWithAI en ramas creadas por la IA ──
            if (sourceBranch.startsWith('ai-fix/')) {
                echo "⏭️  Rama '${sourceBranch}' fue creada por la IA. Se omite FixWithAI para evitar bucle infinito."
                return
            }

            echo "🔀 Rama actual: ${sourceBranch}"

            // ── 3. Inferir repo slug si no se proporcionó ──
            if (!repoSlug) {
                def remoteUrl = sh(script: 'git remote get-url origin', returnStdout: true).trim()
                // Soporta https://github.com/owner/repo.git y git@github.com:owner/repo.git
                def matcher = remoteUrl =~ /(?:github\.com[:\\/])([^\\/]+\\/[^\\/]+?)(?:\.git)?$/
                if (matcher.find()) {
                    repoSlug = matcher.group(1)
                } else {
                    error "No se pudo inferir el repo slug de: ${remoteUrl}. Pásalo explícitamente con repoSlug."
                }
                echo "📦 Repo inferido: ${repoSlug}"
            }

            // ── 4. Determinar credenciales necesarias ──
            def credentialBindings = [
                string(credentialsId: llmCredentialId, variable: 'LLM_API_KEY_VALUE'),
                string(credentialsId: githubCredentialId, variable: 'Github_AI_Auth'),
                string(credentialsId: sonarqubeCredentialId, variable: 'SONARQUBE_TOKEN')
            ]

            // Determinar la variable de entorno correcta para el proveedor del LLM
            def envKeyName = 'OPENAI_API_KEY'
            def resolvedModel = llmModel
            if (llmModel.startsWith('claude') || llmModel.startsWith('anthropic/')) {
                envKeyName = 'ANTHROPIC_API_KEY'
            } else if (llmModel.startsWith('gemini/') || llmModel.startsWith('google/')) {
                envKeyName = 'GEMINI_API_KEY'
            } else if (llmModel.startsWith('gemini-') || llmModel.startsWith('gemini_')) {
                envKeyName = 'GEMINI_API_KEY'
                resolvedModel = "gemini/${llmModel}"
            } else if (llmModel.startsWith('ollama/') || llmModel.startsWith('ollama_chat/')) {
                envKeyName = 'OLLAMA_API_KEY'
            }

            def dryRunFlag = dryRun ? '--dry-run' : ''

            // ── 5. Ejecutar MCP agent con credenciales inyectadas ──
            withCredentials(credentialBindings) {
                echo "🤖 Using MCP Agent mode"

                sh """
                    set -eu
                    mkdir -p ${shellQuote(reportsDir)}
                    python3 -m venv .ai_fixer/venv > /dev/null 2>&1
                    . .ai_fixer/venv/bin/activate > /dev/null 2>&1
                    pip install -r .ai_fixer/requirements-ai.txt > /dev/null 2>&1

                    export LLM_MODEL=${shellQuote(resolvedModel)}
                    export ${envKeyName}="\${LLM_API_KEY_VALUE}"
                    export GITHUB_PERSONAL_ACCESS_TOKEN="\${Github_AI_Auth}"
                    export Github_AI_Auth="\${Github_AI_Auth}"
                    export SONARQUBE_URL=${shellQuote(sonarqubeUrl)}
                    export SONARQUBE_TOKEN="\${SONARQUBE_TOKEN}"
                    export SONARQUBE_EFFECTIVE_PROJECT_KEY=${shellQuote(sonarqubeProjectKey)}
                    export AGENT_REPORTS_DIR=${shellQuote(reportsDir)}
                    ${testConfigFile ? "export AI_TEST_CONFIG_FILE=${shellQuote(testConfigFile)}" : ''}

                    python3 .ai_fixer/mcp_agent.py \
                        --repo ${shellQuote(repoSlug)} \
                        --model ${shellQuote(resolvedModel)} \
                        --source-branch ${shellQuote(sourceBranch)} \
                        --workspace ${shellQuote(env.WORKSPACE ?: '')} \
                        --max-iterations ${maxIterations} \
                        ${dryRunFlag}
                """
            }
        } finally {
            if (preparedAiFixer) {
                sh 'rm -rf .ai_fixer'
            }
        }
    }
}
