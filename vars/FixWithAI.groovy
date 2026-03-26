def call(Map config = [:]) {
    // ── Parámetros con valores por defecto ──
    def llmModel      = config.llmModel      ?: 'gemini-3.1-pro-preview'
    def llmCredentialId    = config.llmCredentialId    ?: 'Gemini_Api_token'
    def githubCredentialId = config.githubCredentialId ?: 'GITHUB_PAT'
    def repoSlug      = config.repoSlug      ?: ''
    def dryRun        = config.dryRun        ?: false
    def maxIterations = config.maxIterations  ?: 25
    def sonarqubeCredentialId = config.sonarqubeCredentialId ?: 'SONARQUBE_TOKEN'
    def sonarqubeUrl  = config.sonarqubeUrl   ?: (env.SONARQUBE_URL ?: '')
    def sonarqubeProjectKey = config.sonarqubeProjectKey ?: (env.SONARQUBE_EFFECTIVE_PROJECT_KEY ?: '')

    script {
        // ── 1. Extraer scripts MCP de la shared library ──
        def mcpAgentScript  = libraryResource 'scripts/mcp_agent.py'
        def testRunnerScript = libraryResource 'scripts/mcp_servers/test_runner_server.py'
        def aiRequirements  = libraryResource 'scripts/requirements-ai.txt'

        writeFile file: '.ai_fixer/mcp_agent.py', text: mcpAgentScript
        writeFile file: '.ai_fixer/requirements-ai.txt', text: aiRequirements
        sh 'mkdir -p .ai_fixer/mcp_servers'
        writeFile file: '.ai_fixer/mcp_servers/__init__.py', text: ''
        writeFile file: '.ai_fixer/mcp_servers/test_runner_server.py', text: testRunnerScript

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

        // ── 4. Validar configuración requerida para MCP ──
        if (!sonarqubeUrl?.trim()) {
            error 'SONARQUBE_URL no está definido. Pásalo en config.sonarqubeUrl o en env.SONARQUBE_URL.'
        }
        if (!sonarqubeProjectKey?.trim()) {
            error 'SONARQUBE_EFFECTIVE_PROJECT_KEY no está definido. Pásalo en config.sonarqubeProjectKey o en env.SONARQUBE_EFFECTIVE_PROJECT_KEY.'
        }

        // ── 5. Determinar credenciales necesarias ──
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

        // ── 6. Ejecutar MCP agent con credenciales inyectadas ──
        withCredentials(credentialBindings) {
            echo "🤖 Using MCP Agent mode"

            sh """
                python3 -m venv .ai_fixer/venv > /dev/null 2>&1
                . .ai_fixer/venv/bin/activate > /dev/null 2>&1
                pip install -r .ai_fixer/requirements-ai.txt > /dev/null 2>&1

                export LLM_MODEL='${resolvedModel}'
                export ${envKeyName}="\${LLM_API_KEY_VALUE}"
                export GITHUB_PERSONAL_ACCESS_TOKEN="\${Github_AI_Auth}"
                export SONARQUBE_URL='${sonarqubeUrl}'
                export SONARQUBE_TOKEN="\${SONARQUBE_TOKEN}"
                export SONARQUBE_EFFECTIVE_PROJECT_KEY='${sonarqubeProjectKey}'

                python3 .ai_fixer/mcp_agent.py \
                    --repo '${repoSlug}' \             
                    --model '${resolvedModel}' \                 
                    --source-branch '${sourceBranch}' \
                    --workspace '${env.WORKSPACE}' \
                    --max-iterations ${maxIterations} \
                    ${dryRunFlag}
            """
        }

        // ── 7. Limpieza ──
        sh 'rm -rf .ai_fixer'
    }
}
