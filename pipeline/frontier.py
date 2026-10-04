"""Optional: run the checker on Claude (a frontier model, through the Anthropic API) instead of the local model.

OFF by default. The whole system runs locally without any key. To switch the
checker to Claude for one run:

    1. Copy .env.example to .env and put your Anthropic API key in it (never commit .env).
    2. CHECKER=frontier python3 run.py intakes/record_1_al_rawdah.txt

Only the checker's roles change (listing needs, deciding, the debate's checker
side, reading the letter). Triage and the drafter stay on the local model, and
so does the letter-quality judge used in the evaluation, so that results with
and without Claude can be compared fairly.

Model: Claude Opus 5.5 (claude-opus-5-5), $4 per million input tokens and $20
per million output tokens. A full run of the checker uses a few thousand
tokens, so cents per intake.

If Claude declines to answer (stop reason "refusal"), the call raises an
error and the run stops: the case does not continue on a missing answer.
"""

from anthropic import AsyncAnthropic
from langchain_core.messages import SystemMessage

FRONTIER_MODEL = "claude-opus-5-5"


class ClaudeStructured:
    """Answers in a fixed shape (a Pydantic model), like with_structured_output on a local model."""

    def __init__(self, schema):
        self.schema = schema
        self.client = AsyncAnthropic()     # reads ANTHROPIC_API_KEY from the environment (.env)

    async def ainvoke(self, messages):
        # The pipeline's messages are one system prompt plus one or more user messages.
        system_parts = []
        user_parts = []
        for message in messages:
            if isinstance(message, SystemMessage):
                system_parts.append(message.content)
            else:
                user_parts.append(message.content)

        response = await self.client.messages.parse(
            model=FRONTIER_MODEL,
            max_tokens=16000,
            system="\n\n".join(system_parts),
            messages=[{"role": "user", "content": "\n\n".join(user_parts)}],
            output_format=self.schema,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to answer this request; the case needs a partner.")
        return response.parsed_output


class ClaudeChat:
    """Stands in for a local chat model in the checker's roles."""

    def with_structured_output(self, schema, method=None):
        return ClaudeStructured(schema)
