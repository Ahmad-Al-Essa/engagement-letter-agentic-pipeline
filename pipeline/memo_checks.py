"""Code checks on triage's scope memo, before anything acts on it.

These are rules plain code can check exactly, so they don't depend on any
model behaving well:

  1. The run found exactly one owner: the client or prospect named in the
     intake (found by code in triage.find_owner, never written by the model).
  2. The verdict matches the memo's content (e.g. "in_scope" with no services
     is a contradiction).
  3. Every selected service is real and not paused (asked of the MCP server).
  4. No quote, no service: every client_quote must actually appear in the
     client's request. A service backed by words the client never wrote fails.

What code can't check: whether a quote really *means* that service. That
judgement belongs to the checker agent (step 5).
"""

from pipeline.sbp_tools import call_tool

MIN_QUOTE_WORDS = 2   # one word ("audit") proves little; "audited statements" is a real quote.
                      # (Was 3: that wrongly rejected the checker's correct quote "audited statements".)


def normalise(text):
    """Lower-case, turn punctuation into spaces, and collapse spaces, so a quote
    still matches if the model changed a line break or a curly quotation mark."""
    characters = []
    for ch in text.lower():
        if ch.isalnum():
            characters.append(ch)
        else:
            characters.append(" ")
    return " ".join("".join(characters).split())


def quote_found(quote, request):
    """True if the quote really appears in the client's request.

    A quote may skip words with "..." or "…": then every piece between the
    dots must appear in the request.
    """
    request_words = normalise(request)
    pieces = quote.replace("…", "...").split("...")

    words_matched = 0
    for piece in pieces:
        piece_words = normalise(piece)
        if piece_words == "":
            continue
        if piece_words not in request_words:
            return False
        words_matched += len(piece_words.split())

    return words_matched >= MIN_QUOTE_WORDS


async def check_memo(memo, intake, tools, owner):
    """Return a list of problems with the memo (an empty list means it passed)."""
    problems = []
    request = intake["request"]
    client_id, prospect_id = owner

    # 1. Exactly one owner: the company named in the intake.
    if client_id is None and prospect_id is None:
        problems.append(f"Could not tell which client or prospect this is: no exact match for "
                        f"'{intake['company']}' was found or recorded.")

    # 2. The verdict matches the content.
    if memo.verdict == "in_scope" and len(memo.services) == 0:
        problems.append("Verdict is in_scope but no service was selected.")
    if memo.verdict == "in_scope" and (len(memo.not_offered) > 0 or len(memo.paused) > 0):
        problems.append("Verdict is in_scope but some needs are not offered or paused; that is unclear.")
    if memo.verdict == "out_of_scope" and len(memo.services) > 0:
        problems.append("Verdict is out_of_scope but services were selected.")

    # 3 and 4. Every selected service: real, on, and backed by the client's words.
    for service in memo.services:
        details = await call_tool(tools, "get_service", {"code": service.code})
        if details.startswith("Not in the catalog"):
            problems.append(f"{service.code} is not in the catalog.")
        elif "PAUSED" in details:
            problems.append(f"{service.code} is paused but was selected.")
        if not quote_found(service.client_quote, request):
            problems.append(f"{service.code}: the quote \"{service.client_quote}\" is not in the client's "
                            f"request (no quote, no service).")

    # Quotes for not-offered and paused needs must be real too.
    for item in memo.not_offered:
        if not quote_found(item.client_quote, request):
            problems.append(f"Not-offered need \"{item.need}\": the quote is not in the client's request.")
    for item in memo.paused:
        if not quote_found(item.client_quote, request):
            problems.append(f"Paused {item.code}: the quote is not in the client's request.")

    return problems
