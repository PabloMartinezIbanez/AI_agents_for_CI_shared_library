def call(Map config = [:]) {
    def repoSlug = (config.repoSlug ?: '').toString().trim()
    def reportsDir = (config.reportsDir ?: (env.AI_REPORTS_DIR ?: 'reports_for_IA')).toString().trim()
    def sourceBranch = (config.sourceBranch ?: (env.CHANGE_BRANCH ?: '')).toString().trim()
    def githubTokenVar = (config.githubTokenVar ?: 'Github_AI_Auth').toString().trim()
    def perPage = (config.perPage ?: 100) as int
    def shellQuote = { value ->
        def normalized = (value ?: '').toString().replace("'", "'\"'\"'")
        return "'${normalized}'"
    }

    script {
        if (!repoSlug || !(repoSlug ==~ /[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/)) {
            error "repoSlug '${repoSlug}' no tiene el formato esperado owner/repo."
        }
        if (!reportsDir) {
            error 'reportsDir no puede estar vacio.'
        }
        if (reportsDir.contains('..')) {
            error "reportsDir '${reportsDir}' no puede contener '..'."
        }
        if (!sourceBranch) {
            echo 'WARN: CHANGE_BRANCH is not available. DetectPreviousAIFix returns false.'
            return false
        }

        def githubToken = env."${githubTokenVar}"?.toString()?.trim()
        if (!githubToken) {
            echo "WARN: GitHub token env var '${githubTokenVar}' is empty. DetectPreviousAIFix returns false."
            return false
        }

        def encodedBase = java.net.URLEncoder.encode(sourceBranch, 'UTF-8')
        def apiUrl = "https://api.github.com/repos/${repoSlug}/pulls?state=closed&base=${encodedBase}&per_page=${perPage}"
        def outputPath = "${reportsDir}/closed_prs.json"

        sh "mkdir -p ${shellQuote(reportsDir)}"

        int apiStatus
        withEnv([
            "AI_FIX_API_URL=${apiUrl}",
            "AI_FIX_OUTPUT_PATH=${outputPath}",
            "AI_FIX_GITHUB_TOKEN=${githubToken}"
        ]) {
            apiStatus = sh(
                script: '''
                    curl -fsSL \
                      -H "Accept: application/vnd.github+json" \
                      -H "Authorization: Bearer $AI_FIX_GITHUB_TOKEN" \
                      "$AI_FIX_API_URL" \
                      -o "$AI_FIX_OUTPUT_PATH"
                ''',
                returnStatus: true
            )
        }

        if (apiStatus != 0) {
            echo "WARN: Failed to query GitHub merged PR history for '${sourceBranch}'. DetectPreviousAIFix returns false."
            return false
        }

        def apiResponse = readFile(outputPath).trim()
        def parsedResponse
        try {
            parsedResponse = new groovy.json.JsonSlurperClassic().parseText(apiResponse)
        } catch (Exception ex) {
            echo "WARN: Could not parse GitHub API response (${ex.message}). DetectPreviousAIFix returns false."
            return false
        }

        if (!(parsedResponse instanceof List)) {
            echo 'WARN: Unexpected GitHub API payload. DetectPreviousAIFix returns false.'
            return false
        }

        def aiPrefix = "ai-fix/${sourceBranch}-"
        def mergedAiPullRequests = parsedResponse.findAll { pr ->
            pr instanceof Map &&
            pr.merged_at &&
            (pr.head instanceof Map) &&
            (pr.head.ref instanceof String) &&
            pr.head.ref.startsWith(aiPrefix)
        }

        if (mergedAiPullRequests) {
            echo "INFO: Detected ${mergedAiPullRequests.size()} merged AI fix PR(s) for '${sourceBranch}'."
            return true
        }

        echo "INFO: No merged AI fix PR detected for '${sourceBranch}'."
        return false
    }
}