def call(Map config = [:]) {
    def repoSlug = (config.repoSlug ?: '').toString().trim()
    def reportsDir = (config.reportsDir ?: (env.AI_REPORTS_DIR ?: 'reports_for_IA')).toString().trim()
    def sourceBranch = (config.sourceBranch ?: (env.CHANGE_BRANCH ?: '')).toString().trim()
    def githubTokenVar = (config.githubTokenVar ?: 'Github_AI_Auth').toString().trim()
    def perPage = (config.perPage ?: 100) as int
    def cooldownMinutesRaw = config.containsKey('cooldownMinutes') ? config.cooldownMinutes : 5
    def shellQuote = { value ->
        def normalized = (value ?: '').toString().replace("'", "'\"'\"'")
        return "'${normalized}'"
    }

    script {
        def parsePositiveInt = { rawValue, fieldName ->
            if (rawValue instanceof Number) {
                def parsedNumber = rawValue.intValue()
                if (parsedNumber <= 0) {
                    error "${fieldName} debe ser un número positivo."
                }
                return parsedNumber
            }
            if (rawValue instanceof String && rawValue.trim()) {
                try {
                    def parsedNumber = Integer.parseInt(rawValue.trim())
                    if (parsedNumber <= 0) {
                        error "${fieldName} debe ser un número positivo."
                    }
                    return parsedNumber
                } catch (Exception ignored) {
                    error "${fieldName} debe ser un número positivo."
                }
            }
            error "${fieldName} debe ser un número positivo."
        }

        def cooldownMinutes = parsePositiveInt(cooldownMinutesRaw, 'cooldownMinutes')

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
        if (!(githubTokenVar ==~ /[A-Za-z_][A-Za-z0-9_]*/)) {
            error "githubTokenVar '${githubTokenVar}' no es un nombre de variable de entorno valido."
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
            "AI_FIX_GITHUB_TOKEN_VAR=${githubTokenVar}"
        ]) {
            apiStatus = sh(
                script: '''
                    AI_FIX_TOKEN="$(printenv "$AI_FIX_GITHUB_TOKEN_VAR")"
                    if [ -z "$AI_FIX_TOKEN" ]; then
                        echo "WARN: GitHub token variable '$AI_FIX_GITHUB_TOKEN_VAR' is empty in shell context." >&2
                        exit 2
                    fi

                    curl -fsSL \
                      -H "Accept: application/vnd.github+json" \
                      -H "Authorization: Bearer $AI_FIX_TOKEN" \
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

        if (!mergedAiPullRequests) {
            echo "INFO: No merged AI fix PR detected for '${sourceBranch}'."
            return false
        }

        def nowInstant = java.time.Instant.now()
        def cooldownSeconds = cooldownMinutes * 60L
        def hasRecentMergedAiFix = mergedAiPullRequests.any { pr ->
            def mergedAtRaw = (pr.merged_at ?: pr.closed_at)?.toString()?.trim()
            if (!mergedAtRaw) {
                return false
            }

            try {
                def mergedAtInstant = java.time.Instant.parse(mergedAtRaw)
                def ageSeconds = java.time.Duration.between(mergedAtInstant, nowInstant).seconds

                // If clocks are slightly skewed and age is negative, treat as recent to avoid loops.
                if (ageSeconds < 0) {
                    return true
                }
                return ageSeconds <= cooldownSeconds
            } catch (Exception ignored) {
                return false
            }
        }

        if (hasRecentMergedAiFix) {
            echo "INFO: Detected merged AI fix PR within the last ${cooldownMinutes} minute(s) for '${sourceBranch}'."
            return true
        }

        echo "INFO: Merged AI fix PR(s) found for '${sourceBranch}', but all are older than ${cooldownMinutes} minute(s)."
        return false
    }
}