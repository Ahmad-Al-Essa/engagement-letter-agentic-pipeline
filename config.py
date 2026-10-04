"""Which model runs each role, and whether it thinks before answering.

This is the only place models are chosen. Every agent asks for its model
with get_llm("<role>"), so changing a line here changes that agent.

Two model families on purpose:
  - gemma4:12b  (Google)  writes: triage and drafting
  - qwen3.5:9b  (Alibaba) judges: the checker
A checker from a different family is harder to fool with the same mistake
the writer made (Week 5, "Method 2").

Smaller machine? Run every role on one model:
    SINGLE_MODEL=gemma4:12b python3 run.py
"""

import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

load_dotenv()   # reads .env if it exists (only needed for the optional Claude checker)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# How much text a model can hold at once. Ollama otherwise loads the model's
# maximum (262,144 for gemma4), which wastes memory; our longest run needs far less.
CONTEXT_TOKENS = 32768

WRITER_MODEL = "gemma4:12b"
JUDGE_MODEL = "qwen3.5:9b"

# Turns text into "meaning fingerprints" for the catalog search (not a chat model).
# Chosen by measurement: see eval/calibrate_search.py.
EMBED_MODEL = "embeddinggemma"

# If set, every role uses this one model instead of the table below.
SINGLE_MODEL = os.getenv("SINGLE_MODEL")

# Optional: CHECKER=frontier runs the checker's roles on Claude (see pipeline/frontier.py).
CHECKER = os.getenv("CHECKER", "local")
CHECKER_ROLES = ["checker", "checker_write", "letter_checker"]

# think = True  -> the model reasons before answering (slower, better judgement)
# think = False -> answers directly (fast; fine for writing from a fixed memo)
# Measured 2026-10-03: with thinking ON, writing the scope memo got stuck in a
# loop ("Wait, I'll check the verdict again...") and never answered. Triage
# therefore thinks while it investigates (tool calls), then writes the memo
# with thinking OFF: by then it is writing down decisions already made.
ROLES = {
    "triage":  {"model": WRITER_MODEL, "think": True,  "temperature": 0.0},
    "triage_memo": {"model": WRITER_MODEL, "think": False, "temperature": 0.0},
    "checker": {"model": JUDGE_MODEL,  "think": True,  "temperature": 0.0},
    "checker_write": {"model": JUDGE_MODEL, "think": False, "temperature": 0.0},
    "drafter": {"model": WRITER_MODEL, "think": False, "temperature": 0.3},
    "letter_checker": {"model": JUDGE_MODEL, "think": False, "temperature": 0.0},
    # The evaluation's letter-quality judge. Always local, so that runs with and
    # without the Claude checker are scored by the same judge.
    "judge": {"model": JUDGE_MODEL, "think": False, "temperature": 0.0},
}


def model_for(role):
    """The model name a role will use (after the SINGLE_MODEL and CHECKER overrides)."""
    if CHECKER == "frontier" and role in CHECKER_ROLES:
        from pipeline.frontier import FRONTIER_MODEL
        return FRONTIER_MODEL
    if SINGLE_MODEL:
        return SINGLE_MODEL
    return ROLES[role]["model"]


def get_llm(role):
    """Return the chat model for one role, with its thinking switch set."""
    if CHECKER == "frontier" and role in CHECKER_ROLES:
        from pipeline.frontier import ClaudeChat
        return ClaudeChat()
    settings = ROLES[role]
    return ChatOllama(
        model=model_for(role),
        base_url=OLLAMA_BASE_URL,
        reasoning=settings["think"],      # Ollama's own on/off switch for thinking
        temperature=settings["temperature"],
        num_ctx=CONTEXT_TOKENS,
    )
