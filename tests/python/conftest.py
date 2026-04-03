import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "resources" / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _install_mcp_stubs():
    if "mcp.server" in sys.modules and "mcp.types" in sys.modules:
        return

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    client_module = types.ModuleType("mcp.client")
    client_stdio_module = types.ModuleType("mcp.client.stdio")
    stdio_module = types.ModuleType("mcp.server.stdio")
    types_module = types.ModuleType("mcp.types")

    class DummyServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            return lambda func: func

        def call_tool(self):
            return lambda func: func

        def create_initialization_options(self):
            return {}

        async def run(self, *args, **kwargs):
            return None

    @asynccontextmanager
    async def stdio_server():
        yield None, None

    class Tool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TextContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return types.SimpleNamespace(tools=[])

    @asynccontextmanager
    async def stdio_client(*args, **kwargs):
        yield None, None

    class StdioServerParameters:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    server_module.Server = DummyServer
    stdio_module.stdio_server = stdio_server
    types_module.Tool = Tool
    types_module.TextContent = TextContent
    client_module.stdio = client_stdio_module
    client_stdio_module.stdio_client = stdio_client

    mcp_module.server = server_module
    mcp_module.client = client_module
    mcp_module.types = types_module
    mcp_module.ClientSession = ClientSession
    mcp_module.StdioServerParameters = StdioServerParameters

    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = server_module
    sys.modules["mcp.client"] = client_module
    sys.modules["mcp.client.stdio"] = client_stdio_module
    sys.modules["mcp.server.stdio"] = stdio_module
    sys.modules["mcp.types"] = types_module


_install_mcp_stubs()
