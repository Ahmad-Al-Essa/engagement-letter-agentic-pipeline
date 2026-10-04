"""The drafting agent, and the checks every draft letter must pass before it is saved.

What the drafter sees: the approved scope only (company, contact, the selected
services with their catalog descriptions and the client's words, the deadline).
It never sees the client's original message, so an instruction hidden in that
message cannot reach it (Week 5 forum: "structured isolation").

What the drafter writes: only the parts that change per client (introduction,
the work under each service, client responsibilities, timeline). Code adds
everything fixed: the "DRAFT, not to be sent" banner, the fee placeholder,
the standard terms, the signature block. A model can't change what it never
writes.

Every draft then goes through:
  1. code checks (check_letter): exactly the approved services, no other
     service's code or name, no amounts of money, no other client's name, no
     empty sections;
  2. the checker reading the letter (review_letter): does it promise anything
     outside the approved services?
A draft that fails goes back to the drafter with the reasons, up to
MAX_ATTEMPTS times; after that, the case goes to a partner.
"""

import re
import time
from datetime import date
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import config
from pipeline.memo_checks import normalise
from pipeline.sbp_tools import call_tool

PROMPTS = Path(__file__).parent.parent / "prompts"
MAX_ATTEMPTS = 3
FEE_PLACEHOLDER = "[FEES: to be completed by the partner]"

# Amounts of money: "KWD 5,000", "5,000 KD", "$200", "300 dinars" ...
MONEY = re.compile(r"(kwd|kd|usd|\$|dinars?)\s?\d|\d[\d,.]*\s?(kwd|kd|usd|dinars?)\b", re.IGNORECASE)


class ServiceScope(BaseModel):
    code: str = Field(description="The approved service's code, exactly as given")
    work_description: str = Field(description="Two to four sentences: what SB&P will do for this client")


class LetterDraft(BaseModel):
    introduction: str = Field(description="One short paragraph")
    services: list[ServiceScope] = Field(description="One entry per approved service, in the same order")
    client_responsibilities: list[str] = Field(description="Three to five points specific to the approved services")
    timeline: str = Field(description="Two or three sentences: the plan against the client's deadline and what it depends on")


class LetterReview(BaseModel):
    passes: bool
    problems: list[str] = Field(description="One short sentence per problem; empty if it passes")


# ---------------------------------------------------------------------------
# What the drafter is given
# ---------------------------------------------------------------------------

async def service_details(tools, codes):
    """Each approved service's catalog entry, read by code from the MCP server.
    Returns {code: {"name": ..., "text": full catalog entry}}."""
    details = {}
    for code in codes:
        text = await call_tool(tools, "get_service", {"code": code})
        # First line reads: "AUD-001 Financial Statement Audit (Audit & Assurance) | status: on"
        first_line = text.splitlines()[0]
        name = first_line[len(code):].split("(")[0].strip()
        details[code] = {"name": name, "text": text}
    return details


def scope_message(intake, memo, details):
    """The approved scope, as the drafter sees it. Built from the memo, never from the raw request."""
    lines = [f"Company: {intake['company']}", f"Contact: {intake['contact']}", "", "Approved services:"]
    for service in memo.services:
        lines.append(f"- {details[service.code]['text']}")
        lines.append(f"  Why selected: {service.reason}")
        lines.append(f"  Client's words: \"{service.client_quote}\"")
    lines.append("")
    lines.append(f"Client's deadline: {memo.deadline or 'none given'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writing and rendering
# ---------------------------------------------------------------------------

async def write_letter(intake, memo, details, earlier_problems, trace, attempt):
    """Ask the drafter for the client-specific parts. On a retry, it is told what failed last time."""
    message = scope_message(intake, memo, details)
    if earlier_problems:
        message += "\n\nYour previous draft was rejected. Fix these problems:\n"
        for problem in earlier_problems:
            message += f"- {problem}\n"

    messages = [SystemMessage((PROMPTS / "drafter_system.md").read_text(encoding="utf-8")), HumanMessage(message)]
    start = time.time()
    writer = config.get_llm("drafter").with_structured_output(LetterDraft, method="json_schema")
    draft = await writer.ainvoke(messages)
    trace.log("letter_draft", {"attempt": attempt, "seconds": round(time.time() - start, 1), "draft": draft.model_dump()})
    return draft


def render_letter(intake, draft, details, for_review=False):
    """Put the full letter together. Everything except the drafter's four parts is fixed text.

    for_review=True leaves out the fixed fee and terms sections, so the checker
    reads only what the drafter wrote. (When it saw them, it once flagged the
    fee placeholder itself as a problem.)
    """
    standard_terms = (PROMPTS / "letter_standard_terms.md").read_text(encoding="utf-8").strip()

    lines = [
        "DRAFT ENGAGEMENT LETTER: FOR PARTNER REVIEW. NOT TO BE SENT.",
        "",
        "SB&P",
        f"Date: {date.today().isoformat()}",
        f"To: {intake['contact']}, {intake['company']}",
        "",
        f"Dear {intake['contact']},",
        "",
        draft.introduction.strip(),
        "",
        "1. Scope of services",
    ]
    for number, service in enumerate(draft.services, start=1):
        name = details.get(service.code, {}).get("name", "")
        lines.append(f"1.{number} {service.code} {name}")
        lines.append(service.work_description.strip())
        lines.append("")
    lines.append("2. Your responsibilities")
    for point in draft.client_responsibilities:
        lines.append(f"- {point.strip()}")
    lines += ["", "3. Timeline", draft.timeline.strip()]
    if not for_review:
        lines += ["", "4. Fees", FEE_PLACEHOLDER, "",
                  "5. Terms", standard_terms, "",
                  "Yours sincerely,", "[Partner name], for SB&P"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

async def check_letter(draft, letter_text, memo, intake, tools):
    """Code checks on one draft. Returns a list of problems (empty = passed)."""
    problems = []

    # 1. Exactly the approved services: none missing, none added.
    approved = []
    for service in memo.services:
        approved.append(service.code)
    written = []
    for service in draft.services:
        written.append(service.code)
    for code in approved:
        if code not in written:
            problems.append(f"The letter is missing the approved service {code}.")
    for code in written:
        if code not in approved:
            problems.append(f"The letter includes {code}, which is not an approved service.")

    # 2. No other service is mentioned: excluded, paused, or any code outside the approved list.
    for code in re.findall(r"\b[A-Z]{2,4}-\d{3}\b", letter_text):
        if code not in approved:
            problems.append(f"The letter mentions {code}, which is not an approved service.")
    for item in memo.excluded:
        details = await call_tool(tools, "get_service", {"code": item.code})
        name = details.splitlines()[0][len(item.code):].split("(")[0].strip()
        if name and normalise(name) in normalise(letter_text):
            problems.append(f"The letter mentions \"{name}\", a service that was ruled out.")

    # 3. No amounts of money: fees are the partner's decision.
    if MONEY.search(letter_text):
        problems.append("The letter states an amount of money; fees are set by the partner.")

    # 4. No other client's name (confidentiality). An empty name lists every client.
    all_clients = await call_tool(tools, "lookup_client", {"name": ""})
    for line in all_clients.splitlines():
        if not line.startswith("- client_id "):
            continue
        name = line.split(":", 1)[1].split("|")[0].strip()
        if normalise(name.split("(")[0]) == normalise(intake["company"].split("(")[0]):
            continue
        if normalise(name) in normalise(letter_text):
            problems.append(f"The letter mentions another client ({name}).")

    # 5. No empty parts.
    if draft.introduction.strip() == "":
        problems.append("The introduction is empty.")
    for service in draft.services:
        if len(service.work_description.split()) < 10:
            problems.append(f"The description of work for {service.code} is too short.")
    if len(draft.client_responsibilities) == 0:
        problems.append("The client's responsibilities are missing.")
    if draft.timeline.strip() == "":
        problems.append("The timeline is empty.")

    return problems


async def review_letter(letter_text, memo, details, trace, attempt):
    """The checker (a different model family) reads the letter against the approved services.
    letter_text here is the drafter's part only (render_letter with for_review=True)."""
    approved = ["Approved services:"]
    for service in memo.services:
        approved.append(f"- {details[service.code]['text']}")
    message = "\n".join(approved) + "\n\nDraft letter:\n\n" + letter_text

    messages = [SystemMessage((PROMPTS / "letter_review.md").read_text(encoding="utf-8")), HumanMessage(message)]
    start = time.time()
    reviewer = config.get_llm("letter_checker").with_structured_output(LetterReview, method="json_schema")
    review = await reviewer.ainvoke(messages)
    trace.log("letter_review", {"attempt": attempt, "seconds": round(time.time() - start, 1),
                                "review": review.model_dump()})
    return review
