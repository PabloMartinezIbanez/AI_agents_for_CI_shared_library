import os


def resolve_env_value(*names, default=""):
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return default


def resolve_reports_dir(workspace):
    raw_reports_dir = resolve_env_value("AGENT_REPORTS_DIR")
    if not raw_reports_dir:
        raw_reports_dir = os.path.join(workspace, "reports_for_IA")
    if not os.path.isabs(raw_reports_dir):
        raw_reports_dir = os.path.join(workspace, raw_reports_dir)
    return os.path.abspath(raw_reports_dir)

