"""Check that everything the pipeline needs is in place, before running it.

Run from the repo root:
    python3 check_setup.py

It checks, in order:
  1. Ollama is running.
  2. Every model in config.py has been pulled.
  3. The database exists.
  4. Each role answers, with thinking on or off as config.py says.
  5. Each role can return a reply that matches a fixed schema (the pipeline
     routes on these replies, so they must always parse).
"""

import json
import time
import urllib.request
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

import config

DB_PATH = Path(__file__).parent / "data" / "sbp.db"

TEST_QUESTION = (
    "A company asks an audit and advisory firm for a legal opinion on a contract dispute. "
    "Is this in scope for the firm? Answer in one or two sentences."
)


class TestVerdict(BaseModel):
    verdict: Literal["in_scope", "out_of_scope", "unclear"]
    reason: str


def ollama_models():
    """Names of the models Ollama has pulled, or None if Ollama isn't running."""
    try:
        with urllib.request.urlopen(config.OLLAMA_BASE_URL + "/api/tags", timeout=5) as resp:
            data = json.load(resp)
    except OSError:
        return None
    names = []
    for m in data["models"]:
        names.append(m["name"])
    return names


def main():
    ok = True

    # 1. Ollama running?
    pulled = ollama_models()
    if pulled is None:
        print(f"[FAIL] Ollama is not reachable at {config.OLLAMA_BASE_URL}. Start the Ollama app.")
        return
    print(f"[ OK ] Ollama is running at {config.OLLAMA_BASE_URL}")

    # 2. Models pulled?
    needed = [config.EMBED_MODEL]
    for role in config.ROLES:
        name = config.model_for(role)
        if name not in needed:
            needed.append(name)
    for name in needed:
        if name in pulled or name + ":latest" in pulled:
            print(f"[ OK ] model {name} is pulled")
        else:
            print(f"[FAIL] model {name} is missing. Run:  ollama pull {name}")
            ok = False
    if not ok:
        return

    # 3. Database built?
    if DB_PATH.exists():
        print(f"[ OK ] database found at {DB_PATH}")
    else:
        print("[FAIL] database missing. Run:  python3 data/build_db.py")
        ok = False

    # 4 and 5. Each role: a plain answer, then a schema-shaped answer.
    for role in config.ROLES:
        llm = config.get_llm(role)
        think = config.ROLES[role]["think"]
        print(f"\n--- role: {role}  (model {config.model_for(role)}, thinking {'on' if think else 'off'})")

        start = time.time()
        reply = llm.invoke(TEST_QUESTION)
        seconds = time.time() - start
        thinking_text = reply.additional_kwargs.get("reasoning_content", "") or ""
        print(f"  plain answer in {seconds:.1f}s, thinking: {len(thinking_text)} characters")
        print(f"  answer: {reply.content.strip()[:200]}")
        if think and not thinking_text:
            print("  [WARN] thinking is ON in config.py but the model returned no thinking")
        if not think and thinking_text:
            print("  [WARN] thinking is OFF in config.py but the model still thought")

        start = time.time()
        structured = llm.with_structured_output(TestVerdict, method="json_schema")
        result = structured.invoke(TEST_QUESTION)
        seconds = time.time() - start
        print(f"  schema answer in {seconds:.1f}s: verdict={result.verdict!r}")

    print("\nAll checks passed." if ok else "\nSome checks failed (see above).")


if __name__ == "__main__":
    main()
