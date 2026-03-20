def call() {
    script {
        def exportScript = libraryResource 'export_sonarqube_issues.py'
        def requirements = libraryResource 'requirements-sonnarqube.txt'
        writeFile file: '.sonnarqube/export_sonarqube_issues.py', text: exportScript
        writeFile file: '.sonnarqube/requirements-sonnarqube.txt', text: requirements
        sh '''
            python3 -m venv venv
            . venv/bin/activate
            pip install -r .sonnarqube/requirements-sonnarqube.txt
            python .sonnarqube/export_sonarqube_issues.py > /dev/null 2>&1
        '''
    }
}