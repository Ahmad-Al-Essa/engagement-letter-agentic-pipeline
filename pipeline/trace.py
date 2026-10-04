"""The trace log: a written record of everything one pipeline run did.

Each run gets an id (its start time, e.g. 20261003-141205) and one file,
runs/<run_id>.jsonl. Every step adds one line to that file: the time, the
step's name, and what happened (inputs, tool calls, results, decisions).

The file is used three ways:
  - evaluation: "did the checker run? which tools were called?" is read from here
  - debugging: when an agent does something odd, the steps show where
  - the demo: a replay script prints a saved run back, step by step
"""

import json
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path(__file__).parent.parent / "runs"


class Trace:
    def __init__(self, label=""):
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        RUNS_DIR.mkdir(exist_ok=True)
        self.path = RUNS_DIR / f"{self.run_id}.jsonl"
        self.log("run_started", {"label": label})

    def log(self, step, data):
        """Add one line to this run's file."""
        line = {"time": datetime.now().isoformat(timespec="seconds"), "step": step}
        line.update(data)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_trace(path):
    """Read a saved run back as a list of steps (for evaluation and replay)."""
    steps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            steps.append(json.loads(line))
    return steps
