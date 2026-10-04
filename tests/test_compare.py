"""Test the code that compares triage's memo with the checker's own view (no model needed).

Run from the repo root:
    python3 tests/test_compare.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
from pipeline.checker import CheckerNeed, CheckerView, compare_with_memo  # noqa: E402
from pipeline.scope_memo import ScopeMemo, SelectedService  # noqa: E402

passed_count = 0
failed_count = 0

INTAKE = {"request": "Our bank has now asked for audited statements as a condition for renewing our "
                     "credit facility. We also need our zakat declaration prepared and filed."}

AUDIT = SelectedService(code="AUD-001", reason="bank needs audited statements",
                        client_quote="our bank has now asked for audited statements")
ZAKAT = SelectedService(code="TAX-002", reason="zakat filing",
                        client_quote="zakat declaration prepared and filed")


def memo_with(services, verdict):
    return ScopeMemo(company="Test Co.", verdict=verdict, verdict_reason="test", services=services,
                     excluded=[], not_offered=[], paused=[], deadline="", open_questions=[])


def view_with(needs, verdict):
    return CheckerView(needs=needs, verdict=verdict, verdict_reason="test")


def expect(description, result, should_agree, phrase=""):
    global passed_count, failed_count
    agrees, differences = result
    ok = agrees == should_agree and (phrase == "" or phrase in " ".join(differences))
    if ok:
        passed_count += 1
    else:
        failed_count += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {description}")
    for difference in differences:
        print(f"       - {difference}")


checker_audit = CheckerNeed(need="audit", client_quote="our bank has now asked for audited statements", code="AUD-001")
checker_zakat = CheckerNeed(need="zakat", client_quote="zakat declaration prepared and filed", code="TAX-002")

expect("same services, same verdict: agree",
       compare_with_memo(memo_with([AUDIT, ZAKAT], "in_scope"), view_with([checker_audit, checker_zakat], "in_scope"), INTAKE),
       should_agree=True)

expect("triage missed a service the checker found: disagree",
       compare_with_memo(memo_with([AUDIT], "in_scope"), view_with([checker_audit, checker_zakat], "in_scope"), INTAKE),
       should_agree=False, phrase="The checker found TAX-002")

expect("triage selected a service the checker didn't find: disagree",
       compare_with_memo(memo_with([AUDIT, ZAKAT], "in_scope"), view_with([checker_audit], "in_scope"), INTAKE),
       should_agree=False, phrase="Triage selected TAX-002")

expect("different verdicts: disagree",
       compare_with_memo(memo_with([AUDIT, ZAKAT], "in_scope"), view_with([checker_audit, checker_zakat], "unclear"), INTAKE),
       should_agree=False, phrase="verdicts differ")

invented = CheckerNeed(need="valuation", client_quote="we also want to know what the company is worth", code="DEAL-002")
expect("a checker service backed by words the client never wrote is not counted",
       compare_with_memo(memo_with([AUDIT, ZAKAT], "in_scope"),
                         view_with([checker_audit, checker_zakat, invented], "in_scope"), INTAKE),
       should_agree=False, phrase="not in the client's request")

print(f"\n{passed_count} passed, {failed_count} failed")
