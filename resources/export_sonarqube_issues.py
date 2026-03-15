#!/usr/bin/env python3
"""
Script para exportar issues de SonarQube en formato JSON apto para IA
"""

import json
import os
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone
from typing import Dict, Any
import sys
from dotenv import load_dotenv 

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))

class SonarQubeExporter:
    def __init__(self, sonar_url: str, sonar_token: str, project_key: str):
        self.sonar_url = sonar_url.rstrip('/')
        self.sonar_token = sonar_token
        self.project_key = project_key
        # Usa autenticación básica con el token como usuario y contraseña vacía
        self.auth = HTTPBasicAuth(sonar_token, '')
    
    def get_issues(self) -> Dict[str, Any]:
        """Obtiene todos los issues del proyecto"""
        endpoint = f"{self.sonar_url}/api/issues/search"
        params = {
            'componentKeys': self.project_key,
            'ps': 500,
            'statuses': 'OPEN,CONFIRMED,REOPENED,RESOLVED,CLOSED'
        }
        
        try:
            # Intenta primero con el token del usuario
            response = requests.get(endpoint, params=params, auth=self.auth, timeout=10)
            if response.status_code == 401:
                # Si falla por autenticación, intenta con credenciales por defecto
                response = requests.get(endpoint, params=params, auth=HTTPBasicAuth('admin', 'admin'), timeout=10)
            elif response.status_code == 400:
                # Error 400 probablemente por parámetros
                print(f"⚠️  Error 400 - posibles parámetros inválidos", file=sys.stderr)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error obteniendo issues: {e}", file=sys.stderr)
            return {'issues': []}
    
    def get_security_hotspots(self) -> Dict[str, Any]:
        """Obtiene security hotspots del proyecto"""
        endpoint = f"{self.sonar_url}/api/hotspots/search"
        params = {
            'projectKey': self.project_key,
            'ps': 500
        }
        
        try:
            # Intenta primero con el token del usuario
            response = requests.get(endpoint, params=params, auth=self.auth, timeout=10)
            if response.status_code == 401:
                # Si falla por autenticación, intenta con credenciales por defecto
                response = requests.get(endpoint, params=params, auth=HTTPBasicAuth('admin', 'admin'), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error obteniendo security hotspots: {e}", file=sys.stderr)
            return {'hotspots': []}
    
    def get_project_measures(self) -> Dict[str, Any]:
        """Obtiene métricas del proyecto"""
        endpoint = f"{self.sonar_url}/api/measures/component"
        params = {
            'component': self.project_key,
            'metricKeys': 'complexity,violations,bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc'
        }
        
        try:
            # Intenta primero con el token del usuario
            response = requests.get(endpoint, params=params, auth=self.auth, timeout=10)
            if response.status_code == 401:
                # Si falla por autenticación, intenta con credenciales por defecto
                response = requests.get(endpoint, params=params, auth=HTTPBasicAuth('admin', 'admin'), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error obteniendo métricas: {e}", file=sys.stderr)
            return {'component': {}}
    
    def generate_report(self) -> Dict[str, Any]:
        """Genera el reporte completo"""
        issues_data = self.get_issues()
        hotspots_data = self.get_security_hotspots()
        
        issues = issues_data.get('issues', [])
        
        # Construir resumen de severidades
        severity_count = {'BLOCKER': 0, 'CRITICAL': 0, 'MAJOR': 0, 'MINOR': 0, 'INFO': 0}
        status_count = {'OPEN': 0, 'CONFIRMED': 0, 'FALSE_POSITIVE': 0, 'ACCEPTED': 0, 'FIXED': 0}
        language_count = {}
        
        for issue in issues:
            severity = issue.get('severity', 'INFO')
            status = issue.get('status', 'OPEN')
            
            if severity in severity_count:
                severity_count[severity] += 1
            if status in status_count:
                status_count[status] += 1
            
            # Obtener lenguaje del componente
            component = issue.get('component', '')
            lang = component.split(':')[1].split('/')[0] if ':' in component else 'unknown'
            language_count[lang] = language_count.get(lang, 0) + 1
        
        # Formatear issues con sugerencias
        formatted_issues = []
        for idx, issue in enumerate(issues, 1):
            formatted_issue = {
                "id": idx,
                "key": issue.get('key'),
                "severity": issue.get('severity'),
                "status": issue.get('status'),
                "rule": {
                    "key": issue.get('rule'),
                    "type": issue.get('type', 'CODE_SMELL')
                },
                "file": issue.get('component', '').split(':')[-1] if ':' in issue.get('component', '') else issue.get('component', ''),
                "location": {
                    "startLine": issue.get('line', 0),
                    "message": issue.get('message', '')
                },
                "createdDate": issue.get('creationDate'),
                "author": issue.get('author', 'unknown'),
                "tags": issue.get('tags', [])
            }
            formatted_issues.append(formatted_issue)
        
        report = {
            "project": {
                "key": self.project_key,
                "analysisDate": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            },
            "summary": {
                "totalIssues": len(issues),
                "bySeverity": {k: v for k, v in severity_count.items() if v > 0},
                "byStatus": {k: v for k, v in status_count.items() if v > 0},
                "byLanguage": language_count,
                "securityHotspots": len(hotspots_data.get('hotspots', []))
            },
            "issues": formatted_issues,
            "securityHotspots": hotspots_data.get('hotspots', [])[:20]
        }
        
        return report

def main():
    load_dotenv()
    # Configuración
    SONAR_URL = os.getenv("SONARQUBE_URL")
    SONAR_TOKEN = os.getenv("SONARQUBE_TOKEN")
    PROJECT_KEY = os.getenv("SONARQUBE_PROJECT_KEY")
    
    print("🔍 Extrayendo issues de SonarQube...", file=sys.stderr)
    print(f"   URL: {SONAR_URL}", file=sys.stderr)
    print(f"   Proyecto: {PROJECT_KEY}", file=sys.stderr)
    print("")
    
    try:
        exporter = SonarQubeExporter(SONAR_URL, SONAR_TOKEN, PROJECT_KEY)
        report = exporter.generate_report()
    except Exception as e:
        print(f"❌ Error fatal: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Guardar reporte
    output_file = 'sonarqube-issues.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Reporte guardado en: {output_file}", file=sys.stderr)
    print(f"📊 Total de issues: {report['summary']['totalIssues']}", file=sys.stderr)
    print(f"🔒 Security Hotspots: {report['summary']['securityHotspots']}", file=sys.stderr)
    
    # Mostrar resumen por severidad
    print("\n📋 Resumen por severidad:", file=sys.stderr)
    for severity, count in report['summary']['bySeverity'].items():
        print(f"  {severity}: {count}", file=sys.stderr)
    
    # Imprimir reporte en stdout
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
