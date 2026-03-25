def call(Map config = [:]) {
    // ── Parámetros con valores por defecto ──
    def reportsDir    = config.reportsDir    ?: (env.AI_REPORTS_DIR ?: 'reports_for_IA')
    def llmModel      = config.llmModel      ?: 'gpt-4o'
    def llmCredentialId    = config.llmCredentialId    ?: 'Gemini_Api_token'
    def githubCredentialId = config.githubCredentialId ?: 'GITHUB_PAT'
    def repoSlug      = config.repoSlug      ?: ''
    def dryRun        = config.dryRun        ?: false
    def useMcp        = config.useMcp        ?: true
    def maxIterations = config.maxIterations  ?: 25
    def sonarqubeCredentialId = config.sonarqubeCredentialId ?: 'SONARQUBE_TOKEN'
    def sonarqubeUrl  = config.sonarqubeUrl   ?: (env.SONARQUBE_URL ?: '')
    def sonarqubeProjectKey = config.sonarqubeProjectKey ?: (env.SONARQUBE_EFFECTIVE_PROJECT_KEY ?: '')

    script {
        // ── 1. Extraer scripts de la shared library ──
        def aiFixerScript   = libraryResource 'scripts/ai_fixer.py'
        def aiRequirements  = libraryResource 'scripts/requirements-ai.txt'

        writeFile file: '.ai_fixer/ai_fixer.py', text: aiFixerScript
        writeFile file: '.ai_fixer/requirements-ai.txt', text: aiRequirements

        if (useMcp) {
            // Extraer MCP agent + test runner server
            def mcpAgentScript = libraryResource 'scripts/mcp_agent.py'
            def testRunnerScript = libraryResource 'scripts/mcp_servers/test_runner_server.py'

            writeFile file: '.ai_fixer/mcp_agent.py', text: mcpAgentScript
            sh 'mkdir -p .ai_fixer/mcp_servers'
            writeFile file: '.ai_fixer/mcp_servers/__init__.py', text: ''
            writeFile file: '.ai_fixer/mcp_servers/test_runner_server.py', text: testRunnerScript
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
            string(credentialsId: githubCredentialId, variable: 'Github_AI_Auth')
        ]
        if (useMcp && sonarqubeCredentialId) {
            credentialBindings.add(string(credentialsId: sonarqubeCredentialId, variable: 'SONARQUBE_TOKEN_VALUE'))
        }

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

        // ── 5. Ejecutar con credenciales inyectadas ──
        withCredentials(credentialBindings) {
            // Configurar git para commits (necesario para ambos modos)
            sh '''
                git config user.name "Jenkins AI Bot"
                git config user.email "jenkins-ai@noreply.github.com"
            '''

            sh """
                git remote set-url origin https://x-access-token:\${Github_AI_Auth}@github.com/${repoSlug}.git
            """

            if (useMcp) {
                // ── MCP Agent mode ──
                echo "🤖 Using MCP Agent mode"

                sh """
                    python3 -m venv .ai_fixer/venv
                    . .ai_fixer/venv/bin/activate
                    pip install -r .ai_fixer/requirements-ai.txt > /dev/null 2>&1

                    export LLM_MODEL='${resolvedModel}'
                    export ${envKeyName}="\${LLM_API_KEY_VALUE}"
                    export GITHUB_PERSONAL_ACCESS_TOKEN="\${Github_AI_Auth}"
                    export SONARQUBE_URL='${sonarqubeUrl}'
                    export SONARQUBE_TOKEN="\${SONARQUBE_TOKEN_VALUE}"
                    export SONARQUBE_EFFECTIVE_PROJECT_KEY='${sonarqubeProjectKey}'

                    python3 .ai_fixer/mcp_agent.py \
                        --repo '${repoSlug}' \
                        --source-branch '${sourceBranch}' \
                        --workspace '${env.WORKSPACE}' \
                        --max-iterations ${maxIterations} \
                        ${dryRunFlag}
                """
            } else {
                // ── Legacy script mode (ai_fixer.py) ──
                echo "📜 Using legacy script mode (ai_fixer.py)"

                // Cargar reportes desde carpeta reports_for_IA
                if (!fileExists(reportsDir)) {
                    echo "⚠️  No existe la carpeta de reportes: ${reportsDir}. Omitiendo AI fix."
                    return
                }

                def existingReportsRaw = sh(
                    script: "find '${reportsDir}' -type f | sort",
                    returnStdout: true
                ).trim()

                def existingReports = existingReportsRaw ? existingReportsRaw.split('\n') as List : []
                if (existingReports.isEmpty()) {
                    echo "⚠️  No se encontraron archivos dentro de ${reportsDir}. Omitiendo AI fix."
                    return
                }
                echo "📄 Reportes encontrados en ${reportsDir}: ${existingReports}"

                def reportsArg = existingReports.collect { "'${it}'" }.join(' ')

                sh """
                    python3 -m venv .ai_fixer/venv
                    . .ai_fixer/venv/bin/activate
                    pip install -r .ai_fixer/requirements-ai.txt > /dev/null 2>&1

                    export LLM_MODEL='${resolvedModel}'
                    export ${envKeyName}="\${LLM_API_KEY_VALUE}"

                    python3 .ai_fixer/ai_fixer.py \
                        --reports ${reportsArg} \
                        --repo '${repoSlug}' \
                        --source-branch '${sourceBranch}' \
                        --workspace '${env.WORKSPACE}' \
                        ${dryRunFlag}
                """
            }
        }

        // ── 7. Limpieza ──
        sh 'rm -rf .ai_fixer'
    }
}
