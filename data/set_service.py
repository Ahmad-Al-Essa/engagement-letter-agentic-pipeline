"""Switch a service on or off — a partner's action, not an agent's.

Run from the repo root:
    python3 data/set_service.py AUD-001 off     # pause: not accepting new engagements
    python3 data/set_service.py AUD-001 on      # accept again
    python3 data/set_service.py --list          # show every service and whether it is on

A paused service still shows up in the catalog search, labelled PAUSED, so the
pipeline can say "we offer this, but not right now" instead of quietly offering
the nearest service that is still on. The agents have no tool that can do this.
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "sbp.db"


def list_services(conn):
    rows = conn.execute("SELECT code, name, active FROM services ORDER BY code").fetchall()
    for code, name, active in rows:
        state = "on" if active == 1 else "PAUSED"
        print(f"{code:9} {state:7} {name}")


def set_service(conn, code, state):
    active = 1 if state == "on" else 0
    cursor = conn.execute("UPDATE services SET active = ? WHERE code = ?", (active, code))
    conn.commit()
    if cursor.rowcount == 0:
        print(f"No service with code {code}.")
    else:
        print(f"{code} is now {'on' if active == 1 else 'PAUSED'}.")


def main():
    conn = sqlite3.connect(DB_PATH)
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        list_services(conn)
    elif len(sys.argv) == 3 and sys.argv[2] in ("on", "off"):
        set_service(conn, sys.argv[1].upper(), sys.argv[2])
    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main()
