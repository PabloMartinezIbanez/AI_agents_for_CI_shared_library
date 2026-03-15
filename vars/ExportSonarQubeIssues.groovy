def call() {
    script {
        def exportScript = libraryResource 'export_sonarqube_issues.py'
        def requirements = libraryResource 'requirements.txt'
        writeFile file: 'export_sonarqube_issues.py', text: exportScript
        writeFile file: 'requirements.txt', text: requirements
        sh 'pip3 install -r requirements.txt'
        sh 'python3 export_sonarqube_issues.py'
    }
}