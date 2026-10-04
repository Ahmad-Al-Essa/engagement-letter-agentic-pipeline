"""The checker: works out its own answer first, then code compares it with triage's.

Why "its own answer first": a judge that reads an answer before checking it
tends to agree with it (Week 5 readings, Zheng et al.). So the checker never
sees triage's memo. It reads the same intake, splits it into needs, searches
the catalog for each, and writes its own view. Then plain code compares the
two answers. Like two people coding the same interview independently, and
then comparing.

Why a different model: the checker runs on qwen3.5 (Alibaba), triage on
gemma4 (Google). A mistake one model family tends to make is less likely to
be repeated by the other (Week 5, "Method 2: a separate checker").

  Part 1, the checker lists the client's needs (thinking on: in testing,
          only with thinking on did it notice a need the client describes
          but doesn't ask for by name, e.g. Record 2's overbilling).
  Part 2, code searches the catalog exactly once per need. (When the
          checker searched for itself, it reworded the same needs 22 times
          until something matched: fishing for a service is the force-fit
          we are guarding against, so the rule is enforced in code.)
  Part 3, the checker decides which service, if any, fits each need
          (thinking off), in a fixed shape.
  Part 4, code compares the checker's view with triage's memo.

Try it (runs input checks, triage, memo checks, then the checker):
    python3 -m pipeline.checker intakes/record_2_gulf_marine.txt
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import config
from pipeline.input_checks import check_intake
from pipeline.memo_checks import check_memo, quote_found
from pipeline.sbp_tools import CHECKER_TOOLS, call_tool, sbp_data_tools
from pipeline.trace import Trace
from pipeline.triage import intake_message, print_memo, run_triage

PROMPTS = Path(__file__).parent.parent / "prompts"


class ListedNeed(BaseModel):
    need: str = Field(description="The need in plain words")
    client_quote: str = Field(description="The client's words for it, copied exactly from the request")


class NeedsList(BaseModel):
    needs: list[ListedNeed]


class CheckerNeed(BaseModel):
    need: str = Field(description="The need in plain words")
    client_quote: str = Field(description="The client's words for it, copied exactly from the request")
    code: str = Field(description="The one service code from the search that fits, or an empty string if none fits")


class CheckerView(BaseModel):
    needs: list[CheckerNeed]
    verdict: Literal["in_scope", "out_of_scope", "unclear"]
    verdict_reason: str = Field(description="One or two sentences explaining the verdict")


async def form_own_view(intake, tools, trace):
    """Parts 1 to 3: the checker reads the intake (never the memo) and writes its own view."""
    system_prompt = (PROMPTS / "checker_system.md").read_text(encoding="utf-8")
    needs_prompt = (PROMPTS / "checker_needs.md").read_text(encoding="utf-8")
    view_prompt = (PROMPTS / "checker_view.md").read_text(encoding="utf-8")

    # Part 1: list the needs (thinking on).
    messages = [SystemMessage(system_prompt), HumanMessage(intake_message(intake) + "\n\n" + needs_prompt)]
    start = time.time()
    needs_writer = config.get_llm("checker").with_structured_output(NeedsList, method="json_schema")
    needs_list = await needs_writer.ainvoke(messages)
    trace.log("checker_needs", {"seconds": round(time.time() - start, 1), "needs": needs_list.model_dump()["needs"]})

    # Part 2: code searches the catalog once per need.
    search_tool = CHECKER_TOOLS[0]
    results_text = ""
    for number, item in enumerate(needs_list.needs, start=1):
        result = await call_tool(tools, search_tool, {"need": item.need})
        trace.log("checker_search", {"need": item.need, "result": result})
        results_text += (f"\nNeed {number}: {item.need}\n"
                         f"Client's words: \"{item.client_quote}\"\n"
                         f"Search result:\n{result}\n")

    # Part 3: decide which service, if any, fits each need (thinking off).
    messages.append(HumanMessage(results_text + "\n" + view_prompt))
    start = time.time()
    view_writer = config.get_llm("checker_write").with_structured_output(CheckerView, method="json_schema")
    view = await view_writer.ainvoke(messages)
    trace.log("checker_view", {"seconds": round(time.time() - start, 1), "view": view.model_dump()})
    return view


def compare_with_memo(memo, view, intake):
    """Part 4, plain code: where do triage's memo and the checker's view differ?

    Returns (agrees, differences). Each difference is a plain-English sentence
    for the partner. The checker's needs count only if its quote really appears
    in the client's request (the same "no quote, no service" rule as triage).
    """
    differences = []
    request = intake["request"]

    triage_codes = []
    for service in memo.services:
        triage_codes.append(service.code)
    paused_codes = []
    for item in memo.paused:
        paused_codes.append(item.code)

    checker_codes = []
    for need in view.needs:
        if need.code == "":
            continue
        if not quote_found(need.client_quote, request):
            differences.append(f"The checker's quote for \"{need.need}\" is not in the client's request, "
                               f"so its {need.code} was not counted.")
            continue
        checker_codes.append(need.code)
        if need.code not in triage_codes and need.code not in paused_codes:
            differences.append(f"The checker found {need.code} for \"{need.need}\" (client's words: "
                               f"\"{need.client_quote}\"), which triage did not select.")

    for code in triage_codes:
        if code not in checker_codes:
            differences.append(f"Triage selected {code}, which the checker did not find for any need.")

    if memo.verdict != view.verdict:
        differences.append(f"The verdicts differ: triage says {memo.verdict}, the checker says {view.verdict}.")

    agrees = len(differences) == 0
    return agrees, differences


def print_check(view, agrees, differences):
    print(f"\nCHECKER'S OWN VIEW (verdict: {view.verdict}) — {view.verdict_reason}")
    for need in view.needs:
        print(f"  {need.code or '(no service)':13} {need.need}  (\"{need.client_quote}\")")
    print("\nCOMPARISON:", "triage and checker AGREE" if agrees else "triage and checker DISAGREE")
    for difference in differences:
        print(f"  - {difference}")


async def main(intake_path):
    text = Path(intake_path).read_text(encoding="utf-8")
    trace = Trace(label=intake_path)
    run_start = time.time()

    passed, problems, intake = check_intake(text)
    trace.log("input_checks", {"passed": passed, "problems": problems, "intake": intake})
    if not passed:
        print("Input checks FAILED; this intake goes to a partner, not to an agent:")
        for problem in problems:
            print(f"  - {problem}")
        return

    async with sbp_data_tools() as tools:
        memo, owner = await run_triage(intake, tools, trace)
        memo_problems = await check_memo(memo, intake, tools, owner)
        trace.log("memo_checks", {"problems": memo_problems})

        view = await form_own_view(intake, tools, trace)
        agrees, differences = compare_with_memo(memo, view, intake)
        trace.log("comparison", {"agrees": agrees, "differences": differences})

    print_memo(memo, owner, memo_problems)
    print_check(view, agrees, differences)
    print(f"\nRun {trace.run_id} took {time.time() - run_start:.0f}s. Trace log: {trace.path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 -m pipeline.checker <intake file>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
