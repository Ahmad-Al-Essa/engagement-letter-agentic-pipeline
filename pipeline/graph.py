"""The pipeline as a LangGraph graph: which step runs after which, and where each case ends.

    input checks ─fail──────────────────────────────────────────┐
         │ pass                                                 │
       triage (agent)                                           │
         │                                                      │
    memo checks ─fail───────────────────────────────────────────┤
         │ pass                                                 │
      checker (agent)                                           ▼
         │                                          to a partner (escalate)
         ├─ disagree, and it could change the route ──► debate (both agents, up to 2 rounds)
         │                                               agree → route below; still disagree → partner
       route ──┬─ agree + in_scope ──► draft (agent) ──► letter checks ──┬─ pass ──► save for partner review
               │                            ▲                            ├─ fail, tries left ─┘ (back to draft)
               │                            └────────────────────────────┘
               │                                                         └─ fail 3 times ──► to a partner
               ├─ agree + out_of_scope ──► recommended decline (a partner decides and contacts the client)
               └─ disagree, or unclear ──► to a partner (escalate)

Routing is plain code reading fields of the memo and the comparison: no model
decides where a case goes, so a model can't misroute it (Week 4 design).

Every route ends with something a human sees: a draft waiting for review, or
a case in the partner's queue. Nothing reaches a client without a partner.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from pipeline.checker import compare_with_memo, form_own_view
from pipeline.debate import run_debate
from pipeline.drafter import MAX_ATTEMPTS, check_letter, render_letter, review_letter, service_details, write_letter
from pipeline.input_checks import check_intake
from pipeline.memo_checks import check_memo
from pipeline.sbp_tools import call_tool
from pipeline.triage import run_triage


class RunState(TypedDict, total=False):
    """Everything one run knows, passed from step to step."""
    text: str                 # the raw intake
    intake: dict              # the parsed intake (input_checks)
    input_problems: list      # why the input checks failed, if they did
    memo: object              # triage's scope memo
    owner: tuple              # (client_id, prospect_id), found by code
    memo_problems: list       # why the memo checks failed, if they did
    view: object              # the checker's own view
    agrees: bool              # did triage and the checker agree?
    differences: list         # where they differ, in plain English
    debate_rounds: int        # how many debate rounds were held (0 if none)
    details: dict             # the approved services' catalog entries (read by code)
    attempts: int             # how many drafts have been written
    letter_text: str          # the latest full letter
    letter_problems: list     # why the latest draft failed its checks, if it did
    letter_draft: object      # the drafter's latest output (before rendering)
    route: str                # "draft", "decline", or "partner"
    outcome: str              # what happened at the end, in plain English


def build_graph(tools, trace):
    """Build the graph for one run. `tools` is the open MCP connection, `trace` the run's log."""

    # ---- the steps (nodes) ----

    async def step_input_checks(state):
        passed, problems, intake = check_intake(state["text"])
        trace.log("input_checks", {"passed": passed, "problems": problems, "intake": intake})
        return {"intake": intake, "input_problems": problems}

    async def step_triage(state):
        memo, owner = await run_triage(state["intake"], tools, trace)
        return {"memo": memo, "owner": owner}

    async def step_memo_checks(state):
        problems = await check_memo(state["memo"], state["intake"], tools, state["owner"])
        trace.log("memo_checks", {"problems": problems})
        return {"memo_problems": problems}

    async def step_checker(state):
        view = await form_own_view(state["intake"], tools, trace)
        agrees, differences = compare_with_memo(state["memo"], view, state["intake"])
        trace.log("comparison", {"agrees": agrees, "differences": differences})
        return {"view": view, "agrees": agrees, "differences": differences}

    async def step_debate(state):
        memo, view, agrees, differences, problems, rounds = await run_debate(
            state["intake"], state["memo"], state["view"], state["differences"], state["owner"], tools, trace)
        return {"memo": memo, "view": view, "agrees": agrees, "differences": differences,
                "memo_problems": problems, "debate_rounds": rounds}

    async def step_escalate(state):
        """Send the case to a partner, with every reason collected on the way."""
        reasons = []
        for problem in state.get("input_problems", []):
            reasons.append(f"Input check: {problem}")
        for problem in state.get("memo_problems", []):
            reasons.append(f"Memo check: {problem}")
        if state.get("debate_rounds", 0) > 0 and not state.get("agrees", False):
            reasons.append(f"Triage and checker still disagreed after {state['debate_rounds']} round(s) of debate.")
        for difference in state.get("differences", []):
            reasons.append(f"Checker: {difference}")
        if state.get("letter_problems"):
            reasons.append(f"The draft letter failed its checks {state['attempts']} times. Last problems:")
            for problem in state["letter_problems"]:
                reasons.append(f"Letter check: {problem}")
        memo = state.get("memo")
        if memo is not None and memo.verdict == "unclear":
            reasons.append(f"Triage verdict is unclear: {memo.verdict_reason}")

        client_id, prospect_id = state.get("owner", (None, None))
        args = {"reason": " | ".join(reasons), "run_id": trace.run_id}
        if client_id is not None:
            args["client_id"] = client_id
        elif prospect_id is not None:
            args["prospect_id"] = prospect_id
        result = await call_tool(tools, "escalate_to_partner", args)
        trace.log("escalated", {"reasons": reasons, "result": result})
        return {"route": "partner", "outcome": result}

    async def step_decline(state):
        """Out of scope, and the checker agrees: recommend a decline. A partner sends it, not the system."""
        memo = state["memo"]
        client_id, prospect_id = state["owner"]
        reason = (f"RECOMMENDED DECLINE (triage and checker agree it is out of scope). The partner decides "
                  f"and contacts the client; the system has sent nothing. Reason: {memo.verdict_reason}")
        args = {"reason": reason, "run_id": trace.run_id}
        if client_id is not None:
            args["client_id"] = client_id
        else:
            args["prospect_id"] = prospect_id
        result = await call_tool(tools, "escalate_to_partner", args)
        trace.log("declined", {"reason": reason, "result": result})
        return {"route": "decline", "outcome": result}

    async def step_draft(state):
        """The drafter writes the client-specific parts; code renders the full letter."""
        memo = state["memo"]
        details = state.get("details")
        if details is None:
            codes = []
            for service in memo.services:
                codes.append(service.code)
            details = await service_details(tools, codes)
        attempt = state.get("attempts", 0) + 1
        draft = await write_letter(state["intake"], memo, details, state.get("letter_problems", []), trace, attempt)
        letter_text = render_letter(state["intake"], draft, details)
        return {"details": details, "attempts": attempt, "letter_draft": draft, "letter_text": letter_text}

    async def step_letter_checks(state):
        """Code checks first; if they pass, the checker reads the letter against the approved services."""
        attempt = state["attempts"]
        problems = await check_letter(state["letter_draft"], state["letter_text"], state["memo"],
                                      state["intake"], tools)
        if not problems:
            drafter_part = render_letter(state["intake"], state["letter_draft"], state["details"], for_review=True)
            review = await review_letter(drafter_part, state["memo"], state["details"], trace, attempt)
            if not review.passes:
                if review.problems:
                    for problem in review.problems:
                        problems.append(f"Checker: {problem}")
                else:
                    problems.append("Checker: the letter failed review (no reason given).")
        trace.log("letter_checks", {"attempt": attempt, "problems": problems, "letter_text": state["letter_text"]})
        return {"letter_problems": problems}

    async def step_save(state):
        """Save the draft for partner review. The status is always 'pending_review'."""
        codes = []
        for service in state["memo"].services:
            codes.append(service.code)
        client_id, prospect_id = state["owner"]
        args = {"service_codes": codes, "letter_text": state["letter_text"], "run_id": trace.run_id}
        if client_id is not None:
            args["client_id"] = client_id
        else:
            args["prospect_id"] = prospect_id
        result = await call_tool(tools, "save_draft_for_review", args)
        trace.log("saved", {"result": result})
        return {"route": "draft", "outcome": result}

    # ---- the routing decisions (plain code) ----

    def after_input_checks(state):
        if state["input_problems"]:
            return "escalate"
        return "triage"

    def after_memo_checks(state):
        if state["memo_problems"]:
            return "escalate"
        return "checker"

    def after_letter_checks(state):
        if not state["letter_problems"]:
            return "save"
        if state["attempts"] < MAX_ATTEMPTS:
            return "draft"        # back to the drafter, with the reasons
        return "escalate"

    def route_on_verdict(state):
        verdict = state["memo"].verdict
        if verdict == "in_scope":
            return "draft"
        if verdict == "out_of_scope":
            return "decline"
        return "escalate"     # unclear: a partner decides

    def after_checker(state):
        if state["agrees"]:
            return route_on_verdict(state)
        if state["memo"].verdict == "unclear" and state["view"].verdict == "unclear":
            return "escalate"     # both unclear: a partner decides whatever the debate says
        return "debate"

    def after_debate(state):
        if state["memo_problems"]:
            return "escalate"
        if state["agrees"]:
            return route_on_verdict(state)
        return "escalate"     # still disagree: a partner decides

    # ---- the graph ----

    graph = StateGraph(RunState)
    graph.add_node("input_checks", step_input_checks)
    graph.add_node("triage", step_triage)
    graph.add_node("memo_checks", step_memo_checks)
    graph.add_node("checker", step_checker)
    graph.add_node("debate", step_debate)
    graph.add_node("escalate", step_escalate)
    graph.add_node("decline", step_decline)
    graph.add_node("draft", step_draft)
    graph.add_node("letter_checks", step_letter_checks)
    graph.add_node("save", step_save)

    graph.add_edge(START, "input_checks")
    graph.add_conditional_edges("input_checks", after_input_checks, ["triage", "escalate"])
    graph.add_edge("triage", "memo_checks")
    graph.add_conditional_edges("memo_checks", after_memo_checks, ["checker", "escalate"])
    graph.add_conditional_edges("checker", after_checker, ["draft", "decline", "escalate", "debate"])
    graph.add_conditional_edges("debate", after_debate, ["draft", "decline", "escalate"])
    graph.add_edge("escalate", END)
    graph.add_edge("decline", END)
    graph.add_edge("draft", "letter_checks")
    graph.add_conditional_edges("letter_checks", after_letter_checks, ["save", "draft", "escalate"])
    graph.add_edge("save", END)

    return graph.compile()
