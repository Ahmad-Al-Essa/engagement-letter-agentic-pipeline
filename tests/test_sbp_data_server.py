"""Test every sbp-data tool through MCP, the same way the pipeline calls them.

Run from the repo root:
    python3 tests/test_sbp_data_server.py

It rebuilds the database first and again at the end, so the test rows it
writes don't stay behind.
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "data"))
import build_db  # noqa: E402

SERVER = {
    "sbp-data": {
        "command": sys.executable,
        "args": [str(REPO / "servers" / "sbp_data_server.py")],
        "transport": "stdio",
    }
}

passed = 0
failed = 0


def pause_service(code):
    conn = sqlite3.connect(REPO / "data" / "sbp.db")
    conn.execute("UPDATE services SET active = 0 WHERE code = ?", (code,))
    conn.commit()
    conn.close()


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


async def check(tools, tool_name, args, expected_phrase):
    global passed, failed
    result = text_of(await tools[tool_name].ainvoke(args))
    ok = expected_phrase in result
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {tool_name}({args})")
    print(f"       -> {result.strip()}")


async def main():
    build_db.build()
    print()

    client = MultiServerMCPClient(SERVER)
    tool_list = await client.get_tools()
    tools = {}
    for t in tool_list:
        tools[t.name] = t
    print("Tools offered by sbp-data:", sorted(tools))
    print()

    # lookup_client: Record 1's company is a client; Record 2's is not.
    await check(tools, "lookup_client", {"name": "Al-Rawdah"}, "client_id 1")
    await check(tools, "lookup_client", {"name": "Gulf Marine Services"}, "No client found")

    # search_service_catalog: a clear need finds its service; work the firm doesn't do finds nothing.
    await check(tools, "search_service_catalog", {"need": "prepare and file our zakat declaration"}, "TAX-002")
    await check(tools, "search_service_catalog", {"need": "design a new logo and website for our brand"},
                "No matching service")

    # get_service: a real code returns its details; an invented one is refused.
    await check(tools, "get_service", {"code": "TAX-002"}, "Zakat Declaration")
    await check(tools, "get_service", {"code": "LAW-999"}, "Not in the catalog")

    # A paused service is still found, but labelled — and a draft can't use it.
    pause_service("AUD-001")
    await check(tools, "search_service_catalog",
                {"need": "audited financial statements required by our bank"}, "AUD-001 Financial Statement Audit (Audit & Assurance), score")
    await check(tools, "search_service_catalog",
                {"need": "audited financial statements required by our bank"}, "[PAUSED")
    await check(tools, "save_draft_for_review",
                {"service_codes": ["AUD-001"], "letter_text": "Dear ...", "run_id": "test-run", "client_id": 1},
                "paused")
    build_db.build()  # switch it back on

    # add_prospect: new record, then the same name again gives the same id (no duplicate).
    args = {"name": "Gulf Marine Services Est.", "contact": "Managing Partner", "needs": "test"}
    await check(tools, "add_prospect", args, "New prospect recorded: prospect_id 4")
    await check(tools, "add_prospect", args, "already on file: prospect_id 4")

    # escalate_to_partner: allowed with one id; refused with both.
    await check(tools, "escalate_to_partner",
                {"reason": "test", "run_id": "test-run", "prospect_id": 4}, "escalation_id 1")
    await check(tools, "escalate_to_partner",
                {"reason": "test", "run_id": "test-run", "client_id": 1, "prospect_id": 4}, "not both")

    # save_draft_for_review: valid draft saved as pending_review; bad inputs refused.
    await check(tools, "save_draft_for_review",
                {"service_codes": ["AUD-001", "TAX-002"], "letter_text": "Dear ...", "run_id": "test-run",
                 "client_id": 1}, "status 'pending_review'")
    await check(tools, "save_draft_for_review",
                {"service_codes": ["AUD-001", "LAW-999"], "letter_text": "Dear ...", "run_id": "test-run",
                 "client_id": 1}, "not in the catalog: LAW-999")
    await check(tools, "save_draft_for_review",
                {"service_codes": ["AUD-001"], "letter_text": "Dear ...", "run_id": "test-run"},
                "exactly one of client_id or prospect_id")

    # The safety rule: no tool can change a status.
    status_tools = []
    for name in tools:
        if "status" in name or "approve" in name:
            status_tools.append(name)
    global passed, failed
    if status_tools:
        failed += 1
        print(f"[FAIL] a tool can change status: {status_tools}")
    else:
        passed += 1
        print("[PASS] no tool can approve or change a status")

    print()
    build_db.build()  # leave a clean database behind
    print(f"\n{passed} passed, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
