def call() {
    script {
        def exportScript = libraryResource 'export_sonarqube_issues.py'
        def requirements = libraryResource 'requirements.txt'
        writeFile file: 'export_sonarqube_issues.py', text: exportScript
        writeFile file: '/lib_requirements/requirements.txt', text: requirements
        sh '''
            python3 -m venv venv
            . venv/bin/activate
            pip install -r /lib_requirements/requirements.txt
            python export_sonarqube_issues.py > /dev/null 2>&1
        '''
    }
}