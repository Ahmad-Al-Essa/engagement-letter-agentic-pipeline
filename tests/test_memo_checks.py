"""Test the pieces of triage that are plain code (no model needed, runs in a second).

Run from the repo root:
    python3 tests/test_memo_checks.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
from pipeline.memo_checks import quote_found  # noqa: E402
from pipeline.triage import find_owner, same_company  # noqa: E402

passed_count = 0
failed_count = 0


def expect(description, actual, expected):
    global passed_count, failed_count
    ok = actual == expected
    if ok:
        passed_count += 1
    else:
        failed_count += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {description}" + ("" if ok else f"  (got {actual!r})"))


REQUEST = ("We have been preparing our own financial statements internally for years,\n"
           "but our bank has now asked for audited statements as a condition for renewing\n"
           "our credit facility.")

# No quote, no service.
expect("an exact quote is found", quote_found("our bank has now asked for audited statements", REQUEST), True)
expect("a quote across a line break is found", quote_found("audited statements as a condition for renewing our credit", REQUEST), True)
expect("a quote with … skipping words is found", quote_found("our bank … audited statements", REQUEST), True)
expect("a paraphrase is NOT found", quote_found("the bank requires an audit", REQUEST), False)
expect("words the client never wrote are NOT found", quote_found("we also need a business valuation", REQUEST), False)
expect("a two-word quote counts", quote_found("audited statements", REQUEST), True)
expect("a one-word quote is too short to count", quote_found("bank", REQUEST), False)

# Same company, ignoring capitals, punctuation and a bracketed note.
expect("bracketed note ignored", same_company("Behbehani Family Holding (unlisted)", "Behbehani Family Holding"), True)
expect("capitals ignored", same_company("AL-RAWDAH TRADING COMPANY K.S.C.C.", "Al-Rawdah Trading Company K.S.C.C."), True)
expect("a different company is not the same", same_company("Gulf Marine Services Est.", "Gulf Horizon Trading Co."), False)
expect("a partial name is not the same", same_company("Gulf", "Gulf Marine Services Est."), False)

# The owner comes from the tool results, and only for the intake's company.
intake = {"company": "Al-Rawdah Trading Company K.S.C.C."}
log = [{"tool": "lookup_client", "args": {"name": "Al-Rawdah"},
        "result": "Found 1 client(s) matching 'Al-Rawdah':\n- client_id 1: Al-Rawdah Trading Company K.S.C.C. | contact: x"}]
expect("a client found by lookup is the owner", find_owner(intake, log), (1, None))

intake = {"company": "Gulf Marine Services Est."}
log = [{"tool": "lookup_client", "args": {"name": "Gulf"},
        "result": "Found 1 client(s) matching 'Gulf':\n- client_id 3: Gulf Horizon Trading Co. | contact: x"},
       {"tool": "add_prospect", "args": {"name": "Gulf Marine Services Est."},
        "result": "New prospect recorded: prospect_id 4 (Gulf Marine Services Est.)."}]
expect("another client returned by a loose lookup is NOT the owner", find_owner(intake, log), (None, 4))

log = [{"tool": "lookup_client", "args": {"name": "Gulf"},
        "result": "Found 1 client(s) matching 'Gulf':\n- client_id 3: Gulf Horizon Trading Co. | contact: x"}]
expect("no exact match and no prospect: no owner (goes to a partner)", find_owner(intake, log), (None, None))

print(f"\n{passed_count} passed, {failed_count} failed")
