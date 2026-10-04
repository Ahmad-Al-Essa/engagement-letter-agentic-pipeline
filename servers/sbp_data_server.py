"""sbp-data — the MCP server that holds the firm's records.

Every read or write the pipeline makes to the database goes through one of
these tools. The tool list *is* the safety rule: there is no tool that changes
a draft's status or resolves an escalation, so no agent can approve anything,
whatever its prompt says. Only a partner can, through review.py.

Tools:
    lookup_client          read   find an existing client by name
    search_service_catalog read   find the services that match one client need
    get_service            read   one service's details by its code (used by the checks)
    add_prospect           write  record a company that is not a client yet
    escalate_to_partner    write  hand a case to a partner
    save_draft_for_review  write  store a draft letter as 'pending_review'

Run on its own (for testing):  python3 servers/sbp_data_server.py
Normally the pipeline starts it for you.
"""

import math
import sqlite3
import sys
from pathlib import Path

from langchain_ollama import OllamaEmbeddings
from mcp.server.fastmcp import FastMCP

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
import config  # noqa: E402  (the one place models are chosen)

DB_PATH = REPO / "data" / "sbp.db"

# Catalog search settings — both chosen by measurement, see eval/calibrate_search.py.
MIN_SCORE = 0.33   # below this, a service is not a match (real matches scored 0.37+, non-fits 0.30 or less)
MAX_RESULTS = 3    # never hand the agent more than this many services per need

mcp = FastMCP("sbp-data", log_level="WARNING")  # hide the library's per-request logging


# ---------------------------------------------------------------------------
# Database connections
# ---------------------------------------------------------------------------

def read_connection():
    """Read-only connection: the database itself refuses any write through it."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def write_connection():
    """Read-write connection, with foreign-key checks switched on."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def log(message):
    # MCP talks over stdout, so the server's own messages go to stderr.
    print(f"[sbp-data] {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def lookup_client(name: str) -> str:
    """Look up the clients database by full or partial company name (case-insensitive).
    Returns the id and contact details for every match, or a message saying no client
    was found. Read-only."""

    log(f"lookup_client called with name={name!r}")

    conn = read_connection()
    rows = conn.execute(
        "SELECT id, name, contact_name, contact_email, sector FROM clients WHERE name LIKE ?",
        (f"%{name}%",),
    ).fetchall()
    conn.close()

    if not rows:
        return f"No client found matching '{name}'."

    lines = []
    for client_id, client, contact, email, sector in rows:
        lines.append(f"- client_id {client_id}: {client} | contact: {contact} | email: {email} | sector: {sector}")
    return f"Found {len(rows)} client(s) matching '{name}':\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Catalog search (meaning fingerprints)
#
# Each service description is turned into a list of numbers (an embedding) once.
# A client need is turned into numbers the same way, and the two are compared:
# the closer the meaning, the higher the score (between 0 and 1).
# ---------------------------------------------------------------------------

embedder = OllamaEmbeddings(model=config.EMBED_MODEL, base_url=config.OLLAMA_BASE_URL)
catalog_cache = []   # filled on the first search: one entry per service, with its fingerprint


def load_catalog():
    """Read every service (active or paused) and fingerprint its description, once per server run."""
    if catalog_cache:
        return catalog_cache

    conn = read_connection()
    rows = conn.execute(
        "SELECT code, name, service_line, description, active FROM services ORDER BY code"
    ).fetchall()
    conn.close()

    texts = []
    for code, name, line, description, active in rows:
        # embeddinggemma expects documents written as "title: ... | text: ..."
        texts.append(f"title: {name} | text: {description}")
    vectors = embedder.embed_documents(texts)

    for row, vector in zip(rows, vectors):
        code, name, line, description, active = row
        catalog_cache.append({
            "code": code, "name": name, "line": line, "description": description,
            "active": active == 1, "vector": vector,
        })
    return catalog_cache


def similarity(a, b):
    """Cosine similarity: 1 = same meaning, 0 = unrelated."""
    dot = 0.0
    size_a = 0.0
    size_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        size_a += x * x
        size_b += y * y
    return dot / math.sqrt(size_a * size_b)


@mcp.tool()
def search_service_catalog(need: str) -> str:
    """Search the firm's service catalog for ONE client need, described in plain words
    (e.g. "audited financial statements for our bank"). Call once per separate need.
    Returns up to 3 services that match, with code, name and description — or
    "No matching service in the catalog", which is a normal, expected answer for
    work the firm does not do. A service marked PAUSED exists but is not accepting
    new engagements right now. Read-only."""

    log(f"search_service_catalog called with need={need!r}")

    catalog = load_catalog()
    # embeddinggemma expects queries written as "task: search result | query: ..."
    need_vector = embedder.embed_query(f"task: search result | query: {need}")

    scored = []
    for service in catalog:
        score = similarity(need_vector, service["vector"])
        scored.append((score, service))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    # Keep only real matches: at or above the minimum score, at most MAX_RESULTS.
    matches = []
    for score, service in scored:
        if score >= MIN_SCORE and len(matches) < MAX_RESULTS:
            matches.append((score, service))

    if not matches:
        best_score, best = scored[0]
        log(f"  no match (closest was {best['code']} at {best_score:.3f})")
        return f"No matching service in the catalog for: '{need}'."

    lines = [f"Services matching '{need}':"]
    for score, service in matches:
        label = "" if service["active"] else "  [PAUSED: not accepting new engagements right now]"
        lines.append(f"- {service['code']} {service['name']} ({service['line']}), score {score:.2f}{label}")
        lines.append(f"  {service['description']}")
    return "\n".join(lines)


@mcp.tool()
def get_service(code: str) -> str:
    """Return one service's details by its exact code (e.g. "AUD-001"): name, service
    line, description, and whether it is on or paused. Says so if the code is not in
    the catalog. Read-only."""

    log(f"get_service called with code={code!r}")

    conn = read_connection()
    row = conn.execute(
        "SELECT code, name, service_line, description, active FROM services WHERE code = ?",
        (code,),
    ).fetchone()
    conn.close()

    if row is None:
        return f"Not in the catalog: {code}."
    code, name, line, description, active = row
    state = "on" if active == 1 else "PAUSED (not accepting new engagements)"
    return f"{code} {name} ({line}) | status: {state}\n{description}"


@mcp.tool()
def add_prospect(name: str, contact: str, needs: str) -> str:
    """Record a company that is NOT an existing client, so the case has a record.
    Use only after lookup_client found no match. If a prospect with exactly this
    name is already on file, returns its id instead of creating a duplicate."""

    log(f"add_prospect called with name={name!r}")

    conn = write_connection()
    existing = conn.execute("SELECT id FROM prospects WHERE name = ?", (name,)).fetchone()
    if existing:
        conn.close()
        return f"Prospect already on file: prospect_id {existing[0]} ({name})."

    cursor = conn.execute(
        "INSERT INTO prospects (name, contact, needs) VALUES (?, ?, ?)",
        (name, contact, needs),
    )
    conn.commit()
    prospect_id = cursor.lastrowid
    conn.close()
    return f"New prospect recorded: prospect_id {prospect_id} ({name})."


@mcp.tool()
def escalate_to_partner(reason: str, run_id: str, client_id: int | None = None,
                        prospect_id: int | None = None) -> str:
    """Hand a case to a partner instead of deciding it. Records it with status
    'awaiting_scope_decision'. Give the client_id OR the prospect_id (or neither,
    if the intake never reached a lookup) — never both."""

    log(f"escalate_to_partner called: client_id={client_id}, prospect_id={prospect_id}")

    if client_id is not None and prospect_id is not None:
        return "Not escalated: give a client_id or a prospect_id, not both."

    conn = write_connection()
    cursor = conn.execute(
        "INSERT INTO escalations (client_id, prospect_id, reason, run_id) VALUES (?, ?, ?, ?)",
        (client_id, prospect_id, reason, run_id),
    )
    conn.commit()
    escalation_id = cursor.lastrowid
    conn.close()
    return f"Escalated to a partner: escalation_id {escalation_id}, status 'awaiting_scope_decision'."


@mcp.tool()
def save_draft_for_review(service_codes: list[str], letter_text: str, run_id: str,
                          client_id: int | None = None, prospect_id: int | None = None) -> str:
    """Store a draft engagement letter for partner review. The status is always
    'pending_review' — this tool cannot set any other status. Give the client_id OR
    the prospect_id, exactly one. Every service code must exist in the catalog and
    must not be paused."""

    log(f"save_draft_for_review called: codes={service_codes}, client_id={client_id}, prospect_id={prospect_id}")

    # Exactly one owner (the database checks this too, but a clear message helps).
    if (client_id is None) == (prospect_id is None):
        return "Not saved: give exactly one of client_id or prospect_id."

    if not service_codes:
        return "Not saved: a draft needs at least one service code."

    if not letter_text.strip():
        return "Not saved: the letter text is empty."

    conn = write_connection()

    # Every code must be a real service in the current catalog, and not paused.
    unknown = []
    paused = []
    for code in service_codes:
        found = conn.execute("SELECT active FROM services WHERE code = ?", (code,)).fetchone()
        if found is None:
            unknown.append(code)
        elif found[0] == 0:
            paused.append(code)
    if unknown:
        conn.close()
        return f"Not saved: these service codes are not in the catalog: {', '.join(unknown)}."
    if paused:
        conn.close()
        return f"Not saved: these services are paused (not accepting new engagements): {', '.join(paused)}."

    # No status column in the INSERT: the database default, 'pending_review', applies.
    cursor = conn.execute(
        "INSERT INTO drafts (client_id, prospect_id, service_codes, letter_text, run_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (client_id, prospect_id, ",".join(service_codes), letter_text, run_id),
    )
    conn.commit()
    draft_id = cursor.lastrowid
    conn.close()
    return f"Draft saved for partner review: draft_id {draft_id}, status 'pending_review'."


if __name__ == "__main__":
    mcp.run(transport="stdio")
