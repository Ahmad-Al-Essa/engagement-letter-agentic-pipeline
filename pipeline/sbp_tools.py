"""The pipeline's connection to the sbp-data MCP server.

One connection is opened per run and shared by every step, so the server
starts once (and fingerprints the catalog once) instead of on every call.

Each agent then receives only the tools its role needs (least privilege):
the lists below are the whole of what each agent can do to the database.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

REPO = Path(__file__).parent.parent

SERVER = {
    "sbp-data": {
        "command": sys.executable,
        "args": [str(REPO / "servers" / "sbp_data_server.py")],
        "transport": "stdio",
    }
}

# Which tools each agent may call. Writes that end a run (escalate, save a
# draft) are not given to any agent: the pipeline's own code calls them.
TRIAGE_TOOLS = ["lookup_client", "search_service_catalog", "add_prospect"]
# The checker only reads the catalog, and its searches are made by code, one
# per need (when it searched for itself, it reworded the same needs 22 times
# until something matched). It doesn't need lookup_client: which client a run
# is about is settled by code (triage.find_owner), not by an agent.
CHECKER_TOOLS = ["search_service_catalog"]


@asynccontextmanager
async def sbp_data_tools():
    """Open the MCP server for one run; give back its tools by name."""
    client = MultiServerMCPClient(SERVER)
    async with client.session("sbp-data") as session:
        tool_list = await load_mcp_tools(session)
        tools = {}
        for tool in tool_list:
            tools[tool.name] = tool
        yield tools


def text_of(result):
    """MCP results arrive as a string or a list of content blocks; return plain text."""
    if isinstance(result, str):
        return result
    parts = []
    for block in result:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def call_tool(tools, name, args):
    """Call one tool and return its result as plain text."""
    result = await tools[name].ainvoke(args)
    return text_of(result)
