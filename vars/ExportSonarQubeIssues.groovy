def call(Map config = [:]) {
    def outputDir = config.outputDir ?: (env.AI_REPORTS_DIR ?: '.')

    script {
        def exportScript = libraryResource 'export_sonarqube_issues.py'
        def requirements = libraryResource 'requirements-sonnarqube.txt'
        writeFile file: '.sonnarqube/export_sonarqube_issues.py', text: exportScript
        writeFile file: '.sonnarqube/requirements-sonnarqube.txt', text: requirements
        sh """
            mkdir -p '${outputDir}'
            python3 -m venv venv
            . venv/bin/activate
            pip install -r .sonnarqube/requirements-sonnarqube.txt
            export SONARQUBE_REPORT_OUTPUT_DIR='${outputDir}'
            python .sonnarqube/export_sonarqube_issues.py > /dev/null 2>&1
        """
    }
}