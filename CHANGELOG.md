# Changelog

## Unreleased

- added real contract docs for `FixWithAI`, test configuration, runtime architecture, and versioning
- added Python and Groovy tests for the shared library
- added CI workflow for the shared library
- fixed `FixWithAI` runtime extraction so it now ships the full `mcp_agent_pkg/`
- fixed `FixWithAI` reports-dir handling and SonarQube project-key export
- hardened `dryRun` so it blocks `edit_file` in addition to branch/PR mutations
- added richer runtime artifacts: `agent_trace.json`, `change_manifest.json`, and `execution_policy_snapshot.json`
- added stronger validation for the test-runner config and `extra_args`

## 1.0.0

- initial tagged baseline for the shared library before the consolidation pass documented in the thesis workspace
