"""Run the whole pipeline on one intake.

    python3 run.py intakes/record_1_al_rawdah.txt

Show the pipeline's graph (Mermaid text; paste into Obsidian or mermaid.live):

    python3 run.py --graph
"""

import asyncio
import sys
import time
from pathlib import Path

from pipeline.checker import print_check
from pipeline.graph import build_graph
from pipeline.sbp_tools import sbp_data_tools
from pipeline.trace import Trace
from pipeline.triage import print_memo


async def run(intake_path):
    text = Path(intake_path).read_text(encoding="utf-8")
    trace = Trace(label=intake_path)
    start = time.time()

    async with sbp_data_tools() as tools:
        graph = build_graph(tools, trace)
        final = await graph.ainvoke({"text": text})

    if final.get("memo") is not None:
        print_memo(final["memo"], final["owner"], final.get("memo_problems", []))
    if final.get("view") is not None:
        print_check(final["view"], final["agrees"], final["differences"])
    if final.get("debate_rounds"):
        result = "they AGREED" if final["agrees"] else "they still DISAGREE"
        print(f"\nDEBATE: {final['debate_rounds']} round(s); {result}. (The memo and view above are after the debate.)")

    if final.get("letter_text") and final["route"] == "draft":
        print("\n" + "=" * 70)
        print(f"DRAFT LETTER (attempt {final['attempts']}):\n")
        print(final["letter_text"])

    print("\n" + "=" * 70)
    print(f"ROUTE: {final['route']}")
    print(f"OUTCOME: {final['outcome']}")
    print(f"Run {trace.run_id} took {time.time() - start:.0f}s. Trace log: {trace.path}")
    trace.log("run_finished", {"route": final["route"], "outcome": final["outcome"],
                               "seconds": round(time.time() - start, 1)})


def show_graph():
    graph = build_graph(tools=None, trace=None)
    print(graph.get_graph().draw_mermaid())


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--graph":
        show_graph()
    elif len(sys.argv) == 2:
        asyncio.run(run(sys.argv[1]))
    else:
        print(__doc__)
