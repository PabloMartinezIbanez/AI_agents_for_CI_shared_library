#!/usr/bin/env python3
"""Compatibility wrapper for the MCP AI agent."""

import asyncio
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mcp_agent_pkg import (  # noqa: E402
    SYSTEM_PROMPT,
    async_main as package_async_main,
    build_server_configs,
    connect_servers,
    extract_validation_results,
    log,
    mcp_tools_to_openai_format,
    persist_agent_artifacts,
    resolve_env_value,
    resolve_reports_dir,
    run_agent_loop,
    write_json_artifact,
)


async def async_main(args):
    return await package_async_main(
        args,
        build_server_configs=build_server_configs,
        connect_servers=connect_servers,
        run_agent_loop=run_agent_loop,
        mcp_tools_to_openai_format=mcp_tools_to_openai_format,
        resolve_env_value=resolve_env_value,
        resolve_reports_dir=resolve_reports_dir,
        persist_agent_artifacts=persist_agent_artifacts,
        log=log,
    )


def main():
    from mcp_agent_pkg import main as package_main

    return package_main(async_main_callable=async_main)


if __name__ == "__main__":
    main()
