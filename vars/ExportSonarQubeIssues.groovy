def call() {
    script {
        def exportScript = libraryResource 'export_sonarqube_issues.py'
        writeFile file: 'export_sonarqube_issues.py', text: exportScript
        def pythonCmd = tool 'python3'
        sh "${pythonCmd}/bin/python3 export_sonarqube_issues.py"
    }
}