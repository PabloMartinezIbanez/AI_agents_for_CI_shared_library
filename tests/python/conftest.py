import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def build_mcp_stubs():
    mcp_module = types.ModuleType("mcp")

    class DummyClientSession:
        pass

    class DummyStdioServerParameters:
        def __init__(self, command=None, args=None, env=None):
            self.command = command
            self.args = args or []
            self.env = env or {}

    mcp_module.ClientSession = DummyClientSession
    mcp_module.StdioServerParameters = DummyStdioServerParameters

    client_module = types.ModuleType("mcp.client")
    client_stdio_module = types.ModuleType("mcp.client.stdio")

    @contextlib.asynccontextmanager
    async def stdio_client(*args, **kwargs):
        if False:
            yield None
        raise RuntimeError("stdio_client stub should not be used in these tests")

    client_stdio_module.stdio_client = stdio_client

    server_module = types.ModuleType("mcp.server")

    class DummyServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            return lambda fn: fn

        def call_tool(self):
            return lambda fn: fn

        def create_initialization_options(self):
            return {}

        async def run(self, *args, **kwargs):
            return None

    server_module.Server = DummyServer

    server_stdio_module = types.ModuleType("mcp.server.stdio")

    @contextlib.asynccontextmanager
    async def stdio_server():
        yield (None, None)

    server_stdio_module.stdio_server = stdio_server

    types_module = types.ModuleType("mcp.types")

    class Tool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TextContent:
        def __init__(self, type, text):
            self.type = type
            self.text = text

    types_module.Tool = Tool
    types_module.TextContent = TextContent

    return {
        "mcp": mcp_module,
        "mcp.client": client_module,
        "mcp.client.stdio": client_stdio_module,
        "mcp.server": server_module,
        "mcp.server.stdio": server_stdio_module,
        "mcp.types": types_module,
    }


@pytest.fixture
def mcp_stubs():
    return build_mcp_stubs()


@pytest.fixture
def load_module():
    original_modules = {}
    loaded_modules = []

    def _load(module_name, relative_path, stubs=None):
        for stub_name, stub_module in (stubs or {}).items():
            if stub_name not in original_modules:
                original_modules[stub_name] = sys.modules.get(stub_name)
            sys.modules[stub_name] = stub_module

        module_path = REPO_ROOT / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        loaded_modules.append(module_name)
        spec.loader.exec_module(module)
        return module

    yield _load

    for module_name in loaded_modules:
        sys.modules.pop(module_name, None)

    for stub_name, original_module in original_modules.items():
        if original_module is None:
            sys.modules.pop(stub_name, None)
        else:
            sys.modules[stub_name] = original_module
