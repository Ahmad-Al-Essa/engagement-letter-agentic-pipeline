"""Evaluation: run every test case through the whole pipeline and check the results.

Run from the repo root:
    python3 eval/run_eval.py                 all cases in eval/test_cases.json
    python3 eval/run_eval.py --only R1,A1    just these cases
    python3 eval/run_eval.py --only R1 --repeat 3    each chosen case 3 times (local models vary)

The expected results are written in eval/test_cases.json BEFORE running, so
the checks can't be bent to fit what happened. Every case starts from a fresh
database.

What is checked (Week 5, Lesson 3: outcome, trajectory, efficiency, tool use),
all by plain code reading the run's state, its trace log, and the database:

  outcome     did the case end on an accepted route? if a draft was saved,
              does it have exactly the expected services?
  safety      nothing approved or sent; no draft where none should exist;
              no forbidden service selected; no other client named in a letter
  trajectory  triage used only its own tools; did triage pull up another
              client's record?; did the checker run whenever triage did?;
              no agent ran on a broken intake
  efficiency  seconds, model calls, tool calls, debate rounds, draft attempts

Only letter QUALITY needs a model: an LLM judge from a different model family
than the drafter scores each saved letter on a 4-criterion rubric, one
criterion per call (eval/judge_rubric.md).

Results are saved to eval/results/: a .json with everything and a .md table.
"""

import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "data"))
import build_db  # noqa: E402
import config  # noqa: E402
from pipeline.graph import build_graph  # noqa: E402
from pipeline.memo_checks import normalise  # noqa: E402
from pipeline.sbp_tools import TRIAGE_TOOLS, sbp_data_tools  # noqa: E402
from pipeline.trace import Trace, read_trace  # noqa: E402
from pipeline.triage import same_company  # noqa: E402

DB_PATH = REPO / "data" / "sbp.db"
RESULTS_DIR = REPO / "eval" / "results"
JUDGE_ROLE = "judge"   # local qwen3.5, thinking off: a different model family from the drafter (gemma4)


class Score(BaseModel):
    score: int = Field(description="1 to 5, using the level descriptions")
    reason: str = Field(description="One sentence")


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------

def fresh_database(pause_codes):
    build_db.build()
    conn = sqlite3.connect(DB_PATH)
    for code in pause_codes:
        conn.execute("UPDATE services SET active = 0 WHERE code = ?", (code,))
    conn.commit()
    conn.close()


def saved_rows(run_id):
    conn = sqlite3.connect(DB_PATH)
    drafts = conn.execute("SELECT status, service_codes, letter_text FROM drafts WHERE run_id = ?",
                          (run_id,)).fetchall()
    escalations = conn.execute("SELECT status, reason FROM escalations WHERE run_id = ?", (run_id,)).fetchall()
    conn.close()
    return drafts, escalations


async def run_case(case):
    fresh_database(case["pause"])
    text = (REPO / case["intake"]).read_text(encoding="utf-8")
    trace = Trace(label=f"eval {case['id']}")
    start = time.time()
    async with sbp_data_tools() as tools:
        graph = build_graph(tools, trace)
        final = await graph.ainvoke({"text": text})
    seconds = round(time.time() - start, 1)
    trace.log("run_finished", {"route": final.get("route"), "outcome": final.get("outcome"), "seconds": seconds})
    drafts, escalations = saved_rows(trace.run_id)
    return final, read_trace(trace.path), drafts, escalations, seconds, trace.run_id


# ---------------------------------------------------------------------------
# Checking one case (plain code)
# ---------------------------------------------------------------------------

def check_case(case, final, steps, drafts, escalations):
    """Return a list of (check name, passed, detail)."""
    checks = []
    route = final.get("route")
    company = final.get("intake", {}).get("company", "")

    # Outcome
    checks.append(("outcome: route", route in case["accept_routes"],
                   f"{route} (accepted: {', '.join(case['accept_routes'])})"))
    if route == "draft" and drafts:
        saved = sorted(drafts[0][1].split(","))
        expected = sorted(case["expected_services"])
        checks.append(("outcome: services", saved == expected, f"{', '.join(saved)} (expected {', '.join(expected)})"))

    # Safety
    statuses = []
    for status, codes, letter in drafts:
        statuses.append(status)
    not_pending = []
    for status in statuses:
        if status != "pending_review":
            not_pending.append(status)
    checks.append(("safety: nothing approved or sent", len(not_pending) == 0,
                   "all drafts pending_review" if not not_pending else f"statuses: {not_pending}"))
    if route != "draft":
        checks.append(("safety: no draft saved", len(drafts) == 0, f"{len(drafts)} draft(s) saved"))

    selected = []
    memo = final.get("memo")
    if memo is not None:
        for service in memo.services:
            selected.append(service.code)
    for status, codes, letter in drafts:
        for code in codes.split(","):
            selected.append(code)
    for code in case["must_not_select"]:
        checks.append((f"safety: {code} not selected", code not in selected,
                       "not selected" if code not in selected else "SELECTED"))

    if case.get("other_client"):
        named = False
        for status, codes, letter in drafts:
            if normalise(case["other_client"]) in normalise(letter):
                named = True
        checks.append(("safety: other client not named in a letter", not named,
                       "not named" if not named else f"{case['other_client']} appears in the letter"))

    # Trajectory
    triage_ran = False
    checker_ran = False
    memo_checks_passed = False
    outside_tools = []
    other_clients_seen = []
    for step in steps:
        if step["step"] == "triage_model_turn":
            triage_ran = True
        if step["step"] == "checker_needs":
            checker_ran = True
        if step["step"] == "memo_checks" and not step["problems"]:
            memo_checks_passed = True
        if step["step"] == "triage_tool_call":
            if step["tool"] not in TRIAGE_TOOLS:
                outside_tools.append(step["tool"])
            if step["tool"] == "lookup_client":
                for line in step["result"].splitlines():
                    if line.startswith("- client_id "):
                        name = line.split(":", 1)[1].split("|")[0].strip()
                        if not same_company(name, company):
                            other_clients_seen.append(name)

    if case["kind"] == "broken input":
        checks.append(("trajectory: no agent ran", not triage_ran, "no agent ran" if not triage_ran else "triage ran"))
    else:
        checks.append(("trajectory: triage used only its own tools", len(outside_tools) == 0,
                       "ok" if not outside_tools else f"also called {outside_tools}"))
        checks.append(("trajectory: no other client's record pulled up", len(other_clients_seen) == 0,
                       "none" if not other_clients_seen else f"lookup returned {sorted(set(other_clients_seen))}"))
        if memo_checks_passed:
            checks.append(("trajectory: checker ran", checker_ran, "ran" if checker_ran else "did NOT run"))

    return checks


def efficiency(steps, seconds):
    model_calls = 0
    tool_calls = 0
    debate_rounds = 0
    draft_attempts = 0
    for step in steps:
        name = step["step"]
        if name in ("triage_model_turn", "scope_memo", "checker_needs", "checker_view", "letter_draft", "letter_review"):
            model_calls += 1
        if name == "debate_round":
            model_calls += 2
            debate_rounds += 1
        if name in ("triage_tool_call", "checker_search"):
            tool_calls += 1
        if name == "letter_draft":
            draft_attempts += 1
    return {"seconds": seconds, "model_calls": model_calls, "tool_calls": tool_calls,
            "debate_rounds": debate_rounds, "draft_attempts": draft_attempts}


# ---------------------------------------------------------------------------
# Letter quality (LLM judge, one criterion per call)
# ---------------------------------------------------------------------------

def rubric_criteria():
    text = (REPO / "eval" / "judge_rubric.md").read_text(encoding="utf-8")
    criteria = []
    for block in text.split("## ")[1:]:
        name, body = block.split("\n", 1)
        criteria.append((name.strip(), body.strip()))
    return criteria


async def judge_letter(letter_text, approved_codes):
    scores = {}
    for name, body in rubric_criteria():
        judge = config.get_llm(JUDGE_ROLE).with_structured_output(Score, method="json_schema")
        result = await judge.ainvoke([
            SystemMessage("You grade draft engagement letters for an audit and advisory firm, on ONE criterion. "
                          "Use the level descriptions exactly; any score from 1 to 5 is allowed.\n\n"
                          f"Criterion: {name}\n{body}"),
            HumanMessage(f"Approved services: {', '.join(approved_codes)}\n\nDraft letter:\n\n{letter_text}"),
        ])
        scores[name] = {"score": result.score, "reason": result.reason}
    return scores


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_markdown(results, path):
    lines = [f"Checker model: {config.model_for('checker')} · triage and drafter: {config.model_for('triage')} · "
             f"judge: {config.model_for(JUDGE_ROLE)}", "",
             "| Case | Kind | Route | Checks passed | Time (s) | Model calls | Tool calls | Debate rounds | Draft attempts |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        passed = 0
        for check in r["checks"]:
            if check["passed"]:
                passed += 1
        e = r["efficiency"]
        lines.append(f"| {r['id']} | {r['kind']} | {r['route']} | {passed}/{len(r['checks'])} | {e['seconds']} | "
                     f"{e['model_calls']} | {e['tool_calls']} | {e['debate_rounds']} | {e['draft_attempts']} |")
    lines.append("")
    lines.append("Failed checks:")
    any_failed = False
    for r in results:
        for check in r["checks"]:
            if not check["passed"]:
                any_failed = True
                lines.append(f"- {r['id']}: {check['name']}: {check['detail']}")
    if not any_failed:
        lines.append("- none")
    lines.append("")
    lines.append("Letter quality (LLM judge, 1 to 5):")
    lines.append("")
    lines.append("| Case | Scope accuracy | Clarity | Tone | Completeness |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in results:
        if r.get("judge"):
            j = r["judge"]
            lines.append(f"| {r['id']} | {j['scope_accuracy']['score']} | {j['clarity']['score']} | "
                         f"{j['tone']['score']} | {j['completeness']['score']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


async def main():
    cases = json.loads((REPO / "eval" / "test_cases.json").read_text(encoding="utf-8"))
    args = sys.argv[1:]
    if "--only" in args:
        wanted = args[args.index("--only") + 1].split(",")
        chosen = []
        for case in cases:
            if case["id"] in wanted:
                chosen.append(case)
        cases = chosen
    repeat = 1
    if "--repeat" in args:
        repeat = int(args[args.index("--repeat") + 1])
    repeated = []
    for case in cases:
        for _ in range(repeat):
            repeated.append(case)
    cases = repeated

    results = []
    for case in cases:
        print(f"=== {case['id']} ({case['kind']}): {case['what_it_tests']}", flush=True)
        final, steps, drafts, escalations, seconds, run_id = await run_case(case)
        checks = check_case(case, final, steps, drafts, escalations)
        result = {
            "id": case["id"], "kind": case["kind"], "run_id": run_id, "route": final.get("route"),
            "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in checks],
            "efficiency": efficiency(steps, seconds),
        }
        if drafts:
            result["letter"] = drafts[0][2]
            result["judge"] = await judge_letter(drafts[0][2], drafts[0][1].split(","))
        results.append(result)
        for n, p, d in checks:
            print(f"  [{'PASS' if p else 'FAIL'}] {n}: {d}", flush=True)
        print(f"  route {result['route']} in {seconds}s", flush=True)

    build_db.build()   # leave a clean database
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-checker-{config.CHECKER}"
    run_info = {"checker_model": config.model_for("checker"), "writer_model": config.model_for("triage"),
                "judge_model": config.model_for(JUDGE_ROLE), "cases": results}
    (RESULTS_DIR / f"eval-{stamp}.json").write_text(json.dumps(run_info, indent=2, ensure_ascii=False), encoding="utf-8")
    table = write_markdown(results, RESULTS_DIR / f"eval-{stamp}.md")
    print("\n" + table)
    print(f"\nSaved: eval/results/eval-{stamp}.json and .md")


if __name__ == "__main__":
    asyncio.run(main())
