"""Test the code checks on draft letters (no model; uses the MCP server for catalog and client names).

Run from the repo root:
    python3 tests/test_letter_checks.py
"""

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "data"))
import build_db  # noqa: E402
from pipeline.drafter import LetterDraft, ServiceScope, check_letter, render_letter  # noqa: E402
from pipeline.sbp_tools import sbp_data_tools  # noqa: E402
from pipeline.scope_memo import ExcludedService, ScopeMemo, SelectedService  # noqa: E402

passed_count = 0
failed_count = 0

INTAKE = {"company": "Al-Rawdah Trading Company K.S.C.C.", "contact": "Finance Manager"}
MEMO = ScopeMemo(
    company=INTAKE["company"], verdict="in_scope", verdict_reason="test",
    services=[SelectedService(code="AUD-001", reason="bank", client_quote="audited statements"),
              SelectedService(code="TAX-002", reason="zakat", client_quote="zakat declaration prepared and filed")],
    excluded=[ExcludedService(code="AUD-002", reason="the bank asked for an audit, not a review")],
    not_offered=[], paused=[], deadline="end of Q1", open_questions=[])
DETAILS = {"AUD-001": {"name": "Financial Statement Audit", "text": ""},
           "TAX-002": {"name": "Zakat Declaration", "text": ""}}
WORK = "SB&P will carry out this work for the client, following our standard approach and the agreed timetable."


def draft_with(services, introduction="Thank you for contacting SB&P about your audit and zakat filing."):
    return LetterDraft(introduction=introduction, services=services,
                       client_responsibilities=["Provide the financial records."], timeline="Before the end of Q1.")


GOOD = [ServiceScope(code="AUD-001", work_description=WORK), ServiceScope(code="TAX-002", work_description=WORK)]


async def expect(tools, description, draft, should_pass, phrase=""):
    global passed_count, failed_count
    letter_text = render_letter(INTAKE, draft, DETAILS)
    problems = await check_letter(draft, letter_text, MEMO, INTAKE, tools)
    ok = (len(problems) == 0) == should_pass and (phrase == "" or phrase in " ".join(problems))
    if ok:
        passed_count += 1
    else:
        failed_count += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {description}")
    for problem in problems:
        print(f"       - {problem}")


async def main():
    build_db.build()
    print()
    async with sbp_data_tools() as tools:
        await expect(tools, "a letter with exactly the approved services passes", draft_with(GOOD), True)
        await expect(tools, "a missing approved service fails",
                     draft_with(GOOD[:1]), False, "missing the approved service TAX-002")
        await expect(tools, "an added service fails",
                     draft_with(GOOD + [ServiceScope(code="FIN-004", work_description=WORK)]), False,
                     "FIN-004, which is not an approved service")
        await expect(tools, "naming a ruled-out service fails",
                     draft_with(GOOD, "Thank you. A Review Engagement would not be enough for your bank."), False,
                     "Review Engagement")
        await expect(tools, "an amount of money fails",
                     draft_with(GOOD, "Thank you. The audit will cost KWD 4,500."), False, "amount of money")
        await expect(tools, "another client's name fails (confidentiality)",
                     draft_with(GOOD, "Thank you. We did similar work for Dasma Medical Supplies Co. last year."),
                     False, "another client (Dasma Medical Supplies Co.)")
    build_db.build()
    print(f"\n{passed_count} passed, {failed_count} failed")


asyncio.run(main())
