"""How the catalog search's model and minimum score were chosen.

Run from the repo root (needs the three embedding models pulled):
    python3 eval/calibrate_search.py

Each test need below is labelled by hand:
    YES   — a service in the catalog clearly fits
    NO    — the firm does not do this work (legal, design, recruiting)
    MAYBE — arguably fits, arguably not (kept out of the threshold choice)

For each embedding model, the script scores every need against all 45 services
and reports the weakest YES and the strongest NO. A minimum score can only work
if the weakest YES is still above the strongest NO ("separated").

Limits, to state in the report: 14 needs, written by us, from our own three
intake records plus a few probes. The threshold is fitted to these, so the real
test is the evaluation on the full pipeline.
"""

import math
import sqlite3
from pathlib import Path

from langchain_ollama import OllamaEmbeddings

DB_PATH = Path(__file__).parent.parent / "data" / "sbp.db"

TEST_NEEDS = [
    ("YES", "audited financial statements required by our bank to renew our credit facility", "AUD-001"),
    ("YES", "prepare and file our zakat declaration", "TAX-002"),
    ("YES", "find out what our family business is worth", "DEAL-002"),
    ("YES", "sell one of our business divisions", "DEAL-004"),
    ("YES", "our books are kept by one accountant in his own way, nothing is standardised", "FIN-002/003/004"),
    ("YES", "we have no board of directors and no written policies", "MC-004/006"),
    ("YES", "help us prepare our company for sale to a buyer", "DEAL-004"),
    ("MAYBE", "investigate whether a former supplier overbilled us over two years", "FIN-001"),
    ("MAYBE", "bring in an outside investor", "DEAL-004"),
    ("NO", "represent us before the commercial court in a dispute with a supplier", "none"),
    ("NO", "review and redraft our supplier contracts", "none"),
    ("NO", "advise us on our legal position before we file a lawsuit", "none"),
    ("NO", "design a new logo and website for our brand", "none"),
    ("NO", "recruit a new sales manager for us", "none"),
]

# Each model expects its own wording around documents and queries.
MODELS = {
    "nomic-embed-text": {
        "document": "search_document: {name}. {description}",
        "query": "search_query: {need}",
    },
    "embeddinggemma": {
        "document": "title: {name} | text: {description}",
        "query": "task: search result | query: {need}",
    },
    "qwen3-embedding:0.6b": {
        "document": "{name}. {description}",
        "query": "Instruct: Given a client's description of what they need, "
                 "retrieve the professional service that fulfils it\nQuery: {need}",
    },
}


def similarity(a, b):
    dot = 0.0
    size_a = 0.0
    size_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        size_a += x * x
        size_b += y * y
    return dot / math.sqrt(size_a * size_b)


def main():
    conn = sqlite3.connect(DB_PATH)
    services = conn.execute("SELECT code, name, description FROM services").fetchall()
    conn.close()

    for model, wording in MODELS.items():
        embedder = OllamaEmbeddings(model=model)
        texts = []
        for code, name, description in services:
            texts.append(wording["document"].format(name=name, description=description))
        vectors = embedder.embed_documents(texts)

        print(f"\n=== {model}")
        yes_scores = []
        no_scores = []
        for label, need, expected in TEST_NEEDS:
            need_vector = embedder.embed_query(wording["query"].format(need=need))
            scored = []
            for (code, name, description), vector in zip(services, vectors):
                scored.append((similarity(need_vector, vector), code))
            scored.sort(reverse=True)
            top_score, top_code = scored[0]
            second_score, second_code = scored[1]

            if label == "YES":
                yes_scores.append(top_score)
            if label == "NO":
                no_scores.append(top_score)
            print(f"  {label:5} top {top_code:8} {top_score:.3f}  gap to 2nd {top_score - second_score:.3f}"
                  f"  (expected {expected:15}) {need[:50]}")

        weakest_yes = min(yes_scores)
        strongest_no = max(no_scores)
        verdict = "separated" if weakest_yes > strongest_no else "OVERLAP"
        print(f"  -> weakest YES {weakest_yes:.3f} | strongest NO {strongest_no:.3f} | "
              f"margin {weakest_yes - strongest_no:+.3f} | {verdict}")
        if weakest_yes > strongest_no:
            print(f"  -> a minimum score halfway between: {(weakest_yes + strongest_no) / 2:.3f}")


if __name__ == "__main__":
    main()
