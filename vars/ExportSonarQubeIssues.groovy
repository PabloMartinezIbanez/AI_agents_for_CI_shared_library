def call() {
    script {
        def exportScript = libraryResource 'export_sonarqube_issues.py'
        writeFile file: 'export_sonarqube_issues.py', text: exportScript
        sh 'python3 export_sonarqube_issues.py'
    }
}