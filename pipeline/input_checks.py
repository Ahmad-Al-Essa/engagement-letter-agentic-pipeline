"""Input checks: plain code that looks at an intake BEFORE any agent reads it.

An intake looks like this (see the intakes/ folder):

    Company:  Al-Rawdah Trading Company K.S.C.C.
    Contact:  Finance Manager
    Sector:   Wholesale distribution — building materials
    Size:     ~85 employees, 3 branches
    Received: 2026-09-01

    "We have been preparing our own financial statements ..."

Header lines ("Field: value") come first, then a blank line, then the
client's own words: the request.

What these checks CAN do: confirm the intake has the right shape. The
required fields are filled in, there is a real request, and nothing is
absurdly long. Code can check these exactly.

What they CANNOT do: tell whether the request hides an instruction aimed at
the agents ("ignore your rules and ..."). No input check can reliably catch
that. The later layers handle it: agents' limited tools, the checker, and
the partner's approval.

If any check fails, no agent sees the intake. The pipeline sends it to a
partner instead (fail closed: when in doubt, a human decides).

Try it:
    python3 -m pipeline.input_checks intakes/record_1_al_rawdah.txt
"""

import sys
from pathlib import Path

KNOWN_FIELDS = ["Company", "Contact", "Sector", "Size", "Received"]
REQUIRED_FIELDS = ["Company", "Contact"]   # add_prospect needs both for a new company

MAX_INTAKE_CHARS = 4000     # the whole intake, header included
MAX_REQUEST_CHARS = 3000    # the client's own words
MIN_REQUEST_WORDS = 10      # fewer words than this is not a request we can triage


def parse_intake(text):
    """Split an intake into its header fields and the client's request."""
    fields = {}
    request_lines = []
    in_header = True

    for line in text.strip().splitlines():
        if in_header:
            if line.strip() == "":
                in_header = False            # the blank line ends the header
                continue
            if ":" in line:
                name, value = line.split(":", 1)
                name = name.strip()
                if name in KNOWN_FIELDS:
                    fields[name] = value.strip()
                    continue
            in_header = False                # not a header line: the request has started
        request_lines.append(line)

    request = "\n".join(request_lines).strip()

    # Remove the quotation marks around the client's words, if present.
    if request.startswith('"') and request.endswith('"'):
        request = request[1:-1].strip()

    return fields, request


def check_intake(text):
    """Run every check on one intake.

    Returns three things:
        passed    True if every check passed
        problems  a list of plain-English reasons (empty if passed)
        intake    the parsed intake: company, contact, sector, size, received, request
    """
    if text is None or text.strip() == "":
        return False, ["The intake is empty."], {}

    problems = []

    if len(text) > MAX_INTAKE_CHARS:
        problems.append(f"The intake is {len(text)} characters long; the limit is {MAX_INTAKE_CHARS}.")

    fields, request = parse_intake(text)

    for name in REQUIRED_FIELDS:
        if fields.get(name, "") == "":
            problems.append(f"The required field '{name}' is missing or empty.")

    word_count = len(request.split())
    if word_count < MIN_REQUEST_WORDS:
        problems.append(f"The client's request has {word_count} words; at least {MIN_REQUEST_WORDS} "
                        f"are needed to triage it.")

    if len(request) > MAX_REQUEST_CHARS:
        problems.append(f"The client's request is {len(request)} characters long; "
                        f"the limit is {MAX_REQUEST_CHARS}.")

    intake = {
        "company": fields.get("Company", ""),
        "contact": fields.get("Contact", ""),
        "sector": fields.get("Sector", ""),
        "size": fields.get("Size", ""),
        "received": fields.get("Received", ""),
        "request": request,
    }

    passed = len(problems) == 0
    return passed, problems, intake


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 -m pipeline.input_checks <intake file>")
        sys.exit(1)

    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    passed, problems, intake = check_intake(text)

    if passed:
        print("PASSED: the intake can go to triage.")
    else:
        print("FAILED: the intake goes to a partner, not to an agent.")
        for problem in problems:
            print(f"  - {problem}")

    print()
    for key, value in intake.items():
        if key == "request":
            print(f"{key:9} {value[:70].replace(chr(10), ' ')}...")   # chr(10) = a line break
        else:
            print(f"{key:9} {value}")
