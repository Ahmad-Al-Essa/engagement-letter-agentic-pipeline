"""The partner's review: the only place a draft or an escalation changes status.

This script is NOT an MCP tool and no agent can call it. The agents' server
(sbp-data) has no tool that approves anything; a partner runs this script.
In a real deployment, this would be a screen in the firm's portal.

    python3 review.py                          list drafts and escalations waiting for a partner
    python3 review.py draft 1                  show draft 1 (evidence + letter), then ask what to do
    python3 review.py escalation 2             show escalation 2 (reasons + evidence), then ask
    python3 review.py draft 1 approve "note"   decide without being asked (for scripts and the demo)

Decisions:
    drafts:       approve · reject · revise   (each with a short note)
    escalations:  resolve                     (with a note saying what the partner decided)

The evidence shown for each case is printed by code from the run's trace log
(the scope memo and the checker's comparison), not summarised by an agent, so
it shows exactly what the system decided and why.
"""

import sqlite3
import sys
from pathlib import Path

from pipeline.trace import RUNS_DIR, read_trace

DB_PATH = Path(__file__).parent / "data" / "sbp.db"
DRAFT_DECISIONS = {"approve": "approved", "reject": "rejected", "revise": "revise"}


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def owner_name(conn, client_id, prospect_id):
    if client_id is not None:
        row = conn.execute("SELECT name FROM clients WHERE id = ?", (client_id,)).fetchone()
        return f"{row[0]} (client {client_id})"
    if prospect_id is not None:
        row = conn.execute("SELECT name FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        return f"{row[0]} (new prospect {prospect_id})"
    return "(no client or prospect identified)"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_waiting(conn):
    print("DRAFTS WAITING FOR REVIEW")
    rows = conn.execute("SELECT id, client_id, prospect_id, service_codes, created_at FROM drafts "
                        "WHERE status = 'pending_review' ORDER BY id").fetchall()
    if not rows:
        print("  (none)")
    for draft_id, client_id, prospect_id, codes, created in rows:
        print(f"  draft {draft_id}: {owner_name(conn, client_id, prospect_id)} | {codes} | {created}")

    print("\nESCALATIONS WAITING FOR A PARTNER")
    rows = conn.execute("SELECT id, client_id, prospect_id, reason, created_at FROM escalations "
                        "WHERE status = 'awaiting_scope_decision' ORDER BY id").fetchall()
    if not rows:
        print("  (none)")
    for esc_id, client_id, prospect_id, reason, created in rows:
        kind = "RECOMMENDED DECLINE" if reason.startswith("RECOMMENDED DECLINE") else "needs a decision"
        print(f"  escalation {esc_id}: {owner_name(conn, client_id, prospect_id)} | {kind} | {created}")

    print("\nOpen one with:  python3 review.py draft <id>   or   python3 review.py escalation <id>")


# ---------------------------------------------------------------------------
# The evidence view (printed by code from the trace log)
# ---------------------------------------------------------------------------

def show_evidence(run_id):
    path = RUNS_DIR / f"{run_id}.jsonl"
    if not run_id or not path.exists():
        print("  (no trace log found for this run)")
        return
    memo = None
    comparison = None
    for step in read_trace(path):
        if step["step"] == "scope_memo":
            memo = step["memo"]
        if step["step"] == "comparison":
            comparison = step
    if memo is None:
        print("  (the run stopped before triage: see the reasons above)")
        return

    print(f"TRIAGE VERDICT: {memo['verdict']}: {memo['verdict_reason']}")
    print("\nSelected services, each next to the client's own words:")
    if not memo["services"]:
        print("  (none)")
    for service in memo["services"]:
        print(f"  {service['code']:9} {service['reason']}")
        print(f"            client wrote: \"{service['client_quote']}\"")
    print("\nRuled out (near neighbours):")
    if not memo["excluded"]:
        print("  (none)")
    for item in memo["excluded"]:
        print(f"  {item['code']:9} {item['reason']}")
    print("\nAsked for, but not offered by the firm:")
    if not memo["not_offered"]:
        print("  (none)")
    for item in memo["not_offered"]:
        print(f"  - {item['need']}  (client wrote: \"{item['client_quote']}\")")
    if memo["paused"]:
        print("\nPaused services the client asked for:")
        for item in memo["paused"]:
            print(f"  {item['code']:9} (client wrote: \"{item['client_quote']}\")")
    if memo["open_questions"]:
        print("\nQuestions to ask the client:")
        for question in memo["open_questions"]:
            print(f"  - {question}")
    if comparison is not None:
        print("\nCHECKER (worked out its own answer first):",
              "agrees with triage" if comparison["agrees"] else "DISAGREES with triage")
        for difference in comparison["differences"]:
            print(f"  - {difference}")


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

def ask(prompt, allowed):
    answer = ""
    while answer not in allowed:
        answer = input(prompt).strip().lower()
    return answer


def review_draft(conn, draft_id, decision=None, note=None):
    row = conn.execute("SELECT client_id, prospect_id, service_codes, letter_text, status, run_id "
                       "FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        print(f"No draft {draft_id}.")
        return
    client_id, prospect_id, codes, letter_text, status, run_id = row
    print("=" * 70)
    print(f"DRAFT {draft_id} | {owner_name(conn, client_id, prospect_id)} | {codes} | status: {status}")
    print("=" * 70)
    show_evidence(run_id)
    print("\n" + "-" * 70 + "\nTHE LETTER\n" + "-" * 70)
    print(letter_text)
    print("-" * 70)

    if status != "pending_review":
        print(f"Already decided: {status}.")
        return
    if decision is None:
        decision = ask("\nDecision? [approve / reject / revise / skip]: ", ["approve", "reject", "revise", "skip"])
        if decision == "skip":
            return
        note = input("Note (optional): ").strip()

    conn.execute("UPDATE drafts SET status = ?, reviewer_note = ?, updated_at = datetime('now') WHERE id = ?",
                 (DRAFT_DECISIONS[decision], note or None, draft_id))
    conn.commit()
    print(f"Draft {draft_id} is now '{DRAFT_DECISIONS[decision]}'. Nothing has been sent to the client.")


def review_escalation(conn, esc_id, decision=None, note=None):
    row = conn.execute("SELECT client_id, prospect_id, reason, status, run_id, partner_note "
                       "FROM escalations WHERE id = ?", (esc_id,)).fetchone()
    if row is None:
        print(f"No escalation {esc_id}.")
        return
    client_id, prospect_id, reason, status, run_id, partner_note = row
    print("=" * 70)
    print(f"ESCALATION {esc_id} | {owner_name(conn, client_id, prospect_id)} | status: {status}")
    print("=" * 70)
    if reason.startswith("RECOMMENDED DECLINE"):
        print("The system RECOMMENDS DECLINING this request. You decide; if you agree, you contact")
        print("the client yourself. The system has sent nothing.\n")
    print("Why it came to you:")
    for part in reason.split(" | "):
        print(f"  - {part}")
    print()
    show_evidence(run_id)

    if status != "awaiting_scope_decision":
        print(f"\nAlready resolved: {partner_note}")
        return
    if decision is None:
        decision = ask("\nDecision? [resolve / skip]: ", ["resolve", "skip"])
        if decision == "skip":
            return
        note = input("What did you decide? (e.g. 'declined by phone', 'trimmed and re-run as run X'): ").strip()

    conn.execute("UPDATE escalations SET status = 'resolved', partner_note = ?, resolved_at = datetime('now') "
                 "WHERE id = ?", (note or None, esc_id))
    conn.commit()
    print(f"Escalation {esc_id} is resolved.")


def main():
    args = sys.argv[1:]
    conn = connect()
    if len(args) == 0:
        list_waiting(conn)
    elif len(args) >= 2 and args[0] == "draft":
        decision = args[2] if len(args) >= 3 else None
        if decision is not None and decision not in DRAFT_DECISIONS:
            print("Draft decisions: approve, reject, revise")
        else:
            review_draft(conn, int(args[1]), decision, args[3] if len(args) >= 4 else None)
    elif len(args) >= 2 and args[0] == "escalation":
        decision = args[2] if len(args) >= 3 else None
        if decision is not None and decision != "resolve":
            print("Escalation decision: resolve")
        else:
            review_escalation(conn, int(args[1]), decision, args[3] if len(args) >= 4 else None)
    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main()
