"""The ReAct loop, used by triage: the one agent that chooses its own tool calls.

    think → call a tool → read the result → repeat, until the model stops
    asking for tools (or a safety limit is reached).

Each agent passes in only the tools it is allowed to use. If the model asks
for any other tool, it gets a refusal instead of a result.

The checker used this loop in an early version, but given the search tool
freely it reworded the same needs 22 times until something matched. Its
searches are now made by code, one per need (see checker.py).

Every model turn and every tool call is written to the run's trace log.
"""

import time

from langchain_core.messages import ToolMessage

from pipeline.sbp_tools import call_tool

MAX_TOOL_ROUNDS = 10   # a safety limit: the loop stops even if the model keeps calling tools


async def run_tool_loop(agent_name, llm, allowed_names, tools, messages, trace):
    """Run the loop for one agent.

    agent_name     the agent's name, e.g. "triage" (used in the trace log)
    llm            the agent's chat model
    allowed_names  the tools this agent may call, e.g. ["lookup_client", ...]
    tools          every tool from the MCP server, by name
    messages       the conversation so far; the loop adds to it
    trace          the run's trace log

    Returns the tool log: every call, with its arguments and result.
    """
    allowed_tools = []
    for name in allowed_names:
        allowed_tools.append(tools[name])
    llm_with_tools = llm.bind_tools(allowed_tools)

    tool_log = []
    finished = False

    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        start = time.time()
        reply = await llm_with_tools.ainvoke(messages)
        messages.append(reply)

        calls = []
        for call in reply.tool_calls:
            calls.append({"tool": call["name"], "args": call["args"]})
        trace.log(f"{agent_name}_model_turn", {
            "round": round_number,
            "seconds": round(time.time() - start, 1),
            "tool_calls": calls,
            "text": reply.content,
        })

        if not reply.tool_calls:
            finished = True     # the model asked for no more tools
            break

        for call in reply.tool_calls:
            if call["name"] in allowed_names:
                result = await call_tool(tools, call["name"], call["args"])
            else:
                result = f"The tool {call['name']} is not available to the {agent_name}."
            tool_log.append({"tool": call["name"], "args": call["args"], "result": result})
            trace.log(f"{agent_name}_tool_call", {"tool": call["name"], "args": call["args"], "result": result})
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    if not finished:
        trace.log(f"{agent_name}_round_limit", {"rounds": MAX_TOOL_ROUNDS})

    return tool_log
