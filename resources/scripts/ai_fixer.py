#!/usr/bin/env python3
"""
AI Code Fixer — Lee reportes de SonarQube y test results, usa un LLM para
corregir el código fuente, crea una rama y abre una PR en GitHub.

Uso:
    python ai_fixer.py --reports sonarqube-issues.json assets/python_test_results.json assets/js_test_results.xml \
                       --repo owner/repo --source-branch main

    python ai_fixer.py --reports sonarqube-issues.json --dry-run   # solo muestra prompts, no llama al LLM
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. Parsers de reportes
# ---------------------------------------------------------------------------

def parse_sonarqube(filepath):
    """Parsea el JSON exportado por ExportSonarQubeIssues y agrupa issues por archivo."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues_by_file = {}
    for issue in data.get("issues", []):
        file_path = issue.get("file", "")
        if not file_path:
            continue
        entry = {
            "source": "sonarqube",
            "line": issue.get("location", {}).get("startLine", 0),
            "message": issue.get("location", {}).get("message", ""),
            "severity": issue.get("severity", "INFO"),
            "rule": issue.get("rule", {}).get("key", ""),
            "type": issue.get("rule", {}).get("type", "CODE_SMELL"),
        }
        issues_by_file.setdefault(file_path, []).append(entry)
    return issues_by_file


def parse_pytest_json(filepath):
    """Parsea el JSON de pytest-json-report y agrupa fallos por archivo."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues_by_file = {}
    for test in data.get("tests", []):
        if test.get("outcome") != "failed":
            continue

        nodeid = test.get("nodeid", "")          # e.g. "test.py::test_fallo_suma"
        test_file = nodeid.split("::")[0] if "::" in nodeid else ""
        test_name = nodeid.split("::")[-1] if "::" in nodeid else nodeid

        call_info = test.get("call", {})
        crash = call_info.get("crash", {})
        longrepr = call_info.get("longrepr", "")

        entry = {
            "source": "pytest",
            "test": test_name,
            "line": crash.get("lineno", 0),
            "message": crash.get("message", ""),
            "longrepr": longrepr,
        }
        if test_file:
            issues_by_file.setdefault(test_file, []).append(entry)
    return issues_by_file


def parse_junit_xml(filepath):
    """Parsea JUnit XML (ej. Node.js --test-reporter=junit) y agrupa fallos por archivo."""
    tree = ET.parse(filepath)
    root = tree.getroot()

    issues_by_file = {}
    # JUnit XML: <testsuites><testsuite><testcase><failure>
    for testsuite in root.iter("testsuite"):
        for testcase in testsuite.iter("testcase"):
            failure = testcase.find("failure")
            if failure is None:
                continue

            classname = testcase.get("classname", "")
            test_name = testcase.get("name", "")
            failure_msg = failure.get("message", "")
            failure_text = failure.text or ""

            # classname suele ser el archivo (ej. "test.js")
            test_file = classname if classname else "test.js"

            entry = {
                "source": "junit",
                "test": test_name,
                "message": failure_msg,
                "details": failure_text.strip(),
            }
            issues_by_file.setdefault(test_file, []).append(entry)
    return issues_by_file


def detect_and_parse(filepath):
    """Detecta el formato del reporte y lo parsea automáticamente."""
    path = Path(filepath)
    if not path.exists():
        print(f"  ⚠️  Reporte no encontrado, omitiendo: {filepath}", file=sys.stderr)
        return {}

    suffix = path.suffix.lower()

    if suffix == ".xml":
        print(f"  📄 Parseando JUnit XML: {filepath}", file=sys.stderr)
        return parse_junit_xml(filepath)

    if suffix == ".json":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Heurísticas para distinguir SonarQube JSON vs pytest-json-report
        if "issues" in data and "project" in data:
            print(f"  📄 Parseando SonarQube JSON: {filepath}", file=sys.stderr)
            return parse_sonarqube(filepath)
        if "tests" in data:
            print(f"  📄 Parseando pytest JSON report: {filepath}", file=sys.stderr)
            return parse_pytest_json(filepath)

        print(f"  ⚠️  Formato JSON no reconocido: {filepath}", file=sys.stderr)
        return {}

    print(f"  ⚠️  Extensión no soportada ({suffix}): {filepath}", file=sys.stderr)
    return {}


def merge_issues(all_maps):
    """Combina varios dicts {archivo: [issues]} en uno solo."""
    merged = {}
    for m in all_maps:
        for file_path, issues in m.items():
            merged.setdefault(file_path, []).extend(issues)
    return merged


# ---------------------------------------------------------------------------
# 2. Construcción de prompts
# ---------------------------------------------------------------------------

def build_prompt(file_path, code, issues, extra_context=None):
    """Crea el prompt para el LLM con el código y la lista de issues."""
    issues_text = "\n".join(
        f"  - [{i.get('source', '?').upper()}] Línea {i.get('line', '?')}: {i.get('message', i.get('longrepr', ''))}"
        + (f" (severity: {i['severity']}, rule: {i['rule']})" if i.get("rule") else "")
        + (f" (test: {i['test']})" if i.get("test") else "")
        for i in issues
    )

    prompt = f"""You are an expert code reviewer and fixer. Your task is to fix the issues listed below in the source file.

## File: {file_path}

```
{code}
```

## Issues to fix:
{issues_text}
"""

    if extra_context:
        for ctx_path, ctx_code in extra_context.items():
            prompt += f"""
## Related file (for context only — do NOT modify): {ctx_path}

```
{ctx_code}
```
"""

    prompt += """
## Instructions:
1. Fix ALL the issues listed above in the source file.
2. Return ONLY the complete corrected source code of the file.
3. Do NOT include markdown code fences, file names, explanations, or any other text.
4. Do NOT modify the overall structure or logic unless strictly required to fix an issue.
5. If an issue is in a test file and the test expectation is wrong (not a bug in the source), fix the test.
6. If an issue is in a test file but the bug is in the source module, return the test file unchanged — the source file will be fixed separately.
7. Preserve the original coding style, indentation, and comments.
"""
    return prompt


def strip_markdown_fences(text):
    """Elimina markdown code fences que el LLM pueda incluir."""
    # Eliminar ```language al inicio y ``` al final
    text = re.sub(r"^```[\w]*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text.strip())
    return text


# ---------------------------------------------------------------------------
# 3. Llamada al LLM
# ---------------------------------------------------------------------------

def call_llm(prompt, model):
    """Llama al LLM usando litellm."""
    import litellm

    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": "You are an expert software engineer that fixes code issues. Return only the corrected code, nothing else."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    return strip_markdown_fences(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# 4. Relación test → source
# ---------------------------------------------------------------------------

TEST_SOURCE_MAP = {
    # test_file -> source_file (patrones comunes)
    "test_suma.py": "src/calculator/suma.py",
    "test_prueba.js": "src/calculator/prueba.js",
}


def infer_source_for_test(test_file, workspace):
    """Dado un archivo de test, intenta encontrar el módulo fuente asociado."""
    # Primero, buscar en el mapa estático
    base = Path(test_file).name
    if base in TEST_SOURCE_MAP:
        candidate = Path(workspace) / TEST_SOURCE_MAP[base]
        if candidate.exists():
            return str(Path(TEST_SOURCE_MAP[base]))

    # Heurística: test_X.py -> X.py, X.test.js -> X.js
    name = Path(test_file).stem
    suffix = Path(test_file).suffix
    patterns = []
    if name.startswith("test_"):
        patterns.append(name[5:] + suffix)       # test_suma.py -> suma.py
    if name.endswith("_test"):
        patterns.append(name[:-5] + suffix)       # suma_test.py -> suma.py
    if name.endswith(".test"):
        patterns.append(name[:-5] + suffix)       # prueba.test.js -> prueba.js
    # test.py -> (no hay convención clara)

    for pattern in patterns:
        candidate = Path(workspace) / Path(test_file).parent / pattern
        if candidate.exists():
            return str(Path(test_file).parent / pattern)

    return None


# ---------------------------------------------------------------------------
# 5. Git operations
# ---------------------------------------------------------------------------

def git_run(*args, cwd=None):
    """Ejecuta un comando git y devuelve stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        print(f"  ⚠️  git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
    return result.stdout.strip()


def get_current_branch(cwd=None):
    """Obtiene la rama actual."""
    return git_run("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)


def create_branch_and_commit(workspace, modified_files, source_branch):
    """Crea una rama nueva, commitea los archivos modificados y pushea."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    new_branch = f"ai-fix/{source_branch}-{timestamp}"

    print(f"\n🔀 Creando rama: {new_branch}", file=sys.stderr)
    git_run("checkout", "-b", new_branch, cwd=workspace)

    for f in modified_files:
        git_run("add", f, cwd=workspace)

    commit_msg = f"fix: AI-generated fixes for {len(modified_files)} file(s)\n\nSource branch: {source_branch}"
    git_run("commit", "-m", commit_msg, cwd=workspace)

    print(f"📤 Pushing rama {new_branch}...", file=sys.stderr)
    push_result = subprocess.run(
        ["git", "push", "origin", new_branch],
        capture_output=True, text=True, cwd=workspace,
    )
    if push_result.returncode != 0:
        print(f"  ❌ Push failed: {push_result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    # Volver a la rama original
    git_run("checkout", source_branch, cwd=workspace)

    return new_branch


# ---------------------------------------------------------------------------
# 6. Crear PR via PyGithub
# ---------------------------------------------------------------------------

def create_pull_request(repo_slug, github_token, head_branch, base_branch, issues_by_file):
    """Crea una PR en GitHub con un resumen de los issues corregidos."""
    from github import Github

    g = Github(github_token)
    repo = g.get_repo(repo_slug)

    # Construir body de la PR
    body_lines = [
        "## 🤖 AI-Generated Code Fixes",
        "",
        "This PR was automatically generated by the AI Code Fixer in the CI pipeline.",
        f"Source branch: `{base_branch}`",
        "",
        "### Issues addressed:",
        "",
    ]

    for file_path, issues in sorted(issues_by_file.items()):
        body_lines.append(f"#### `{file_path}`")
        for issue in issues:
            source = issue.get("source", "?").upper()
            msg = issue.get("message", issue.get("longrepr", "N/A"))
            severity = issue.get("severity", "")
            rule = issue.get("rule", "")
            test = issue.get("test", "")

            parts = [f"- **[{source}]** {msg}"]
            if severity:
                parts.append(f" (severity: {severity})")
            if rule:
                parts.append(f" (rule: `{rule}`)")
            if test:
                parts.append(f" (test: `{test}`)")
            body_lines.append("".join(parts))
        body_lines.append("")

    body_lines.extend([
        "---",
        "⚠️ **Please review all changes carefully before merging.** The AI may introduce unintended modifications.",
    ])

    title = f"fix: AI-generated fixes for {base_branch}"
    body = "\n".join(body_lines)

    # Comprobar si ya existe una PR abierta del mismo head
    existing_prs = repo.get_pulls(state="open", head=f"{repo_slug.split('/')[0]}:{head_branch}")
    for pr in existing_prs:
        print(f"  ℹ️  PR ya existe: #{pr.number} — actualizando body", file=sys.stderr)
        pr.edit(body=body)
        return pr.html_url

    pr = repo.create_pull(
        title=title,
        body=body,
        head=head_branch,
        base=base_branch,
    )
    return pr.html_url


# ---------------------------------------------------------------------------
# 7. Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Code Fixer — Fix code issues using LLM")
    parser.add_argument("--reports", nargs="+", required=True, help="Paths to report files (JSON, XML)")
    parser.add_argument("--repo", required=True, help="GitHub repo slug (owner/repo)")
    parser.add_argument("--source-branch", required=True, help="Current branch name")
    parser.add_argument("--workspace", default=".", help="Path to the workspace root")
    parser.add_argument("--dry-run", action="store_true", help="Show prompts without calling LLM or git")
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    model = os.environ.get("LLM_MODEL", "gemini-3.1-pro-preview")
    github_token = os.environ.get("Github_AI_Auth", "")

    print("=" * 60, file=sys.stderr)
    print("🤖  AI Code Fixer", file=sys.stderr)
    print(f"   Model:  {model}", file=sys.stderr)
    print(f"   Repo:   {args.repo}", file=sys.stderr)
    print(f"   Branch: {args.source_branch}", file=sys.stderr)
    print(f"   Reports: {args.reports}", file=sys.stderr)
    print(f"   Dry run: {args.dry_run}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # --- Parsear reportes ---
    print("\n📋 Parseando reportes...", file=sys.stderr)
    parsed = [detect_and_parse(r) for r in args.reports]
    issues_by_file = merge_issues(parsed)

    if not issues_by_file:
        print("\n✅ No se encontraron issues. Nada que hacer.", file=sys.stderr)
        return

    print(f"\n📊 Resumen: {sum(len(v) for v in issues_by_file.values())} issues en {len(issues_by_file)} archivo(s):", file=sys.stderr)
    for fp, issues in sorted(issues_by_file.items()):
        print(f"   {fp}: {len(issues)} issue(s)", file=sys.stderr)

    # --- Procesar cada archivo ---
    modified_files = []

    for file_path, issues in sorted(issues_by_file.items()):
        abs_path = Path(workspace) / file_path
        if not abs_path.exists():
            print(f"\n  ⚠️  Archivo no encontrado, omitiendo: {file_path}", file=sys.stderr)
            continue

        print(f"\n🔧 Procesando: {file_path} ({len(issues)} issues)", file=sys.stderr)
        code = abs_path.read_text(encoding="utf-8")

        # Obtener contexto: si es un test, incluir el source module
        extra_context = {}
        has_test_issues = any(i.get("source") in ("pytest", "junit") for i in issues)
        if has_test_issues:
            source_file = infer_source_for_test(file_path, workspace)
            if source_file:
                source_abs = Path(workspace) / source_file
                if source_abs.exists():
                    extra_context[source_file] = source_abs.read_text(encoding="utf-8")
                    print(f"   📎 Contexto incluido: {source_file}", file=sys.stderr)

        prompt = build_prompt(file_path, code, issues, extra_context or None)

        if args.dry_run:
            print(f"\n{'─' * 40}", file=sys.stderr)
            print(f"PROMPT para {file_path}:", file=sys.stderr)
            print(f"{'─' * 40}", file=sys.stderr)
            print(prompt, file=sys.stderr)
            continue

        # Llamar al LLM
        print(f"   🧠 Llamando a {model}...", file=sys.stderr)
        fixed_code = call_llm(prompt, model)

        if not fixed_code or fixed_code.strip() == code.strip():
            print(f"   ℹ️  Sin cambios para {file_path}", file=sys.stderr)
            continue

        # Escribir el código corregido
        abs_path.write_text(fixed_code, encoding="utf-8")
        modified_files.append(file_path)
        print(f"   ✅ Archivo corregido: {file_path}", file=sys.stderr)

    if args.dry_run:
        print("\n🏁 Dry run completado. No se realizaron cambios.", file=sys.stderr)
        return

    if not modified_files:
        print("\n✅ El LLM no propuso cambios. Nada que hacer.", file=sys.stderr)
        return

    print(f"\n📝 Archivos modificados: {modified_files}", file=sys.stderr)

    # --- Git: crear rama, commit, push ---
    new_branch = create_branch_and_commit(workspace, modified_files, args.source_branch)

    # --- Crear PR ---
    if not github_token:
        print("  ⚠️  GITHUB_TOKEN no definido, omitiendo creación de PR", file=sys.stderr)
        print(f"  📌 Rama creada: {new_branch} — crea la PR manualmente", file=sys.stderr)
        return

    print("\n🔗 Creando Pull Request...", file=sys.stderr)
    pr_url = create_pull_request(args.repo, github_token, new_branch, args.source_branch, issues_by_file)
    print(f"\n🎉 PR creada: {pr_url}", file=sys.stderr)


if __name__ == "__main__":
    main()
