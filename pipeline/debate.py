"""The debate: when triage and the checker disagree, each sees the other's answer and may change its mind.

Called only when the two disagree. (If both say "unclear", the case is
genuinely ambiguous and goes straight to a partner: there is nothing to argue.)

Each round, both agents see the other side's latest answer and the list of
differences, and write a revised answer. They may defend their answer or
change it, and are told not to change it just to end the disagreement.
Code then compares the two again:

    agree           → stop; the case is routed on the agreed verdict
    still disagree  → another round, up to MAX_ROUNDS
    after MAX_ROUNDS → a partner decides, with both final positions

Week 4 design and debate practical: stop at agreement (in the practical, the
sceptic invented a new flaw after both had agreed), cap the rounds, and treat
"no agreement" as a case for a human. The two sides run on different model
families (gemma4 and qwen3.5), so this is a debate between genuinely
different agents, not one model talking to itself.

The revised memo must pass the same code checks as the first one, and may
only use services that one of the two sides already named (no new services
from memory in the middle of an argument).
"""

import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

import config
from pipeline.checker import CheckerView, compare_with_memo
from pipeline.memo_checks import check_memo
from pipeline.sbp_tools import call_tool
from pipeline.scope_memo import ScopeMemo
from pipeline.triage import intake_message

PROMPTS = Path(__file__).parent.parent / "prompts"
MAX_ROUNDS = 2


def memo_text(memo):
    """Triage's memo, written out for the other side to read."""
    lines = [f"Verdict: {memo.verdict}. {memo.verdict_reason}", "Selected services:"]
    if not memo.services:
        lines.append("  (none)")
    for service in memo.services:
        lines.append(f"  {service.code}: {service.reason} (client's words: \"{service.client_quote}\")")
    lines.append("Ruled out:")
    if not memo.excluded:
        lines.append("  (none)")
    for item in memo.excluded:
        lines.append(f"  {item.code}: {item.reason}")
    lines.append("Not offered by the firm:")
    if not memo.not_offered:
        lines.append("  (none)")
    for item in memo.not_offered:
        lines.append(f"  {item.need} (client's words: \"{item.client_quote}\")")
    if memo.paused:
        lines.append("Paused:")
        for item in memo.paused:
            lines.append(f"  {item.code} (client's words: \"{item.client_quote}\")")
    return "\n".join(lines)


def view_text(view):
    """The checker's view, written out for the other side to read."""
    lines = [f"Verdict: {view.verdict}. {view.verdict_reason}", "Needs:"]
    for need in view.needs:
        lines.append(f"  {need.need}: {need.code or 'no fitting service'} (client's words: \"{need.client_quote}\")")
    return "\n".join(lines)


async def catalog_entries(tools, memo, view):
    """Catalog entries for every service either side has named (read by code)."""
    codes = []
    for service in memo.services:
        codes.append(service.code)
    for item in memo.excluded:
        codes.append(item.code)
    for item in memo.paused:
        codes.append(item.code)
    for need in view.needs:
        if need.code:
            codes.append(need.code)

    unique_codes = []
    for code in codes:
        if code not in unique_codes:
            unique_codes.append(code)

    entries = []
    for code in unique_codes:
        entries.append(await call_tool(tools, "get_service", {"code": code}))
    return unique_codes, "\n\n".join(entries)


async def run_debate(intake, memo, view, differences, owner, tools, trace):
    """Run up to MAX_ROUNDS rounds. Returns (memo, view, agrees, differences, memo_problems, rounds)."""
    codes_in_play, entries = await catalog_entries(tools, memo, view)
    # Each side also gets the same instructions for filling in its answer as the first time.
    # (Without them, triage once put not-offered needs under "services" with the code "none";
    # the memo checks caught it.)
    triage_rules = ((PROMPTS / "debate_triage.md").read_text(encoding="utf-8") + "\n\n"
                    + (PROMPTS / "triage_memo.md").read_text(encoding="utf-8"))
    checker_rules = ((PROMPTS / "debate_checker.md").read_text(encoding="utf-8") + "\n\n"
                     + (PROMPTS / "checker_view.md").read_text(encoding="utf-8"))

    agrees = False
    memo_problems = []
    for round_number in range(1, MAX_ROUNDS + 1):
        shared = (intake_message(intake)
                  + "\n\nDifferences between the two answers:\n- " + "\n- ".join(differences)
                  + "\n\nCatalog entries for the services either side named:\n" + entries)

        # Both sides answer this round from the other side's previous answer.
        start = time.time()
        triage_writer = config.get_llm("triage_memo").with_structured_output(ScopeMemo, method="json_schema")
        new_memo = await triage_writer.ainvoke([
            SystemMessage(triage_rules),
            HumanMessage(shared + "\n\nYOUR scope memo:\n" + memo_text(memo)
                         + "\n\nThe CHECKER's view:\n" + view_text(view)),
        ])
        checker_writer = config.get_llm("checker_write").with_structured_output(CheckerView, method="json_schema")
        new_view = await checker_writer.ainvoke([
            SystemMessage(checker_rules),
            HumanMessage(shared + "\n\nYOUR view:\n" + view_text(view)
                         + "\n\nTRIAGE's scope memo:\n" + memo_text(memo)),
        ])
        memo = new_memo
        view = new_view

        # The revised memo must pass the same code checks, and use only services already in play.
        memo_problems = await check_memo(memo, intake, tools, owner)
        for service in memo.services:
            if service.code not in codes_in_play:
                memo_problems.append(f"In the debate, triage selected {service.code}, which neither side "
                                     f"had named before.")

        agrees, differences = compare_with_memo(memo, view, intake)
        trace.log("debate_round", {
            "round": round_number,
            "seconds": round(time.time() - start, 1),
            "memo": memo.model_dump(),
            "view": view.model_dump(),
            "agrees": agrees,
            "differences": differences,
            "memo_problems": memo_problems,
        })
        if memo_problems or agrees:
            return memo, view, agrees, differences, memo_problems, round_number

    return memo, view, agrees, differences, memo_problems, MAX_ROUNDS
