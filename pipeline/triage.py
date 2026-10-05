"""The triage agent: reads one intake and writes the scope memo.

It works in two parts:

  Part 1, the ReAct loop (agent_loop.py). The model thinks, calls a tool,
  reads the result, and repeats: look up the client (or record a prospect),
  then search the catalog once per need. It can only call the three tools
  in TRIAGE_TOOLS (sbp_tools.py).

  Part 2, the memo. With everything the tools returned in front of it, the
  model writes the scope memo, forced into the ScopeMemo shape. Thinking is
  off for this part (see config.py: with it on, the model looped).

The instructions it follows are plain text files in prompts/, so the wording
can be edited without touching this code.

Try it (runs the input checks, triage, and the memo checks on one intake):
    python3 -m pipeline.triage intakes/record_1_al_rawdah.txt
"""

import asyncio
import sys
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

import config
from pipeline.input_checks import check_intake
from pipeline.memo_checks import check_memo, normalise
from pipeline.agent_loop import run_tool_loop
from pipeline.sbp_tools import TRIAGE_TOOLS, sbp_data_tools
from pipeline.scope_memo import ScopeMemo
from pipeline.trace import Trace

PROMPTS = Path(__file__).parent.parent / "prompts"


def intake_message(intake):
    """The intake as triage sees it: header fields, then the client's own words."""
    return (
        f"Company: {intake['company']}\n"
        f"Contact: {intake['contact']}\n"
        f"Sector: {intake['sector']}\n"
        f"Size: {intake['size']}\n"
        f"Received: {intake['received']}\n\n"
        f"Client's request:\n\"{intake['request']}\""
    )


async def run_triage(intake, tools, trace):
    """Run triage on one intake. Returns the scope memo and its owner (client_id, prospect_id)."""
    system_prompt = (PROMPTS / "triage_system.md").read_text(encoding="utf-8")
    memo_prompt = (PROMPTS / "triage_memo.md").read_text(encoding="utf-8")

    messages = [SystemMessage(system_prompt), HumanMessage(intake_message(intake))]

    # Part 1: the ReAct loop (see agent_loop.py).
    tool_log = await run_tool_loop("triage", config.get_llm("triage"), TRIAGE_TOOLS, tools, messages, trace)

    # Part 2: the memo, forced into the ScopeMemo shape.
    messages.append(HumanMessage(memo_prompt))
    start = time.time()
    memo_writer = config.get_llm("triage_memo").with_structured_output(ScopeMemo, method="json_schema")
    memo = await memo_writer.ainvoke(messages)
    trace.log("scope_memo", {"seconds": round(time.time() - start, 1), "memo": memo.model_dump()})

    owner = find_owner(intake, tool_log)
    trace.log("owner", {"client_id": owner[0], "prospect_id": owner[1]})

    return memo, owner


def find_owner(intake, tool_log):
    """Work out, in code, which client or prospect this run is about.

    The model never handles database ids (it once put a prospect's id in the
    client_id field, which pointed at a different, real client). Instead,
    code reads the ids from what the tools returned, and accepts only the
    company named in the intake:

      - a client, if lookup_client listed a client with the same name as the
        intake's company; or else
      - a prospect, if add_prospect was called with that same name.

    "Same name" ignores capitals, punctuation, and a note in brackets, so
    "Behbehani Family Holding (unlisted)" matches "Behbehani Family Holding".

    Returns (client_id, prospect_id). Both are None if neither happened; the
    memo checks then fail and the case goes to a partner.
    """
    company = intake["company"]

    for entry in tool_log:
        if entry["tool"] != "lookup_client":
            continue
        # Each match is one line: "- client_id 1: Al-Rawdah Trading Company K.S.C.C. | contact: ..."
        for line in entry["result"].splitlines():
            if not line.startswith("- client_id "):
                continue
            id_text, rest = line[len("- client_id "):].split(":", 1)
            name = rest.split("|")[0]
            if same_company(name, company):
                return int(id_text), None

    for entry in tool_log:
        if entry["tool"] != "add_prospect":
            continue
        if not same_company(entry["args"].get("name", ""), company):
            continue
        # The result reads "New prospect recorded: prospect_id 4 (...)" or "...on file: prospect_id 4 (...)"
        after = entry["result"].split("prospect_id ", 1)
        if len(after) == 2:
            return None, int(after[1].split()[0])

    return None, None


def same_company(name_a, name_b):
    """True if two company names are the same, ignoring capitals, punctuation,
    and anything in brackets."""
    base_a = normalise(name_a.split("(")[0])
    base_b = normalise(name_b.split("(")[0])
    return base_a != "" and base_a == base_b


def print_memo(memo, owner, problems):
    """Plain printout of the memo and its check results."""
    client_id, prospect_id = owner
    if client_id is not None:
        owner_text = f"client_id {client_id}"
    elif prospect_id is not None:
        owner_text = f"prospect_id {prospect_id}"
    else:
        owner_text = "NO MATCHING CLIENT OR PROSPECT"
    print(f"\nSCOPE MEMO: {memo.company} ({owner_text})")
    print(f"Verdict: {memo.verdict} — {memo.verdict_reason}")

    print("\nSelected services:")
    for s in memo.services:
        print(f"  {s.code}: {s.reason}")
        print(f"      client's words: \"{s.client_quote}\"")
    print("\nRuled out (near neighbours):")
    for e in memo.excluded:
        print(f"  {e.code}: {e.reason}")
    print("\nNot offered by the firm:")
    for n in memo.not_offered:
        print(f"  {n.need}  (\"{n.client_quote}\")")
    print("\nPaused:")
    for p in memo.paused:
        print(f"  {p.code}  (\"{p.client_quote}\")")
    print(f"\nDeadline: {memo.deadline or '-'}")
    print("Open questions:")
    for q in memo.open_questions:
        print(f"  - {q}")

    print("\nCODE CHECKS ON THE MEMO:", "passed" if not problems else "FAILED")
    for problem in problems:
        print(f"  - {problem}")


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

    print_memo(memo, owner, memo_problems)
    print(f"\nRun {trace.run_id} took {time.time() - run_start:.0f}s. Trace log: {trace.path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 -m pipeline.triage <intake file>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
